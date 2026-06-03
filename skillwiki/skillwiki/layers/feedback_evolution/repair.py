"""Skill 自动修复器 — 对退化 Skill 进行 LLM 驱动的自动修复。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill, SkillImplementation, SkillState
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger
from .monitor import SkillHealthReport

logger = get_logger(__name__)

_REPAIR_PROMPT = """
You are the SkillWiki Skill Repair Agent.

Problem Skill:
- name: {name}
- description: {description}
- type: {skill_type}
- current implementation:
{implementation}

Health report:
- success_rate: {success_rate:.1%}
- usage_count: {usage_count}
- avg_latency_ms: {avg_latency_ms:.0f}
- issues:
{issues}

Recent failure cases:
{failure_cases}

Task:
1. Identify the root cause.
2. Provide a repaired implementation when possible.
3. Update preconditions or postconditions only when needed.
4. If the skill cannot be safely repaired, set fix_type to "deprecate".

Return only valid JSON with this shape:
{{
  "root_cause": "Root cause analysis",
  "fix_type": "code_fix|prompt_fix|interface_fix|deprecate",
  "fixed_implementation": {{
    "language": "python",
    "code": null,
    "prompt_template": "Repaired prompt if applicable",
    "tool_calls": [],
    "sub_skill_ids": []
  }},
  "updated_preconditions": [],
  "updated_postconditions": [],
  "confidence": 0.8,
  "repair_notes": "Short repair notes"
}}
"""


@dataclass
class RepairResult:
    """修复结果。"""
    skill_id: str
    success: bool = False
    fix_type: str = ""
    repaired_skill: Optional[Skill] = None
    root_cause: str = ""
    repair_notes: str = ""
    confidence: float = 0.0
    should_deprecate: bool = False
    error: str = ""


class SkillRepair:
    """Skill 自动修复器。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def repair(
        self,
        skill: Skill,
        health_report: SkillHealthReport,
        failure_cases: Optional[List[Dict[str, Any]]] = None,
    ) -> RepairResult:
        """尝试自动修复退化的 Skill。"""
        result = RepairResult(skill_id=skill.skill_id)

        impl_info = "No implementation"
        if skill.implementation:
            impl = skill.implementation
            if impl.code:
                impl_info = f"```python\n{impl.code[:800]}\n```"
            elif impl.prompt_template:
                impl_info = f"Prompt: {impl.prompt_template[:400]}"
            elif impl.sub_skill_ids:
                impl_info = f"Composed child skills: {impl.sub_skill_ids}"

        failure_summary = "No failure cases recorded"
        if failure_cases:
            lines = []
            for i, case in enumerate(failure_cases[:5]):
                lines.append(
                    f"{i+1}. Input: {json.dumps(case.get('input', {}), ensure_ascii=False)[:100]}\n"
                    f"   Error: {case.get('error', 'unknown')}"
                )
            failure_summary = "\n".join(lines)

        prompt = _REPAIR_PROMPT.format(
            name=skill.name,
            description=skill.description,
            skill_type=skill.skill_type.value,
            implementation=impl_info,
            success_rate=health_report.success_rate,
            usage_count=health_report.usage_count,
            avg_latency_ms=health_report.avg_latency_ms,
            issues="\n".join(f"- {i}" for i in health_report.issues),
            failure_cases=failure_summary,
        )

        try:
            response = self._llm.chat([
                Message.system("You are the SkillWiki Skill Repair Agent. Return JSON only."),
                Message.user(prompt),
            ])
        except Exception as exc:
            result.error = f"LLM repair call failed: {exc}"
            result.root_cause = "repair_llm_unavailable"
            result.repair_notes = "Repair could not run because the LLM call failed."
            logger.warning("Skill repair LLM failed: %s", exc)
            return result

        data = self._extract_json(response.content)
        if not data:
            result.error = "LLM returned invalid JSON"
            result.root_cause = "invalid_repair_response"
            return result

        result.root_cause = str(data.get("root_cause") or "")
        result.fix_type = _safe_fix_type(data.get("fix_type"))
        result.confidence = _clamp_float(data.get("confidence"), default=0.5)
        result.repair_notes = str(data.get("repair_notes") or "")

        if result.fix_type == "deprecate":
            result.should_deprecate = True
            result.success = True
            logger.info("Skill should be deprecated: %s - %s", skill.name, result.root_cause)
            return result

        # 应用修复
        try:
            repaired = skill.model_copy(deep=True)
            repaired.bump_version("patch")
            repaired.state = SkillState.DRAFT  # 修复后回到 Draft 重新验证

            impl_data = data.get("fixed_implementation", {})
            if impl_data:
                code = impl_data.get("code") or None
                prompt_template = impl_data.get("prompt_template") or None
                sub_skill_ids = impl_data.get("sub_skill_ids", []) or []
                if not code and not prompt_template and not sub_skill_ids:
                    result.error = "fixed_implementation is empty"
                    return result
                repaired.implementation = SkillImplementation(
                    language=impl_data.get("language", "python"),
                    code=code,
                    prompt_template=prompt_template,
                    tool_calls=impl_data.get("tool_calls", []),
                    sub_skill_ids=sub_skill_ids,
                )
            else:
                result.error = "missing fixed_implementation"
                return result

            if data.get("updated_preconditions"):
                repaired.interface.preconditions = data["updated_preconditions"]
            if data.get("updated_postconditions"):
                repaired.interface.postconditions = data["updated_postconditions"]

            result.repaired_skill = repaired
            result.success = True
            logger.info(
                f"Skill repair succeeded: {skill.name} v{skill.version} -> v{repaired.version} "
                f"(confidence={result.confidence:.2f})"
            )
        except Exception as e:
            result.error = str(e)
            logger.error("Skill repair failed: %s - %s", skill.name, e)

        return result

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


def _safe_fix_type(value: Any) -> str:
    fix_type = str(value or "").strip()
    allowed = {"code_fix", "prompt_fix", "interface_fix", "deprecate"}
    return fix_type if fix_type in allowed else "prompt_fix"


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))
