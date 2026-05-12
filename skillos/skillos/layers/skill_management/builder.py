"""Skill Builder Agent — 从任务/轨迹/文档生成 Skill 草稿。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...models.skill_model import (
    MetaSkillCategory,
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
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


DEFAULT_OBJECT_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}}
ALLOWED_SKILL_TYPES = {item.value for item in SkillType}


_BUILD_FROM_TASK_PROMPT = """
You are the SkillOS Skill Builder Agent.

Input task:
{task_description}

Context:
{context}

Task:
- Identify one reusable Skill from the task.
- Prefer a small atomic Skill unless the task clearly needs composition.
- Use stable snake_case for the skill name.
- Use skill_type only from: atomic, functional, strategic.
- Make prompt_template variables match input_schema.properties.
- Do not create vague skills such as "handle_task" or "process_data".
- Keep all fields concise and useful for a future agent.

Quality examples:
- Atomic: extract_api_endpoint, input api_document, output endpoints.
- Functional: summarize_execution_trace, input trajectory_text, output summary and action_count.
- Strategic: plan_multi_step_repair, input failure_report, output repair_plan.

Return only valid JSON with this shape:
{{
  "name": "skill_name_snake_case",
  "description": "One sentence skill description",
  "skill_type": "atomic",
  "tags": ["tag1", "tag2"],
  "input_schema": {{
    "type": "object",
    "properties": {{
      "param1": {{"type": "string", "description": "Parameter description"}}
    }},
    "required": ["param1"]
  }},
  "output_schema": {{
    "type": "object",
    "properties": {{
      "result": {{"type": "string"}}
    }}
  }},
  "prompt_template": "Execute the requested action using {{param1}}.",
  "confidence": 0.85,
  "build_notes": "Why this reusable skill was created"
}}
"""

_BUILD_FROM_TRAJECTORY_PROMPT = """
You are the SkillOS Skill Builder Agent.

Execution trajectory:
{trajectory}

Task:
- Extract one reusable Skill from the trajectory.
- Use stable snake_case for the skill name.
- Use skill_type only from: atomic, functional, strategic.
- Make prompt_template variables match input_schema.properties.
- Prefer an atomic Skill unless the trajectory clearly shows a reusable multi-step workflow.
- Keep schemas valid JSON Schema objects.

Quality examples:
- Atomic: click_confirm_button from a repeated click step.
- Functional: import_markdown_document from parse, validate, and create steps.
- Strategic: diagnose_failed_execution from error clustering and repair planning.

