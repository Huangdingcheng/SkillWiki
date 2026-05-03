"""Skill Maintainer Agent for repair, split, merge, and deprecate actions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
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


class MaintenanceAction(str, Enum):
    REPAIR = "repair"
    SPLIT = "split"
    MERGE = "merge"
    DEPRECATE = "deprecate"
    NO_ACTION = "no_action"


@dataclass
class MaintenanceResult:
    action: MaintenanceAction
    skill_id: str
    success: bool
    updated_skill: Optional[Skill] = None
    new_skills: List[Skill] = field(default_factory=list)
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


_REPAIR_PROMPT = """
You are the SkillOS Skill Maintainer Agent.

Problem Skill:
- name: {name}
- description: {description}
- current_implementation: {implementation}

Failure information:
{failure_info}

Audit issues:
{audit_issues}

Task:
- Identify a small repair.
- Return either a repaired prompt_template or repaired code.
- Keep the repaired implementation compatible with the existing interface.

Return only valid JSON with this shape:
{{
  "repaired_prompt_template": "Repaired prompt template, if applicable",
  "repaired_code": null,
  "repair_notes": "Short repair notes",
  "confidence": 0.8
}}
"""

_SPLIT_PROMPT = """
You are the SkillOS Skill Maintainer Agent.

Skill to split:
- name: {name}
- description: {description}
- implementation: {implementation}

Reason:
{reason}

Task:
- Split the skill into a small set of atomic child skills.
- Use stable snake_case names.
- Keep each child prompt concise.
- Return 2 to 5 child skills.

Return only valid JSON with this shape:
{{
  "sub_skills": [
    {{
      "name": "sub_skill_1",
      "description": "Child skill 1 description",
      "prompt_template": "Child skill 1 prompt"
    }},
    {{
      "name": "sub_skill_2",
      "description": "Child skill 2 description",
      "prompt_template": "Child skill 2 prompt"
    }}
  ],
  "split_notes": "Short split notes",
  "confidence": 0.8
}}
"""

_MERGE_PROMPT = """
You are the SkillOS Skill Maintainer Agent.

Skill A:
{skill_a}

Skill B:
{skill_b}

Reason:
{reason}

Task:
- Merge two similar Skills into one reusable Skill.
- Preserve the useful input/output interface information.
- Return a non-empty prompt_template or code implementation.
- Use stable snake_case for the merged skill name.

