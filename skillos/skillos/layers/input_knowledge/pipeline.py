"""Experience Processing Pipeline: Extractor -> Normalizer -> Summarizer -> Indexer."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)

ALLOWED_SKILL_TYPES = {"atomic", "functional", "strategic"}


@dataclass
class StructuredExperience:
    unit_id: str
    source_type: str
    raw_content: str
    extracted_actions: List[str] = field(default_factory=list)
    normalized_actions: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    proposed_skill_name: Optional[str] = None
    proposed_description: Optional[str] = None
    proposed_type: Optional[str] = None
    confidence: float = 0.0
    index_keywords: List[str] = field(default_factory=list)
    index_embedding_hint: str = ""


@dataclass
class PipelineResult:
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: List[str] = field(default_factory=list)
    units: List[StructuredExperience] = field(default_factory=list)


_EXTRACT_PROMPT = """
You are the SkillOS Extractor Agent.

Input source type:
{source_type}

Raw input:
{raw_content}

Task:
- Extract reusable actions from the input.
- Each action must be one concise human-readable sentence.
- Propose a reusable skill name in snake_case.
- Estimate confidence as a number in [0, 1].

Return only valid JSON with this shape:
{{
  "actions": ["action 1", "action 2"],
  "proposed_skill_name": "snake_case_name",
  "confidence": 0.8
}}
"""


class ExtractorAgent:
    """Extract reusable action sentences from raw input."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client
        self.last_token_usage = 0

    def extract(self, raw_content: str, source_type: str) -> Dict[str, Any]:
        self.last_token_usage = 0
        if _should_skip_llm(self._llm):
            return {
                "actions": [raw_content.strip().splitlines()[0][:160] if raw_content.strip() else "Process input"],
                "proposed_skill_name": f"skill_from_{_safe_slug(source_type) or 'input'}",
                "confidence": 0.3,
            }
        prompt = _EXTRACT_PROMPT.format(
            source_type=source_type,
            raw_content=raw_content[:4000],
        )
        try:
            resp = self._llm.chat(
                [
                    Message.system("You are the SkillOS Extractor Agent. Return JSON only."),
                    Message.user(prompt),
                ]
            )
            self.last_token_usage = resp.total_tokens
            data = _parse_json(resp.content)
            if data:
                return data
        except Exception as exc:
            logger.warning("Extractor LLM failed: %s", exc)

        fallback_action = raw_content.strip().splitlines()[0][:160] if raw_content.strip() else "Process input"
        return {
            "actions": [fallback_action],
            "proposed_skill_name": f"skill_from_{_safe_slug(source_type) or 'input'}",
            "confidence": 0.3,
        }


_NORMALIZE_PROMPT = """
You are the SkillOS Normalizer Agent.

Raw actions:
{actions}

Task:
- Convert each action into a structured operation.
- Use stable keys: verb, object, condition, description.
- Keep descriptions short and useful for a future Skill definition.

Return only valid JSON with this shape:
{{
  "normalized": [
    {{
      "verb": "click",
      "object": "login button",
      "condition": "login page is open",
      "description": "Click the login button"
    }}
  ]
}}
"""


class NormalizerAgent:
    """Normalize action sentences into simple operation dictionaries."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client
        self.last_token_usage = 0

    def normalize(self, actions: List[str]) -> List[Dict[str, Any]]:
        self.last_token_usage = 0
        if not actions:
            return []
        if _should_skip_llm(self._llm):
            return _fallback_normalized_actions(actions)

        prompt = _NORMALIZE_PROMPT.format(actions=json.dumps(actions, ensure_ascii=False))
        try:
            resp = self._llm.chat(
                [
                    Message.system("You are the SkillOS Normalizer Agent. Return JSON only."),
                    Message.user(prompt),
                ]
            )
            self.last_token_usage = resp.total_tokens
            data = _parse_json(resp.content)
            normalized = data.get("normalized") if data else None
            if isinstance(normalized, list):
                return [n for n in normalized if isinstance(n, dict)]
        except Exception as exc:
            logger.warning("Normalizer LLM failed: %s", exc)

        return _fallback_normalized_actions(actions)


_SUMMARIZE_PROMPT = """
You are the SkillOS Summarizer Agent.

Normalized actions:
{normalized_actions}

Task:
- Produce a concise reusable Skill description.
- Choose exactly one skill_type: atomic, functional, or strategic.
- Provide 1-5 lowercase tags.
- Estimate confidence as a number in [0, 1].

Return only valid JSON with this shape:
{{
  "description": "Reusable skill description",
  "skill_type": "atomic",
  "tags": ["web", "form"],
  "confidence": 0.85
}}
"""


class SummarizerAgent:
    """Summarize normalized operations into a candidate Skill description."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client
        self.last_token_usage = 0

    def summarize(self, normalized_actions: List[Dict[str, Any]], proposed_name: str = "") -> Dict[str, Any]:
        self.last_token_usage = 0
        if _should_skip_llm(self._llm):
            return _fallback_summary(proposed_name)
        prompt = _SUMMARIZE_PROMPT.format(
            normalized_actions=json.dumps(normalized_actions, ensure_ascii=False)[:4000],
        )
        try:
            resp = self._llm.chat(
                [
                    Message.system("You are the SkillOS Summarizer Agent. Return JSON only."),
                    Message.user(prompt),
                ]
            )
            self.last_token_usage = resp.total_tokens
            data = _parse_json(resp.content)
            if data:
                return data
        except Exception as exc:
            logger.warning("Summarizer LLM failed: %s", exc)

        return _fallback_summary(proposed_name)


