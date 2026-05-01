"""Skill Maintainer Agent — 执行 repair/split/merge/deprecate 操作。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill, SkillImplementation
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
  "split_notes": "Short split notes"
}}
"""


class SkillMaintainerAgent:
    """执行 Skill 的 repair/split/merge/deprecate 操作。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def repair(
        self,
        skill: Skill,
        failure_info: str = "",
        audit_issues: Optional[List[str]] = None,
    ) -> MaintenanceResult:
        """修复有问题的 Skill。"""
        impl = skill.implementation
        impl_str = ""
        if impl:
            if impl.prompt_template:
                impl_str = f"prompt: {impl.prompt_template[:150]}"
            elif impl.code:
                impl_str = f"code: {impl.code[:150]}"

        prompt = _REPAIR_PROMPT.format(
            name=skill.name,
            description=skill.description,
            implementation=impl_str or "(no implementation)",
            failure_info=failure_info or "(no failure information)",
            audit_issues=json.dumps(audit_issues or [], ensure_ascii=False),
        )

        try:
            response = self._llm.chat([
                Message.system("You are the SkillOS Skill Maintainer Agent. Return JSON only."),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                updated = skill.model_copy(deep=True)
                repaired_prompt = str(data.get("repaired_prompt_template") or "").strip()
                repaired_code = str(data.get("repaired_code") or "").strip()
                if not repaired_prompt and not repaired_code:
                    return MaintenanceResult(
                        action=MaintenanceAction.REPAIR,
                        skill_id=skill.skill_id,
                        success=False,
                        reason="repair response did not include repaired_prompt_template or repaired_code",
                    )
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
                    details={"confidence": _clamp_float(data.get("confidence"), default=0.5)},
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
        """将过大的 Skill 拆分为多个子 Skill。"""
        impl = skill.implementation
        impl_str = impl.prompt_template[:150] if impl and impl.prompt_template else "(no implementation)"

        prompt = _SPLIT_PROMPT.format(
            name=skill.name,
            description=skill.description,
            implementation=impl_str,
            reason=reason or "Skill is too broad or complex",
        )

        try:
            response = self._llm.chat([
                Message.system("You are the SkillOS Skill Maintainer Agent. Return JSON only."),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data and data.get("sub_skills"):
                from ...models.skill_model import SkillInterface, SkillProvenance, SkillType
                new_skills = []
                for sub in data["sub_skills"]:
                    sub_name = _safe_skill_name(sub.get("name"), fallback=f"{skill.name}_sub")
                    sub_description = str(sub.get("description") or "").strip()
                    if not sub_description:
                        sub_description = f"Child skill split from {skill.name}."
                    sub_prompt = str(sub.get("prompt_template") or "").strip()
                    if not sub_prompt:
                        sub_prompt = f"Execute the {sub_name.replace('_', ' ')} step."
                    s = Skill(
                        name=sub_name,
                        description=sub_description,
                        skill_type=SkillType.ATOMIC,
                        tags=skill.tags,
                        interface=SkillInterface(
                            input_schema=skill.interface.input_schema,
                            output_schema=skill.interface.output_schema,
                        ),
                        implementation=SkillImplementation(
                            prompt_template=sub_prompt,
                        ),
                        provenance=SkillProvenance(
                            source_type="split",
                            created_by_agent="skill_maintainer",
                            parent_skill_ids=[skill.skill_id],
                        ),
                    )
                    new_skills.append(s)
                return MaintenanceResult(
                    action=MaintenanceAction.SPLIT,
                    skill_id=skill.skill_id,
                    success=True,
                    new_skills=new_skills,
                    reason=data.get("split_notes", reason),
                )
        except Exception as exc:
            logger.warning("Maintainer split LLM call failed: %s", exc)

        return MaintenanceResult(
            action=MaintenanceAction.SPLIT,
            skill_id=skill.skill_id,
            success=False,
            reason=f"Split failed: {reason}",
        )

    def deprecate(self, skill: Skill, reason: str) -> MaintenanceResult:
        """标记 Skill 为废弃。"""
        return MaintenanceResult(
            action=MaintenanceAction.DEPRECATE,
            skill_id=skill.skill_id,
            success=True,
            reason=reason,
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


def _safe_skill_name(value: Any, *, fallback: str) -> str:
    raw = str(value or fallback or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        raw = "child_skill"
    if not re.match(r"^[a-z]", raw):
        raw = f"skill_{raw}"
    return raw[:128]


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))
