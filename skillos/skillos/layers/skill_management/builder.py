"""Skill Builder Agent — 从任务/轨迹/文档生成 Skill 草稿。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.skill_model import (
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillTestCase,
    SkillType,
)
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SkillDraft:
    skill: Skill
    confidence: float
    source_type: str
    raw_input: str = ""
    build_notes: str = ""


_BUILD_FROM_TASK_PROMPT = """
你是 SkillOS 的 Skill Builder Agent，负责从任务描述中提取并生成 Skill。

## 输入任务
{task_description}

## 上下文
{context}

## 要求
从任务中识别可复用的原子操作，生成 Skill 定义。

## 输出格式（严格 JSON）
{{
  "name": "skill_name_snake_case",
  "description": "Skill 功能描述（一句话）",
  "skill_type": "atomic",
  "tags": ["tag1", "tag2"],
  "input_schema": {{
    "type": "object",
    "properties": {{
      "param1": {{"type": "string", "description": "参数描述"}}
    }},
    "required": ["param1"]
  }},
  "output_schema": {{
    "type": "object",
    "properties": {{
      "result": {{"type": "string"}}
    }}
  }},
  "prompt_template": "请执行以下操作：{{param1}}",
  "confidence": 0.85,
  "build_notes": "构建说明"
}}

只输出 JSON。
"""

_BUILD_FROM_TRAJECTORY_PROMPT = """
你是 SkillOS 的 Skill Builder Agent，负责从执行轨迹中提取 Skill。

## 执行轨迹
{trajectory}

## 要求
分析轨迹中的操作模式，提取可复用的 Skill。

## 输出格式（严格 JSON）
{{
  "name": "skill_name_snake_case",
  "description": "Skill 功能描述",
  "skill_type": "atomic",
  "tags": ["tag1"],
  "input_schema": {{"type": "object", "properties": {{}}, "required": []}},
  "output_schema": {{"type": "object", "properties": {{}}}},
  "prompt_template": "基于轨迹的执行模板",
  "confidence": 0.75,
  "build_notes": "从轨迹提取"
}}

只输出 JSON。
"""


class SkillBuilderAgent:
    """从任务/轨迹/文档生成 Skill 草稿。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def build_from_task(
        self, task_description: str, context: Optional[Dict[str, Any]] = None
    ) -> SkillDraft:
        """从任务描述生成 Skill。"""
        prompt = _BUILD_FROM_TASK_PROMPT.format(
            task_description=task_description,
            context=json.dumps(context or {}, ensure_ascii=False)[:200],
        )
        return self._build(prompt, "task", task_description)

    def build_from_trajectory(self, trajectory: str) -> SkillDraft:
        """从执行轨迹生成 Skill。"""
        prompt = _BUILD_FROM_TRAJECTORY_PROMPT.format(trajectory=trajectory[:800])
        return self._build(prompt, "trajectory", trajectory)

    def build_from_experience_unit(self, unit: Any) -> SkillDraft:
        """Build a Skill draft from a structured fixed-demo experience unit.

        This deterministic path is the default for the research demo: curated
        source JSON has already been normalized by the input pipeline, so the
        Builder Agent should formalize it instead of asking the LLM again.
        """
        metadata = getattr(unit, "metadata", {}) or {}
        name = _normalize_skill_name(
            getattr(unit, "proposed_skill_name", None)
            or metadata.get("raw_skill", {}).get("name")
            or "extracted_skill"
        )
        description = (
            getattr(unit, "proposed_description", None)
            or getattr(unit, "summary", "")
            or f"Skill extracted from {getattr(unit, 'source_type', 'source')}."
        )
        skill_type = _safe_skill_type(getattr(unit, "proposed_type", None))
        source_type = str(getattr(unit, "source_type", metadata.get("source_type", "ingest")))
        interface = _interface_from_metadata(metadata)
        implementation = _implementation_from_metadata(metadata, description)
        tags = _unique_tags(
            [source_type, "auto-imported", "agent-built"]
            + list(getattr(unit, "index_keywords", []) or [])[:6]
            + [
                str(metadata.get("capability_scope", "")),
                str(metadata.get("capability_kind", "")),
            ]
        )

        skill = Skill(
            name=name,
            description=description,
            skill_type=skill_type,
            state=SkillState.SKILL_CANDIDATE,
            tags=tags,
            interface=interface,
            implementation=implementation,
            test_cases=_test_cases_from_metadata(metadata),
            provenance=SkillProvenance(
                source_type=source_type,
                source_ids=[
                    str(getattr(unit, "unit_id", "")),
                    str(metadata.get("source_id", "")),
                ],
                created_by_agent="SkillBuilderAgent",
                creation_context={
                    "source_title": metadata.get("source_title"),
                    "confidence": getattr(unit, "confidence", 0.0),
                    "pipeline": "fixed_demo_experience_pipeline",
                    "capability_scope": metadata.get("capability_scope"),
                    "capability_kind": metadata.get("capability_kind"),
                    "target": metadata.get("target"),
                },
            ),
        )
        return SkillDraft(
            skill=skill,
            confidence=float(getattr(unit, "confidence", 0.8) or 0.8),
            source_type=source_type,
            raw_input=getattr(unit, "raw_content", "")[:200],
            build_notes="Built deterministically from a structured experience unit.",
        )

    def _build(self, prompt: str, source_type: str, raw_input: str) -> SkillDraft:
        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Skill Builder Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                skill_type_str = data.get("skill_type", "atomic")
                try:
                    skill_type = SkillType(skill_type_str)
                except ValueError:
                    skill_type = SkillType.ATOMIC

                skill = Skill(
                    name=data.get("name", "unnamed_skill"),
                    description=data.get("description", ""),
                    skill_type=skill_type,
                    tags=data.get("tags", []),
                    interface=SkillInterface(
                        input_schema=data.get("input_schema", {"type": "object", "properties": {}}),
                        output_schema=data.get("output_schema", {"type": "object", "properties": {}}),
                    ),
                    implementation=SkillImplementation(
                        prompt_template=data.get("prompt_template"),
                    ),
                    provenance=SkillProvenance(
                        source_type=source_type,
                        created_by_agent="SkillBuilderAgent",
                    ),
                )
                return SkillDraft(
                    skill=skill,
                    confidence=float(data.get("confidence", 0.7)),
                    source_type=source_type,
                    raw_input=raw_input[:200],
                    build_notes=data.get("build_notes", ""),
                )
        except Exception as exc:
            logger.warning(f"SkillBuilder LLM 调用失败: {exc}")

        # 降级：生成占位 Skill
        skill = Skill(
            name="unnamed_skill",
            description=f"从 {source_type} 自动生成（LLM 失败）",
            skill_type=SkillType.ATOMIC,
            tags=[source_type],
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object", "properties": {}},
            ),
            provenance=SkillProvenance(
                source_type=source_type,
                created_by_agent="SkillBuilderAgent",
            ),
        )
        return SkillDraft(skill=skill, confidence=0.1, source_type=source_type, raw_input=raw_input[:200])

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
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