class IndexerAgent:
    """Generate lightweight search keywords and embedding hints."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def index(self, skill_name: str, description: str, tags: List[str]) -> Dict[str, Any]:
        keywords = set(_normalize_tags(tags))
        keywords.update(_keyword_tokens(description)[:10])
        keywords.update(_keyword_tokens(skill_name.replace("_", " ")))
        return {
            "keywords": sorted(keywords),
            "embedding_hint": f"{skill_name}: {description}".strip(),
        }


class ExperiencePipeline:
    """Run the four-stage experience processing pipeline."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._extractor = ExtractorAgent(llm_client)
        self._normalizer = NormalizerAgent(llm_client)
        self._summarizer = SummarizerAgent(llm_client)
        self._indexer = IndexerAgent(llm_client)

    def process(self, raw_content: str, source_type: str) -> PipelineResult:
        """Process one raw input item into a structured experience candidate."""
        import uuid

        source_type = _safe_slug(source_type) or "unknown"
        errors: List[str] = []

        try:
            extracted = self._extractor.extract(raw_content, source_type)
            actions = _string_list(extracted.get("actions"))
            proposed_name = _safe_skill_name(
                extracted.get("proposed_skill_name"),
                fallback=f"skill_from_{source_type}",
            )
            confidence = _clamp_float(extracted.get("confidence"), default=0.5)

            normalized = self._normalizer.normalize(actions)

            summary_data = self._summarizer.summarize(normalized, proposed_name)
            description = str(summary_data.get("description") or "").strip()
            if not description:
                description = f"Execute the reusable {proposed_name.replace('_', ' ')} workflow."
            skill_type = _safe_skill_type(summary_data.get("skill_type"))
            tags = _normalize_tags(summary_data.get("tags"))
            confidence = max(confidence, _clamp_float(summary_data.get("confidence"), default=0.5))

            index_data = self._indexer.index(proposed_name, description, tags)
            token_usage = (
                self._extractor.last_token_usage
                + self._normalizer.last_token_usage
                + self._summarizer.last_token_usage
            )

            unit = StructuredExperience(
                unit_id=str(uuid.uuid4()),
                source_type=source_type,
                raw_content=raw_content[:200],
                extracted_actions=actions,
                normalized_actions=normalized,
                summary=description,
                proposed_skill_name=proposed_name,
                proposed_description=description,
                proposed_type=skill_type,
                confidence=confidence,
                index_keywords=index_data.get("keywords", []),
                index_embedding_hint=index_data.get("embedding_hint", ""),
            )

            return PipelineResult(
                success=True,
                source_type=source_type,
                unit_count=1,
                token_usage=token_usage,
                units=[unit],
            )

        except Exception as exc:
            logger.error("ExperiencePipeline processing failed: %s", exc)
            errors.append(str(exc))
            return PipelineResult(
                success=False,
                source_type=source_type,
                unit_count=0,
                token_usage=0,
                errors=errors,
            )

    def process_batch(self, items: List[Dict[str, str]]) -> PipelineResult:
        """Process multiple raw input items."""
        all_units: List[StructuredExperience] = []
        errors: List[str] = []
        token_usage = 0
        for item in items:
            result = self.process(item.get("content", ""), item.get("source_type", "unknown"))
            all_units.extend(result.units)
            errors.extend(result.errors)
            token_usage += result.token_usage
        return PipelineResult(
            success=len(all_units) > 0,
            source_type="batch",
            unit_count=len(all_units),
            token_usage=token_usage,
            errors=errors,
            units=all_units,
        )


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
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


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _should_skip_llm(llm_client: LLMClient) -> bool:
    cfg = getattr(llm_client, "_cfg", None)
    api_key = str(getattr(cfg, "api_key", "") or "").strip().lower()
    return api_key in {"test", "dummy", "your_key", "<team-test-key>"} or "local-test" in api_key


def _fallback_normalized_actions(actions: List[str]) -> List[Dict[str, Any]]:
    return [
        {
            "verb": _first_word(action) or "do",
            "object": action,
            "condition": "",
            "description": action,
        }
        for action in actions
    ]


def _fallback_summary(proposed_name: str) -> Dict[str, Any]:
    readable_name = (proposed_name or "operation sequence").replace("_", " ")
    return {
        "description": f"Execute the reusable {readable_name} workflow.",
        "skill_type": "atomic",
        "tags": [],
        "confidence": 0.3,
    }


def _safe_skill_type(value: Any) -> str:
    skill_type = str(value or "").strip().lower()
    return skill_type if skill_type in ALLOWED_SKILL_TYPES else "atomic"


def _safe_skill_name(value: Any, *, fallback: str) -> str:
    candidate = _safe_slug(str(value or ""))
    if not candidate:
        candidate = _safe_slug(fallback)
    if not candidate:
        candidate = "skill_from_input"
    if not re.match(r"^[a-z]", candidate):
        candidate = f"skill_{candidate}"
    return candidate[:128]


def _safe_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_tags(value: Any) -> List[str]:
    tags = _string_list(value)
    cleaned = []
    for tag in tags:
        slug = _safe_slug(tag)
        if slug and slug not in cleaned:
            cleaned.append(slug)
    return cleaned[:8]


def _keyword_tokens(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9_]{2,}", text.lower())
        if token not in {"the", "and", "for", "with", "from", "into", "this", "that"}
    ]


def _first_word(text: str) -> str:
    match = re.search(r"[A-Za-z_]+", text)
    return match.group(0).lower() if match else ""
