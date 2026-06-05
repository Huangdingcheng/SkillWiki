"""Skill 检索器 — 在运行时根据任务需求检索最合适的 Skill。

检索策略优先级：
1. Reuse（直接复用）：精确匹配已有 Released Skill
2. Compose（组合）：将多个 Skill 组合满足需求
3. Adapt（适配）：对已有 Skill 进行参数适配
4. Generate（生成）：触发新 Skill 生成流程
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ...models.skill_model import Skill, SkillState, SkillType
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
    """检索结果。"""
    strategy: RetrievalStrategy
    skills: List[Skill] = field(default_factory=list)
    execution_order: List[str] = field(default_factory=list)  # skill_id 列表
    confidence: float = 0.0
    rationale: str = ""
    parameter_mapping: Dict[str, Any] = field(default_factory=dict)
    needs_generation: bool = False
    generation_hint: str = ""

    @property
    def primary_skill(self) -> Optional[Skill]:
        return self.skills[0] if self.skills else None


_RETRIEVAL_PROMPT = """
你是 SkillOS 的 Skill Retrieval + Relevance Filtering Agent。

工作方式参考 MS-Agent / ReMe：
1. 检索结果只是候选记忆，可能有噪声。
2. 先判断任务本身需要什么能力，再判断 Skill 是否能作为辅助知识。
3. Skill 不应统治任务；如果 Skill 会改变用户目标，必须拒绝。
4. 若多个 Skill 只覆盖局部步骤，可以选择 compose 或 adapt。
5. 若没有 Skill 能覆盖关键步骤，应选择 generate，并给出通用新 Skill 方向。

## 任务描述
{task_description}

## 当前状态
{current_state}

## 可用 Skill（按相关性排序）
{available_skills}

## 决策策略
请按以下顺序判断：
1. reject drift：先拒绝会把任务带偏的 Skill。
2. reuse：只有当某 Skill 的目标、输入、输出、副作用都与任务匹配时直接复用。
3. compose：多个 Skill 能覆盖 functional/atomic 子步骤时组合。
4. adapt：Skill 的执行骨架有用，但参数、URL、路径、应用名、选择器或 prompt 过于硬编码时适配。
5. generate：没有足够覆盖时生成新 Skill，且要描述它的通用适用范围。

## 输出格式（严格 JSON）
{{
  "strategy": "reuse | compose | adapt | generate",
  "selected_skill_ids": ["skill_id_1"],
  "execution_order": ["skill_id_1"],
  "confidence": 0.9,
  "rationale": "选择理由，必须说明为什么没有发生 task drift",
  "coverage": {{
    "covers_full_task": false,
    "covered_parts": ["functional or atomic part covered"],
    "missing_parts": ["parts that still require agent generation or observation"]
  }},
  "rejected_skill_ids": [
    {{"skill_id": "skill_id_2", "reason": "why it would drift from the task"}}
  ],
  "parameter_mapping": {{
    "skill_id_1": {{
      "param1": "从任务描述、当前状态或 observation 中提取的值"
    }}
  }},
  "needs_generation": false,
  "generation_hint": "通用新 Skill 的目标、输入槽、输出槽和三层粒度"
}}

只输出 JSON，不要其他内容。
"""


class SkillRetriever:
    """运行时 Skill 检索器。

    结合语义搜索和 LLM 推理，为给定任务找到最合适的 Skill 组合。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        search_engine: Any,  # SkillSearchEngine，避免循环导入
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
        """为任务检索最合适的 Skill。

        Args:
            task_description: 任务描述（自然语言）
            current_state: 当前执行状态
            domain: 限定领域（可选）

        Returns:
            RetrievalResult，包含策略和选中的 Skill
        """
        from ...layers.skill_repository.indexing import SearchQuery

        # 1. 语义搜索候选集
        query = SearchQuery(
            text=task_description,
            domain=domain,
            state=SkillState.RELEASED,
            max_results=self._max_candidates,
        )
        search_results = await self._search.search(query)

        if not search_results:
            # 无候选，直接触发生成
            return RetrievalResult(
                strategy=RetrievalStrategy.GENERATE,
                confidence=0.0,
                needs_generation=True,
                generation_hint=task_description,
                rationale="无匹配 Skill，需要生成新 Skill",
            )

        # 2. LLM 推理选择最优策略
        skills_info = self._format_skills_for_prompt(search_results)
        llm_result = await self._llm_select(
            task_description, current_state or {}, skills_info
        )

        if not llm_result:
            # LLM 失败，降级为最高分候选
            best = search_results[0].skill
            return RetrievalResult(
                strategy=RetrievalStrategy.REUSE,
                skills=[best],
                execution_order=[best.skill_id],
                confidence=search_results[0].score,
                rationale="LLM 不可用，使用最高相关性 Skill",
            )

        # 3. 构建检索结果
        skill_map = {r.skill.skill_id: r.skill for r in search_results}
        selected_ids = llm_result.get("selected_skill_ids", [])
        selected_skills = [skill_map[sid] for sid in selected_ids if sid in skill_map]

        strategy_str = llm_result.get("strategy", "reuse")
        try:
            strategy = RetrievalStrategy(strategy_str)
        except ValueError:
            strategy = RetrievalStrategy.REUSE

        result = RetrievalResult(
            strategy=strategy,
            skills=selected_skills,
            execution_order=llm_result.get("execution_order", selected_ids),
            confidence=llm_result.get("confidence", 0.7),
            rationale=llm_result.get("rationale", ""),
            parameter_mapping=llm_result.get("parameter_mapping", {}),
            needs_generation=llm_result.get("needs_generation", False),
            generation_hint=llm_result.get("generation_hint", ""),
        )

        logger.info(
            f"Skill 检索: [{strategy.value}] "
            f"选中 {len(selected_skills)} 个 Skill, "
            f"置信度={result.confidence:.2f}"
        )
        return result

    async def retrieve_by_id(self, skill_id: str) -> Optional[Skill]:
        """按 ID 直接检索（用于已知 Skill 的执行）。"""
        from ...layers.skill_repository.indexing import SearchQuery
        results = await self._search.search(SearchQuery(text=skill_id, max_results=1))
        if results and results[0].skill.skill_id == skill_id:
            return results[0].skill
        return None

    def _format_skills_for_prompt(self, search_results: List[Any]) -> str:
        lines = []
        for i, r in enumerate(search_results[:8]):
            s = r.skill
            lines.append(
                f"{i+1}. [{s.skill_id}] {s.name} ({s.skill_type.value}, {s.domain})\n"
                f"   描述: {s.description[:100]}\n"
                f"   成功率: {s.metrics.success_rate:.0%}, 使用: {s.metrics.usage_count}次"
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
        response = self._llm.chat([
            Message.system(
                "你是 SkillOS 的 Skill 检索与相关性过滤专家。"
                "用户任务永远优先于检索结果；严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])
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