Return only valid JSON with this shape:
{{
  "merged_name": "merged_skill_name",
  "merged_description": "Reusable merged skill description",
  "merged_type": "functional",
  "merged_tags": ["tag1", "tag2"],
  "merged_interface": {{
    "input_schema": {{"type": "object", "properties": {{}}}},
    "output_schema": {{"type": "object", "properties": {{}}}},
    "preconditions": [],
    "postconditions": [],
    "side_effects": []
  }},
  "merged_implementation": {{
    "language": "python",
    "code": null,
    "prompt_template": "Merged prompt"
  }},
  "merge_rationale": "Why these skills should be merged",
  "confidence": 0.8
}}
"""


class SkillMaintainerAgent:
    """Execute Skill repair, split, merge, and deprecate maintenance actions."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def repair(
        self,
        skill: Skill,
        failure_info: str = "",
        audit_issues: Optional[List[str]] = None,
    ) -> MaintenanceResult:
        """Repair a problematic Skill without changing public contracts."""
        impl_str = _implementation_summary(skill)
        prompt = _REPAIR_PROMPT.format(
            name=skill.name,
            description=skill.description,
            implementation=impl_str,
            failure_info=failure_info or "(no failure information)",
            audit_issues=json.dumps(audit_issues or [], ensure_ascii=False),
        )

        try:
            response = self._llm.chat([
                Message.system("You are the SkillOS Skill Maintainer Agent. Return JSON only."),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if not data:
                return MaintenanceResult(
                    action=MaintenanceAction.REPAIR,
                    skill_id=skill.skill_id,
                    success=False,
                    reason="repair response was not valid JSON",
                )

            repaired_prompt = str(data.get("repaired_prompt_template") or "").strip()
            repaired_code = str(data.get("repaired_code") or "").strip()
            confidence = _clamp_float(data.get("confidence"), default=0.5)
            if not repaired_prompt and not repaired_code:
                return MaintenanceResult(
                    action=MaintenanceAction.REPAIR,
                    skill_id=skill.skill_id,
                    success=False,
                    reason="repair response did not include repaired_prompt_template or repaired_code",
                    details={"confidence": confidence},
                )

            updated = skill.model_copy(deep=True)
            if updated.implementation is None:
                updated.implementation = SkillImplementation(
                    prompt_template=repaired_prompt or None,
                    code=repaired_code or None,
                )
            else:
                if repaired_prompt:
                    updated.implementation.prompt_template = repaired_prompt
                if repaired_code:
                    updated.implementation.code = repaired_code

            return MaintenanceResult(
                action=MaintenanceAction.REPAIR,
                skill_id=skill.skill_id,
                success=True,
                updated_skill=updated,
                reason=str(data.get("repair_notes") or "LLM repair"),
                details={"confidence": confidence},
            )
        except Exception as exc:
            logger.warning("Maintainer repair LLM call failed: %s", exc)

        return MaintenanceResult(
            action=MaintenanceAction.REPAIR,
            skill_id=skill.skill_id,
            success=False,
            reason=f"Repair failed: {failure_info}",
        )

    def split(self, skill: Skill, reason: str = "") -> MaintenanceResult:
        """Split an overly broad Skill into atomic child Skills."""
        prompt = _SPLIT_PROMPT.format(
            name=skill.name,
            description=skill.description,
            implementation=_implementation_summary(skill),
            reason=reason or "Skill is too broad or complex",
        )

        try:
            response = self._llm.chat([
                Message.system("You are the SkillOS Skill Maintainer Agent. Return JSON only."),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            raw_sub_skills = data.get("sub_skills") if data else None
            if not isinstance(raw_sub_skills, list):
                return MaintenanceResult(
                    action=MaintenanceAction.SPLIT,
                    skill_id=skill.skill_id,
                    success=False,
                    reason="split response did not include sub_skills",
                )

            new_skills = []
            for index, sub in enumerate(raw_sub_skills[:5], start=1):
                if not isinstance(sub, dict):
                    continue
                if not any(str(sub.get(key) or "").strip() for key in ("name", "description", "prompt_template")):
                    continue
                child = _child_skill_from_split(skill, sub, index)
                new_skills.append(child)

            if not new_skills:
                return MaintenanceResult(
                    action=MaintenanceAction.SPLIT,
                    skill_id=skill.skill_id,
                    success=False,
                    reason="split response did not include usable sub_skills",
                )

            return MaintenanceResult(
                action=MaintenanceAction.SPLIT,
                skill_id=skill.skill_id,
                success=True,
                new_skills=new_skills,
                reason=str(data.get("split_notes") or reason or "LLM split"),
                details={
                    "source_skill_id": skill.skill_id,
                    "sub_skill_count": len(new_skills),
                    "confidence": _clamp_float(data.get("confidence"), default=0.5),
                },
            )
        except Exception as exc:
            logger.warning("Maintainer split LLM call failed: %s", exc)

        return MaintenanceResult(
            action=MaintenanceAction.SPLIT,
            skill_id=skill.skill_id,
            success=False,
            reason=f"Split failed: {reason}",
        )

    def merge(self, skill_a: Skill, skill_b: Skill, reason: str = "") -> MaintenanceResult:
        """Merge two similar Skills into a new draft Skill."""
        prompt = _MERGE_PROMPT.format(
            skill_a=_skill_summary(skill_a),
            skill_b=_skill_summary(skill_b),
            reason=reason or "Skills are similar or redundant",
        )

        try:
            response = self._llm.chat([
                Message.system("You are the SkillOS Skill Maintainer Agent. Return JSON only."),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if not data:
                return MaintenanceResult(
                    action=MaintenanceAction.MERGE,
                    skill_id=skill_a.skill_id,
                    success=False,
                    reason="merge response was not valid JSON",
                    details={"source_skill_ids": [skill_a.skill_id, skill_b.skill_id]},
                )

            merged_skill = _merged_skill_from_data(skill_a, skill_b, data)
            if not merged_skill:
                return MaintenanceResult(
                    action=MaintenanceAction.MERGE,
                    skill_id=skill_a.skill_id,
                    success=False,
                    reason="merge response did not include a usable merged implementation",
                    details={"source_skill_ids": [skill_a.skill_id, skill_b.skill_id]},
                )

            return MaintenanceResult(
                action=MaintenanceAction.MERGE,
                skill_id=skill_a.skill_id,
                success=True,
                updated_skill=merged_skill,
                reason=str(data.get("merge_rationale") or reason or "LLM merge"),
                details={
                    "source_skill_ids": [skill_a.skill_id, skill_b.skill_id],
                    "merge_rationale": str(data.get("merge_rationale") or ""),
                    "confidence": _clamp_float(data.get("confidence"), default=0.5),
                },
            )
        except Exception as exc:
            logger.warning("Maintainer merge LLM call failed: %s", exc)

        return MaintenanceResult(
            action=MaintenanceAction.MERGE,
            skill_id=skill_a.skill_id,
            success=False,
            reason=f"Merge failed: {reason}",
            details={"source_skill_ids": [skill_a.skill_id, skill_b.skill_id]},
        )

    def deprecate(
        self,
        skill: Skill,
        reason: str,
        replacement_skill_id: Optional[str] = None,
    ) -> MaintenanceResult:
        """Return a deprecation decision without mutating Wiki state."""
        return MaintenanceResult(
            action=MaintenanceAction.DEPRECATE,
            skill_id=skill.skill_id,
            success=True,
            reason=reason,
            details={
                "reason": reason,
                "replacement_skill_id": replacement_skill_id,
            },
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


def _child_skill_from_split(parent: Skill, data: Dict[str, Any], index: int) -> Skill:
    sub_name = _safe_skill_name(data.get("name"), fallback=f"{parent.name}_part_{index}")
    sub_description = _safe_text(
        data.get("description"),
        fallback=f"Child skill split from {parent.name}.",
    )
    sub_prompt = _safe_text(
        data.get("prompt_template"),
        fallback=f"Execute the {sub_name.replace('_', ' ')} step.",
    )
    return Skill(
        name=sub_name,
        description=sub_description,
        skill_type=SkillType.ATOMIC,
        tags=_safe_tags(data.get("tags"), parent.tags),
        interface=SkillInterface(
            input_schema=_safe_schema(data.get("input_schema") or parent.interface.input_schema),
            output_schema=_safe_schema(data.get("output_schema") or parent.interface.output_schema),
        ),
        implementation=SkillImplementation(prompt_template=sub_prompt),
        provenance=SkillProvenance(
            source_type="split",
            created_by_agent="skill_maintainer",
            parent_skill_ids=[parent.skill_id],
        ),
    )


def _merged_skill_from_data(skill_a: Skill, skill_b: Skill, data: Dict[str, Any]) -> Optional[Skill]:
    implementation_data = data.get("merged_implementation") or data.get("implementation") or {}
    if not isinstance(implementation_data, dict):
        implementation_data = {}
    prompt_template = str(
        implementation_data.get("prompt_template") or data.get("prompt_template") or ""
    ).strip()
    code = str(implementation_data.get("code") or data.get("code") or "").strip()
    sub_skill_ids = implementation_data.get("sub_skill_ids") or []
    if not isinstance(sub_skill_ids, list):
        sub_skill_ids = []
    if not prompt_template and not code and not sub_skill_ids:
        return None

    skill_type = _safe_skill_type(data.get("merged_type") or data.get("skill_type"))
    interface_data = data.get("merged_interface") or data.get("interface") or {}
    if not isinstance(interface_data, dict):
        interface_data = {}
    meta_category = MetaSkillCategory.MAINTENANCE if skill_type == SkillType.STRATEGIC else None
    return Skill(
        name=_safe_skill_name(data.get("merged_name") or data.get("name"), fallback=f"{skill_a.name}_{skill_b.name}"),
        description=_safe_text(
            data.get("merged_description") or data.get("description"),
            fallback=f"Merged skill from {skill_a.name} and {skill_b.name}.",
        ),
        skill_type=skill_type,
        meta_category=meta_category,
        domain=str(data.get("merged_domain") or data.get("domain") or skill_a.domain or skill_b.domain or "general"),
        granularity_level=_safe_granularity(data.get("merged_granularity_level"), default=max(skill_a.granularity_level, skill_b.granularity_level)),
        tags=_safe_tags(data.get("merged_tags") or data.get("tags"), [*skill_a.tags, *skill_b.tags, "merged"]),
        interface=SkillInterface(
            input_schema=_safe_schema(interface_data.get("input_schema") or _merge_schema(skill_a.interface.input_schema, skill_b.interface.input_schema)),
            output_schema=_safe_schema(interface_data.get("output_schema") or _merge_schema(skill_a.interface.output_schema, skill_b.interface.output_schema)),
            preconditions=_safe_string_list(interface_data.get("preconditions")),
            postconditions=_safe_string_list(interface_data.get("postconditions")),
            side_effects=_safe_string_list(interface_data.get("side_effects")),
        ),
        implementation=SkillImplementation(
            language=str(implementation_data.get("language") or "python"),
            code=code or None,
            prompt_template=prompt_template or None,
            tool_calls=_safe_string_list(implementation_data.get("tool_calls")),
            sub_skill_ids=_safe_string_list(sub_skill_ids),
        ),
        provenance=SkillProvenance(
            source_type="merge",
            created_by_agent="skill_maintainer",
            parent_skill_ids=[skill_a.skill_id, skill_b.skill_id],
            creation_context={
                "merge_rationale": str(data.get("merge_rationale") or ""),
                "source_skill_names": [skill_a.name, skill_b.name],
            },
        ),
    )


def _implementation_summary(skill: Skill) -> str:
    impl = skill.implementation
    if not impl:
        return "(no implementation)"
    if impl.prompt_template:
        return f"prompt: {impl.prompt_template[:200]}"
    if impl.code:
        return f"code: {impl.code[:200]}"
    if impl.sub_skill_ids:
        return f"sub_skill_ids: {impl.sub_skill_ids}"
    return "(no implementation)"


def _skill_summary(skill: Skill) -> str:
    return json.dumps(
        {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "description": skill.description,
            "skill_type": skill.skill_type.value,
            "domain": skill.domain,
            "tags": skill.tags,
            "input_schema": skill.interface.input_schema,
            "output_schema": skill.interface.output_schema,
            "implementation": _implementation_summary(skill),
        },
        ensure_ascii=False,
    )[:1200]


def _merge_schema(first: Dict[str, Any], second: Dict[str, Any]) -> Dict[str, Any]:
    merged = {"type": "object", "properties": {}}
    for schema in (first or {}, second or {}):
        props = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(props, dict):
            merged["properties"].update(props)
    return merged


def _safe_schema(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": "object", "properties": {}}
    schema = dict(value)
    if schema.get("type") != "object":
        schema["type"] = "object"
    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    return schema


def _safe_skill_name(value: Any, *, fallback: str) -> str:
    raw = str(value or fallback or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        raw = "maintained_skill"
    if not re.match(r"^[a-z]", raw):
        raw = f"skill_{raw}"
    return raw[:128]


def _safe_skill_type(value: Any) -> SkillType:
    skill_type = str(value or "").strip().lower()
    allowed = {item.value for item in SkillType}
    return SkillType(skill_type) if skill_type in allowed else SkillType.FUNCTIONAL


def _safe_tags(value: Any, fallback: List[str]) -> List[str]:
    raw_tags = value if isinstance(value, list) else fallback
    tags: List[str] = []
    for tag in raw_tags:
        slug = re.sub(r"[^a-z0-9_]+", "_", str(tag or "").strip().lower())
        slug = re.sub(r"_+", "_", slug).strip("_")
        if slug and slug not in tags:
            tags.append(slug)
    return tags[:8]


def _safe_text(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _safe_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _safe_granularity(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(5, number))


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))
