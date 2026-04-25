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
你是 SkillOS 的 Skill Maintainer Agent，负责修复有问题的 Skill。

## 问题 Skill
名称: {name}
描述: {description}
当前实现: {implementation}

## 失败信息
{failure_info}

## 审计问题
{audit_issues}

## 要求
生成修复后的 prompt_template 或 code 实现。

## 输出格式（严格 JSON）
{{
  "repaired_prompt_template": "修复后的 prompt 模板（如适用）",
  "repaired_code": null,
  "repair_notes": "修复说明",
  "confidence": 0.8
}}

只输出 JSON。
"""

_SPLIT_PROMPT = """
你是 SkillOS 的 Skill Maintainer Agent，负责将过大的 Skill 拆分为多个子 Skill。

## 待拆分 Skill
名称: {name}
描述: {description}
实现: {implementation}

## 拆分原因
{reason}

## 输出格式（严格 JSON）
{{
  "sub_skills": [
    {{
      "name": "sub_skill_1",
      "description": "子 Skill 1 描述",
      "prompt_template": "子 Skill 1 的 prompt"
    }},
    {{
      "name": "sub_skill_2",
      "description": "子 Skill 2 描述",
      "prompt_template": "子 Skill 2 的 prompt"
    }}
  ],
  "split_notes": "拆分说明"
}}

只输出 JSON。
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
            implementation=impl_str or "（无实现）",
            failure_info=failure_info or "（无失败信息）",
            audit_issues=json.dumps(audit_issues or [], ensure_ascii=False),
        )

        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Skill Maintainer Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                updated = skill.model_copy(deep=True)
                if updated.implementation is None:
                    updated.implementation = SkillImplementation()
                if data.get("repaired_prompt_template"):
                    updated.implementation.prompt_template = data["repaired_prompt_template"]
                if data.get("repaired_code"):
                    updated.implementation.code = data["repaired_code"]
                return MaintenanceResult(
                    action=MaintenanceAction.REPAIR,
                    skill_id=skill.skill_id,
                    success=True,
                    updated_skill=updated,
                    reason=data.get("repair_notes", "LLM 修复"),
                )
        except Exception as exc:
            logger.warning(f"Maintainer repair LLM 失败: {exc}")

        return MaintenanceResult(
            action=MaintenanceAction.REPAIR,
            skill_id=skill.skill_id,
            success=False,
            reason=f"修复失败: {failure_info}",
        )

    def split(self, skill: Skill, reason: str = "") -> MaintenanceResult:
        """将过大的 Skill 拆分为多个子 Skill。"""
        impl = skill.implementation
        impl_str = impl.prompt_template[:150] if impl and impl.prompt_template else "（无实现）"

        prompt = _SPLIT_PROMPT.format(
            name=skill.name,
            description=skill.description,
            implementation=impl_str,
            reason=reason or "Skill 功能过于复杂",
        )

        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Skill Maintainer Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data and data.get("sub_skills"):
                from ...models.skill_model import SkillInterface, SkillProvenance, SkillType
                new_skills = []
                for sub in data["sub_skills"]:
                    s = Skill(
                        name=sub.get("name", f"{skill.name}_sub"),
                        description=sub.get("description", ""),
                        skill_type=SkillType.ATOMIC,
                        tags=skill.tags,
                        interface=SkillInterface(
                            input_schema=skill.interface.input_schema,
                            output_schema=skill.interface.output_schema,
                        ),
                        implementation=SkillImplementation(
                            prompt_template=sub.get("prompt_template"),
                        ),
                        provenance=SkillProvenance(
                            source_type="split",
                            author="skill_maintainer",
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
            logger.warning(f"Maintainer split LLM 失败: {exc}")

        return MaintenanceResult(
            action=MaintenanceAction.SPLIT,
            skill_id=skill.skill_id,
            success=False,
            reason=f"拆分失败: {reason}",
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
