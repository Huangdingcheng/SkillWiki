"""Skill Auditor Agent — 对 Skill 进行 schema/安全/后置条件审计。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AuditResult:
    skill_id: str
    skill_name: str
    passed: bool
    schema_ok: bool = True
    safety_ok: bool = True
    postcondition_ok: bool = True
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    audit_score: float = 1.0


_AUDIT_PROMPT = """
You are the SkillOS Skill Auditor Agent.

Skill definition:
- name: {name}
- description: {description}
- type: {skill_type}
- tags: {tags}
- input_schema: {input_schema}
- output_schema: {output_schema}
- implementation: {implementation}

Audit dimensions:
1. Schema completeness and consistency.
2. Safety risks, including injection, privilege misuse, data leaks, and resource abuse.
3. Whether the implementation can satisfy the skill description and postconditions.
4. Naming quality and maintainability.

Return only valid JSON with this shape:
{{
  "passed": true,
  "schema_ok": true,
  "safety_ok": true,
  "postcondition_ok": true,
  "audit_score": 0.9,
  "issues": ["Issue 1"],
  "recommendations": ["Recommendation 1"]
}}
"""


class SkillAuditorAgent:
    """对 Skill 进行 schema/安全/后置条件审计。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def audit(self, skill: Skill) -> AuditResult:
        """审计 Skill，返回审计结果。"""
        # 先做本地规则检查
        issues: List[str] = []
        recommendations: List[str] = []
        schema_ok = True
        safety_ok = True
        postcondition_ok = True
        quality_penalty = 0.0

        input_schema = skill.interface.input_schema or {}
        output_schema = skill.interface.output_schema or {}
        input_properties = input_schema.get("properties")
        output_properties = output_schema.get("properties")

        if input_schema.get("type") != "object" or not isinstance(input_properties, dict):
            issues.append("input_schema must be an object schema with a properties object")
            schema_ok = False
            recommendations.append("Set input_schema to {'type': 'object', 'properties': {...}}")
            input_properties = input_properties if isinstance(input_properties, dict) else {}
        if output_schema.get("type") != "object" or not isinstance(output_properties, dict):
            issues.append("output_schema must be an object schema with a properties object")
            schema_ok = False
            recommendations.append("Set output_schema to {'type': 'object', 'properties': {...}}")
            output_properties = output_properties if isinstance(output_properties, dict) else {}
        elif not output_properties:
            recommendations.append("Define output_schema.properties so downstream agents know what this Skill returns")
            quality_penalty += 0.05

        required = input_schema.get("required", [])
        if required is None:
            required = []
        if not isinstance(required, list):
            issues.append("input_schema.required must be a list when present")
            recommendations.append("Use a list of property names for input_schema.required")
            schema_ok = False
            required = []
        for field_name in required:
            if field_name not in input_properties:
                issues.append(f"required field '{field_name}' is missing from input_schema.properties")
                recommendations.append(f"Add '{field_name}' to input_schema.properties or remove it from required")
                schema_ok = False

        if not re.match(r"^[a-z][a-z0-9_]*$", skill.name):
            issues.append(f"skill name '{skill.name}' must be snake_case and start with a lowercase letter")
            recommendations.append("Rename the Skill using snake_case, for example 'click_submit_button'")
            schema_ok = False

        if len(str(skill.description or "").strip()) < 16:
            recommendations.append("Expand the Skill description so it explains reusable behavior, not only a task name")
            quality_penalty += 0.05

        impl = skill.implementation
        if impl is None:
            issues.append("skill implementation is missing")
            recommendations.append("Provide a prompt_template, code implementation, or sub_skill_ids")
            postcondition_ok = False
        elif impl.prompt_template is not None and not impl.prompt_template.strip():
            issues.append("prompt_template is empty")
            recommendations.append("Remove the blank prompt_template or replace it with an executable prompt")
            postcondition_ok = False

        skill_type = skill.skill_type.value
        if skill_type == "strategic" and skill.meta_category is None:
            issues.append("strategic Skill must define meta_category")
            recommendations.append("Set meta_category for strategic Skills so downstream routing can classify it")
            postcondition_ok = False
        if skill_type in {"functional", "strategic"} and impl and not impl.sub_skill_ids:
            recommendations.append(
                "Functional or strategic Skills should document composition intent or reference sub_skill_ids"
            )
            quality_penalty += 0.05

        if impl and impl.code:
            dangerous = ["os.system", "subprocess", "eval(", "exec(", "__import__", "open("]
            for d in dangerous:
                if d in impl.code:
                    issues.append(f"code contains potentially dangerous operation: {d}")
                    recommendations.append("Remove dangerous operations or wrap them in a reviewed safe tool")
                    safety_ok = False
        if impl and impl.prompt_template and impl.prompt_template.strip():
            prompt_vars = _extract_prompt_variables(impl.prompt_template)
            for var_name in sorted(prompt_vars):
                if var_name not in input_properties:
                    issues.append(
                        f"prompt_template variable '{var_name}' is missing from input_schema.properties"
                    )
                    recommendations.append(
                        f"Add '{var_name}' to input_schema.properties or remove it from prompt_template"
                    )
                    schema_ok = False

        # LLM 深度审计
        try:
            impl_str = ""
            if impl:
                if impl.prompt_template:
                    impl_str = f"prompt_template: {impl.prompt_template[:100]}"
                elif impl.code:
                    impl_str = f"code: {impl.code[:100]}"
                elif impl.sub_skill_ids:
                    impl_str = f"sub_skills: {impl.sub_skill_ids}"

            prompt = _AUDIT_PROMPT.format(
                name=skill.name,
                description=skill.description,
                skill_type=skill.skill_type.value,
                tags=skill.tags,
                input_schema=json.dumps(skill.interface.input_schema, ensure_ascii=False)[:200],
                output_schema=json.dumps(skill.interface.output_schema, ensure_ascii=False)[:200],
                implementation=impl_str or "(no implementation)",
            )
            response = self._llm.chat([
                Message.system("You are the SkillOS Skill Auditor Agent. Return JSON only."),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                llm_issues = _string_list(data.get("issues"))
                llm_recommendations = _string_list(data.get("recommendations"))
                issues.extend(llm_issues)
                recommendations.extend(llm_recommendations)
                schema_ok = bool(data.get("schema_ok", schema_ok)) and schema_ok
                safety_ok = bool(data.get("safety_ok", safety_ok)) and safety_ok
                postcondition_ok = bool(data.get("postcondition_ok", postcondition_ok))
                local_score = _score_audit(
                    schema_ok=schema_ok,
                    safety_ok=safety_ok,
                    postcondition_ok=postcondition_ok,
                    issue_count=len(issues),
                    quality_penalty=quality_penalty,
                )
                audit_score = min(_clamp_float(data.get("audit_score"), default=0.8), local_score)
                passed = (
                    bool(data.get("passed", True))
                    and schema_ok
                    and safety_ok
                    and postcondition_ok
                    and audit_score >= 0.6
                    and not issues
                )
                return AuditResult(
                    skill_id=skill.skill_id,
                    skill_name=skill.name,
                    passed=passed,
                    schema_ok=schema_ok,
                    safety_ok=safety_ok,
                    postcondition_ok=postcondition_ok,
                    issues=issues,
                    recommendations=recommendations,
                    audit_score=audit_score,
                )
        except Exception as exc:
            logger.warning("Auditor LLM call failed: %s", exc)

        passed = schema_ok and safety_ok and postcondition_ok and len(issues) == 0
        return AuditResult(
            skill_id=skill.skill_id,
            skill_name=skill.name,
            passed=passed,
            schema_ok=schema_ok,
            safety_ok=safety_ok,
            postcondition_ok=postcondition_ok,
            issues=issues,
            recommendations=recommendations,
            audit_score=_score_audit(
                schema_ok=schema_ok,
                safety_ok=safety_ok,
                postcondition_ok=postcondition_ok,
                issue_count=len(issues),
                quality_penalty=quality_penalty,
            ),
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


def _extract_prompt_variables(prompt_template: str) -> set[str]:
    """Return field names used as `{name}` or `{{name}}` in a prompt template."""
    variables: set[str] = set()
    for match in re.finditer(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", prompt_template):
        variables.add(match.group(1))
    for match in re.finditer(r"(?<!\{)\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}(?!\})", prompt_template):
        variables.add(match.group(1))
    return variables


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _score_audit(
    *,
    schema_ok: bool,
    safety_ok: bool,
    postcondition_ok: bool,
    issue_count: int,
    quality_penalty: float,
) -> float:
    score = 1.0
    if not schema_ok:
        score -= 0.35
    if not safety_ok:
        score -= 0.45
    if not postcondition_ok:
        score -= 0.25
    score -= min(0.25, issue_count * 0.05)
    score -= quality_penalty
    return max(0.0, min(1.0, score))
