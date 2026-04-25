"""Experience Processing Pipeline — Extractor/Normalizer/Summarizer/Indexer 串联。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceUnit
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

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


# ── Extractor Agent ───────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """
你是 SkillOS 的 Extractor Agent，从原始输入中提取操作动作序列。

## 输入类型
{source_type}

## 原始内容
{raw_content}

## 要求
识别所有可复用的操作动作，每个动作用一句话描述。

## 输出格式（严格 JSON）
{{
  "actions": ["动作1", "动作2", "动作3"],
  "proposed_skill_name": "snake_case_name",
  "confidence": 0.8
}}

只输出 JSON。
"""


class ExtractorAgent:
    """从原始输入中提取操作动作序列。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def extract(self, raw_content: str, source_type: str) -> Dict[str, Any]:
        prompt = _EXTRACT_PROMPT.format(
            source_type=source_type,
            raw_content=raw_content[:800],
        )
        try:
            resp = self._llm.chat([
                Message.system("你是 SkillOS Extractor Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = _parse_json(resp.content)
            if data:
                return data
        except Exception as exc:
            logger.warning(f"Extractor LLM 失败: {exc}")
        return {"actions": [raw_content[:100]], "proposed_skill_name": "extracted_skill", "confidence": 0.3}


# ── Normalizer Agent ──────────────────────────────────────────────────────────

_NORMALIZE_PROMPT = """
你是 SkillOS 的 Normalizer Agent，将提取的动作规范化为标准格式。

## 原始动作列表
{actions}

## 要求
将每个动作规范化为包含 verb（动词）、object（对象）、condition（条件）的结构。

## 输出格式（严格 JSON）
{{
  "normalized": [
    {{"verb": "click", "object": "button", "condition": "page loaded", "description": "点击按钮"}}
  ]
}}

只输出 JSON。
"""


class NormalizerAgent:
    """将提取的动作规范化为标准结构。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def normalize(self, actions: List[str]) -> List[Dict[str, Any]]:
        if not actions:
            return []
        prompt = _NORMALIZE_PROMPT.format(actions=json.dumps(actions, ensure_ascii=False))
        try:
            resp = self._llm.chat([
                Message.system("你是 SkillOS Normalizer Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = _parse_json(resp.content)
            if data and "normalized" in data:
                return data["normalized"]
        except Exception as exc:
            logger.warning(f"Normalizer LLM 失败: {exc}")
        return [{"verb": a.split()[0] if a else "do", "object": a, "description": a} for a in actions]


# ── Summarizer Agent ──────────────────────────────────────────────────────────

_SUMMARIZE_PROMPT = """
你是 SkillOS 的 Summarizer Agent，将规范化的动作序列总结为 Skill 描述。

## 规范化动作
{normalized_actions}

## 要求
生成简洁的 Skill 描述（一句话），以及建议的 Skill 类型（atomic/functional/strategic）。

## 输出格式（严格 JSON）
{{
  "description": "Skill 功能描述",
  "skill_type": "atomic",
  "tags": ["tag1", "tag2"],
  "confidence": 0.85
}}

只输出 JSON。
"""


class SummarizerAgent:
    """将规范化动作总结为 Skill 描述。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def summarize(self, normalized_actions: List[Dict[str, Any]], proposed_name: str = "") -> Dict[str, Any]:
        prompt = _SUMMARIZE_PROMPT.format(
            normalized_actions=json.dumps(normalized_actions, ensure_ascii=False)[:400],
        )
        try:
            resp = self._llm.chat([
                Message.system("你是 SkillOS Summarizer Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = _parse_json(resp.content)
            if data:
                return data
        except Exception as exc:
            logger.warning(f"Summarizer LLM 失败: {exc}")
        return {
            "description": f"执行 {proposed_name or '操作序列'}",
            "skill_type": "atomic",
            "tags": [],
            "confidence": 0.3,
        }


# ── Indexer Agent ─────────────────────────────────────────────────────────────

class IndexerAgent:
    """为 Skill 生成索引关键词和检索提示。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def index(self, skill_name: str, description: str, tags: List[str]) -> Dict[str, Any]:
        keywords = set(tags)
        keywords.update(description.lower().split()[:10])
        keywords.update(skill_name.replace("_", " ").split())
        return {
            "keywords": list(keywords),
            "embedding_hint": f"{skill_name}: {description}",
        }


# ── Pipeline ──────────────────────────────────────────────────────────────────

class ExperiencePipeline:
    """Extractor → Normalizer → Summarizer → Indexer 串联管道。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._extractor = ExtractorAgent(llm_client)
        self._normalizer = NormalizerAgent(llm_client)
        self._summarizer = SummarizerAgent(llm_client)
        self._indexer = IndexerAgent(llm_client)

    def process(self, raw_content: str, source_type: str) -> PipelineResult:
        """处理单条原始输入，返回结构化经验。"""
        import uuid
        errors: List[str] = []

        try:
            # Stage 1: Extract
            extracted = self._extractor.extract(raw_content, source_type)
            actions = extracted.get("actions", [])
            proposed_name = extracted.get("proposed_skill_name", "unnamed")
            confidence = float(extracted.get("confidence", 0.5))

            # Stage 2: Normalize
            normalized = self._normalizer.normalize(actions)

            # Stage 3: Summarize
            summary_data = self._summarizer.summarize(normalized, proposed_name)
            description = summary_data.get("description", "")
            skill_type = summary_data.get("skill_type", "atomic")
            tags = summary_data.get("tags", [])
            confidence = max(confidence, float(summary_data.get("confidence", 0.5)))

            # Stage 4: Index
            index_data = self._indexer.index(proposed_name, description, tags)

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
                token_usage=0,
                units=[unit],
            )

        except Exception as exc:
            logger.error(f"ExperiencePipeline 处理失败: {exc}")
            errors.append(str(exc))
            return PipelineResult(
                success=False,
                source_type=source_type,
                unit_count=0,
                token_usage=0,
                errors=errors,
            )

    def process_batch(self, items: List[Dict[str, str]]) -> PipelineResult:
        """批量处理多条原始输入。"""
        all_units: List[StructuredExperience] = []
        errors: List[str] = []
        for item in items:
            result = self.process(item.get("content", ""), item.get("source_type", "unknown"))
            all_units.extend(result.units)
            errors.extend(result.errors)
        return PipelineResult(
            success=len(all_units) > 0,
            source_type="batch",
            unit_count=len(all_units),
            token_usage=0,
            errors=errors,
            units=all_units,
        )


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None