def _safe_skill_type(value: Optional[str]) -> SkillType:
    try:
        return SkillType(value or "functional")
    except ValueError:
        return SkillType.FUNCTIONAL


def _interface_from_metadata(metadata: Dict[str, Any]) -> SkillInterface:
    interface = metadata.get("interface") if isinstance(metadata, dict) else None
    if isinstance(interface, dict):
        return SkillInterface(
            input_schema=interface.get("input_schema") or {"type": "object", "properties": {}},
            output_schema=interface.get("output_schema") or {"type": "object", "properties": {}},
            preconditions=interface.get("preconditions") or [],
            postconditions=interface.get("postconditions") or [],
            side_effects=interface.get("side_effects") or [],
        )
    return SkillInterface(
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
    )


def _implementation_from_metadata(metadata: Dict[str, Any], fallback_prompt: str) -> SkillImplementation:
    implementation = metadata.get("implementation") if isinstance(metadata, dict) else None
    if isinstance(implementation, dict):
        prompt_template = implementation.get("prompt_template")
        code = implementation.get("code")
        sub_skill_ids = implementation.get("sub_skill_ids") or []
        if prompt_template or code or sub_skill_ids:
            return SkillImplementation(
                language=implementation.get("language") or "python",
                code=code,
                prompt_template=prompt_template,
                tool_calls=implementation.get("tool_calls") or [],
                sub_skill_ids=sub_skill_ids,
            )
    return SkillImplementation(prompt_template=fallback_prompt)


def _test_cases_from_metadata(metadata: Dict[str, Any]) -> List[SkillTestCase]:
    raw_skill = metadata.get("raw_skill") if isinstance(metadata, dict) else None
    cases = raw_skill.get("test_cases") if isinstance(raw_skill, dict) else None
    result: List[SkillTestCase] = []
    if isinstance(cases, list):
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                continue
            result.append(SkillTestCase(
                name=str(case.get("name") or f"imported_case_{index + 1}"),
                description=str(case.get("description") or "Imported natural workflow validation case."),
                input_data=case.get("input_data") if isinstance(case.get("input_data"), dict) else {},
                expected_output=case.get("expected_output") if isinstance(case.get("expected_output"), dict) else None,
                expected_state_changes=case.get("expected_state_changes") if isinstance(case.get("expected_state_changes"), dict) else {},
                tags=[str(tag) for tag in case.get("tags", [])] if isinstance(case.get("tags"), list) else ["imported"],
            ))
    for name in metadata.get("tests", []) if isinstance(metadata.get("tests"), list) else []:
        if any(case.name == str(name) for case in result):
            continue
        result.append(SkillTestCase(
            name=str(name),
            description=f"Runtime validation case for {name}.",
            input_data={},
            expected_output=None,
            tags=["imported"],
        ))
    return result


def _normalize_skill_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip()).strip("_").lower()
    if not cleaned:
        return "extracted_skill"
    if not cleaned[0].isalpha():
        cleaned = f"skill_{cleaned}"
    return cleaned


def _unique_tags(tags: List[str]) -> List[str]:
    seen = set()
    result = []
    for tag in tags:
        cleaned = str(tag).strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result
