"""Runtime Skill retriever.

The retriever combines repository search results with a small LLM decision step.
It chooses whether the runtime should reuse, compose, adapt, or generate skills.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill, SkillState
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


class RetrievalStrategy(str, Enum):
    REUSE = "reuse"
    COMPOSE = "compose"
    ADAPT = "adapt"
    GENERATE = "generate"


@dataclass
class RetrievalResult:
    """Result of runtime skill retrieval."""

    strategy: RetrievalStrategy
    skills: List[Skill] = field(default_factory=list)
    execution_order: List[str] = field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""
    parameter_mapping: Dict[str, Any] = field(default_factory=dict)
    needs_generation: bool = False
    generation_hint: str = ""

    @property
    def primary_skill(self) -> Optional[Skill]:
        return self.skills[0] if self.skills else None


_RETRIEVAL_PROMPT = """
Select the best SkillWiki retrieval strategy for the task.

Task:
{task_description}

Current state:
{current_state}

Available skills, ranked by search relevance:
{available_skills}

Rules:
- Return JSON only. Do not include Markdown or commentary.
- strategy must be one of: reuse, compose, adapt, generate.
- selected_skill_ids must only contain ids from the available skills list.
- Use reuse when one skill directly solves the task.
- Use compose when multiple listed skills are needed in sequence.
- Use adapt when one listed skill is close but needs parameter adaptation.
- Use generate only when the listed skills cannot reasonably solve the task.
- confidence must be a number between 0 and 1.

Return this JSON shape:
{{
  "strategy": "reuse",
  "selected_skill_ids": ["skill_id_1"],
  "execution_order": ["skill_id_1"],
  "confidence": 0.9,
  "rationale": "why this strategy was selected",
  "parameter_mapping": {{
    "skill_id_1": {{"param1": "value extracted from the task"}}
  }},
  "needs_generation": false,
  "generation_hint": ""
}}
"""


class SkillRetriever:
    """Find the most suitable Skill or Skill set for a runtime task."""

    def __init__(
        self,
        llm_client: LLMClient,
        search_engine: Any,
        max_candidates: int = 10,
    ) -> None:
        self._llm = llm_client
        self._search = search_engine
        self._max_candidates = max_candidates

    async def retrieve(
        self,
        task_description: str,
        current_state: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
    ) -> RetrievalResult:
        """Retrieve candidate Skills and choose a runtime strategy."""

        from ...layers.skill_repository.indexing import SearchQuery

        query = SearchQuery(
            text=task_description,
            domain=domain,
            state=SkillState.RELEASED,
            max_results=self._max_candidates,
        )
        search_results = await self._search.search(query)

        if not search_results:
            return RetrievalResult(
                strategy=RetrievalStrategy.GENERATE,
                confidence=0.0,
                needs_generation=True,
                generation_hint=task_description,
                rationale="No matching skill found; new skill generation is needed.",
            )

        skills_info = self._format_skills_for_prompt(search_results)
        try:
            llm_result = await self._llm_select(
                task_description, current_state or {}, skills_info
            )
        except Exception as exc:
            logger.warning("Retriever LLM failed; using fallback result: %s", exc)
            llm_result = None

        if not llm_result:
            return _fallback_reuse(search_results)

        skill_map = {r.skill.skill_id: r.skill for r in search_results}
        selected_ids = _string_list(llm_result.get("selected_skill_ids", []))
        selected_skills = [skill_map[sid] for sid in selected_ids if sid in skill_map]

        strategy_str = str(llm_result.get("strategy", "reuse"))
        try:
            strategy = RetrievalStrategy(strategy_str)
        except ValueError:
            strategy = RetrievalStrategy.REUSE

        if not selected_skills and strategy != RetrievalStrategy.GENERATE:
            return _fallback_reuse(search_results)

        execution_order = [
            skill_id
            for skill_id in _string_list(
                llm_result.get("execution_order", selected_ids)
            )
            if skill_id in skill_map
        ] or [skill.skill_id for skill in selected_skills]
        confidence = _clamp_float(llm_result.get("confidence", 0.7))
        parameter_mapping = llm_result.get("parameter_mapping", {})
        if not isinstance(parameter_mapping, dict):
            parameter_mapping = {}

        result = RetrievalResult(
            strategy=strategy,
            skills=selected_skills,
            execution_order=execution_order,
            confidence=confidence,
            rationale=str(llm_result.get("rationale", "")),
            parameter_mapping=parameter_mapping,
            needs_generation=bool(
                llm_result.get("needs_generation", strategy == RetrievalStrategy.GENERATE)
            ),
            generation_hint=str(llm_result.get("generation_hint", "")),
        )

        logger.info(
            "Skill retrieval [%s] selected %s skill(s), confidence %.2f",
            strategy.value,
            len(selected_skills),
            result.confidence,
        )
        return result

    async def retrieve_by_id(self, skill_id: str) -> Optional[Skill]:
        """Retrieve a Skill by exact id, even if search returns fuzzy matches first."""

        from ...layers.skill_repository.indexing import SearchQuery

        results = await self._search.search(
            SearchQuery(text=skill_id, max_results=self._max_candidates)
        )
        for result in results:
            if result.skill.skill_id == skill_id:
                return result.skill
        return None

    def _format_skills_for_prompt(self, search_results: List[Any]) -> str:
        lines = []
        for i, result in enumerate(search_results[:8]):
            skill = result.skill
            lines.append(
                f"{i + 1}. [{skill.skill_id}] {skill.name} "
                f"({skill.skill_type.value}, {skill.domain})\n"
                f"   description: {skill.description[:100]}\n"
                f"   success_rate: {skill.metrics.success_rate:.0%}, "
                f"usage_count: {skill.metrics.usage_count}"
            )
        return "\n".join(lines)

    async def _llm_select(
        self,
        task: str,
        state: Dict[str, Any],
        skills_info: str,
    ) -> Optional[Dict[str, Any]]:
        prompt = _RETRIEVAL_PROMPT.format(
            task_description=task,
            current_state=json.dumps(state, ensure_ascii=False)[:300],
            available_skills=skills_info,
        )
        response = self._llm.chat(
            [
                Message.system(
                    "You are the SkillWiki Retrieval Agent. Return strict JSON only."
                ),
                Message.user(prompt),
            ]
        )
        return self._extract_json(response.content)

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None


def _fallback_reuse(search_results: List[Any]) -> RetrievalResult:
    best_result = search_results[0]
    best_skill = best_result.skill
    return RetrievalResult(
        strategy=RetrievalStrategy.REUSE,
        skills=[best_skill],
        execution_order=[best_skill.skill_id],
        confidence=_clamp_float(getattr(best_result, "score", 0.0)),
        rationale="LLM selection failed; using the highest-scoring retrieved skill.",
    )


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item)]