Return only valid JSON with this shape:
{{
  "name": "skill_name_snake_case",
  "description": "One sentence skill description",
  "skill_type": "atomic",
  "tags": ["tag1"],
  "input_schema": {{"type": "object", "properties": {{}}, "required": []}},
  "output_schema": {{"type": "object", "properties": {{}}}},
  "prompt_template": "Execute the reusable trajectory workflow.",
  "confidence": 0.75,
  "build_notes": "Extracted from trajectory"
}}
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

    def _build(self, prompt: str, source_type: str, raw_input: str) -> SkillDraft:
        try:
            response = self._llm.chat([
                Message.system("You are the SkillOS Skill Builder Agent. Return JSON only."),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                return self._draft_from_data(data, source_type, raw_input)
        except Exception as exc:
            logger.warning("SkillBuilder LLM call failed: %s", exc)

        return self._fallback_draft(source_type, raw_input)

    def _draft_from_data(
        self,
        data: Dict[str, Any],
        source_type: str,
        raw_input: str,
    ) -> SkillDraft:
        name = _safe_skill_name(data.get("name"), fallback=f"skill_from_{source_type}")
        skill_type = _safe_skill_type(data.get("skill_type"))
        prompt_template = _safe_text(
            data.get("prompt_template"),
            fallback=f"Execute the reusable {name.replace('_', ' ')} workflow.",
        )
        description = _safe_description(data.get("description"), source_type=source_type, name=name)
        input_schema = _align_schema_with_prompt(_safe_schema(data.get("input_schema")), prompt_template)
        output_schema = _safe_schema(data.get("output_schema"))

        skill = Skill(
            name=name,
            description=description,
            skill_type=skill_type,
            meta_category=MetaSkillCategory.GENERATION
            if skill_type == SkillType.STRATEGIC
            else None,
            tags=_safe_tags(data.get("tags"), source_type),
            interface=SkillInterface(
                input_schema=input_schema,
                output_schema=output_schema,
            ),
            implementation=SkillImplementation(prompt_template=prompt_template),
            provenance=SkillProvenance(
                source_type=source_type,
                created_by_agent="skill_builder",
                creation_context={"builder_source": source_type},
            ),
        )
        return SkillDraft(
            skill=skill,
            confidence=_clamp_float(data.get("confidence"), default=0.7),
            source_type=source_type,
            raw_input=raw_input[:200],
            build_notes=_safe_text(data.get("build_notes"), fallback="Generated by SkillBuilderAgent."),
        )

    def _fallback_draft(self, source_type: str, raw_input: str) -> SkillDraft:
        name = _safe_skill_name("", fallback=f"skill_from_{source_type}")
        description = f"Execute a reusable workflow derived from {source_type} input."
        skill = Skill(
            name=name,
            description=description,
            skill_type=SkillType.ATOMIC,
            tags=_safe_tags([source_type], source_type),
            interface=SkillInterface(
                input_schema=dict(DEFAULT_OBJECT_SCHEMA),
                output_schema=dict(DEFAULT_OBJECT_SCHEMA),
            ),
            implementation=SkillImplementation(
                prompt_template=f"Execute the reusable {name.replace('_', ' ')} workflow."
            ),
            provenance=SkillProvenance(
                source_type=source_type,
                created_by_agent="skill_builder",
                creation_context={"fallback": True},
            ),
        )
        return SkillDraft(
            skill=skill,
            confidence=0.1,
            source_type=source_type,
            raw_input=raw_input[:200],
            build_notes="Fallback draft generated because the LLM response was unavailable or invalid.",
        )

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


def _safe_text(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _safe_description(value: Any, *, source_type: str, name: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 16:
        return text
    readable_name = name.replace("_", " ")
    return f"Reusable {readable_name} skill derived from {source_type} input."


def _safe_skill_name(value: Any, *, fallback: str) -> str:
    raw = str(value or fallback or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        raw = "generated_skill"
    if not re.match(r"^[a-z]", raw):
        raw = f"skill_{raw}"
    return raw[:128]


def _safe_skill_type(value: Any) -> SkillType:
    skill_type = str(value or "").strip().lower()
    return SkillType(skill_type) if skill_type in ALLOWED_SKILL_TYPES else SkillType.ATOMIC


def _safe_schema(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return dict(DEFAULT_OBJECT_SCHEMA)
    schema = dict(value)
    if schema.get("type") != "object":
        schema["type"] = "object"
    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    else:
        schema["properties"] = {
            str(key): prop if isinstance(prop, dict) else {"type": "string"}
            for key, prop in schema["properties"].items()
            if str(key).strip()
        }
    if "required" in schema and not isinstance(schema["required"], list):
        schema["required"] = []
    if isinstance(schema.get("required"), list):
        properties = schema.get("properties", {})
        schema["required"] = [
            str(field)
            for field in schema["required"]
            if isinstance(field, str) and field in properties
        ]
    return schema


def _align_schema_with_prompt(schema: Dict[str, Any], prompt_template: str) -> Dict[str, Any]:
    properties = schema.setdefault("properties", {})
    for variable in sorted(_extract_prompt_variables(prompt_template)):
        if variable not in properties:
            properties[variable] = {
                "type": "string",
                "description": f"Value for the {variable} prompt variable.",
            }
    if "required" in schema and isinstance(schema["required"], list):
        schema["required"] = [
            field for field in schema["required"] if isinstance(field, str) and field in properties
        ]
    return schema


def _extract_prompt_variables(prompt_template: str) -> set[str]:
    variables: set[str] = set()
    for match in re.finditer(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", prompt_template):
        variables.add(match.group(1))
    for match in re.finditer(r"(?<!\{)\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}(?!\})", prompt_template):
        variables.add(match.group(1))
    return variables


def _safe_tags(value: Any, source_type: str) -> List[str]:
    raw_tags = value if isinstance(value, list) else []
    tags: List[str] = []
    for tag in [source_type, *raw_tags]:
        slug = re.sub(r"[^a-z0-9_]+", "_", str(tag or "").strip().lower())
        slug = re.sub(r"_+", "_", slug).strip("_")
        if slug and slug not in tags:
            tags.append(slug)
    return tags[:8]


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))
