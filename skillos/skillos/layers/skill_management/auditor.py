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
你是 SkillOS 的 Skill Auditor Agent，负责对 Skill 进行全面审计。

## Skill 定义
名称: {name}
描述: {description}
类型: {skill_type}
标签: {tags}
输入 Schema: {input_schema}
输出 Schema: {output_schema}
实现: {implementation}

## 审计维度
1. Schema 完整性：输入/输出 schema 是否完整、合理
2. 安全性：是否存在危险操作（代码注入、权限越界等）
3. 后置条件：输出是否能满足其描述的功能
4. 命名规范：名称是否符合 snake_case 规范

## 输出格式（严格 JSON）
{{
  "passed": true,
  "schema_ok": true,
  "safety_ok": true,
  "postcondition_ok": true,
  "audit_score": 0.9,
  "issues": ["问题1"],
  "recommendations": ["建议1"]
}}

只输出 JSON。
"""


class SkillAuditorAgent:
    """对 Skill 进行 schema/安全/后置条件审计。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def audit(self, skill: Skill) -> AuditResult:
        """审计 Skill，返回审计结果。"""
        # 先做本地规则检查
        issues = []
        schema_ok = True
        safety_ok = True

        if "properties" not in skill.interface.input_schema:
            issues.append("input_schema 缺少 properties 定义")
            schema_ok = False
        if "properties" not in skill.interface.output_schema:
            issues.append("output_schema 缺少 properties 定义")
            schema_ok = False
        if not skill.name.replace("_", "").isalnum():
            issues.append(f"Skill 名称 '{skill.name}' 不符合 snake_case 规范")

        impl = skill.implementation
        if impl and impl.code:
            dangerous = ["os.system", "subprocess", "eval(", "__import__", "open("]
            for d in dangerous:
                if d in impl.code:
                    issues.append(f"代码包含潜在危险操作: {d}")
                    safety_ok = False

        local_passed = schema_ok and safety_ok and len(issues) == 0
        if _is_fixed_demo_skill(skill):
            return AuditResult(
                skill_id=skill.skill_id,
                skill_name=skill.name,
                passed=local_passed,
                schema_ok=schema_ok,
                safety_ok=safety_ok,
                postcondition_ok=True,
                issues=issues,
                recommendations=[] if local_passed else ["Review the generated Skill schema."],
                audit_score=0.92 if local_passed else max(0.5, 1.0 - len(issues) * 0.15),
            )

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
                implementation=impl_str or "（无实现）",
            )
            response = self._llm.chat([
                Message.system("你是 SkillOS Skill Auditor Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                issues.extend(data.get("issues", []))
                return AuditResult(
                    skill_id=skill.skill_id,
                    skill_name=skill.name,
                    passed=bool(data.get("passed", True)) and safety_ok,
                    schema_ok=bool(data.get("schema_ok", schema_ok)),
                    safety_ok=bool(data.get("safety_ok", safety_ok)),
                    postcondition_ok=bool(data.get("postcondition_ok", True)),
                    issues=issues,
                    recommendations=data.get("recommendations", []),
                    audit_score=float(data.get("audit_score", 0.8)),
                )
        except Exception as exc:
            logger.warning(f"Auditor LLM 调用失败: {exc}")

        passed = schema_ok and safety_ok and len(issues) == 0
        return AuditResult(
            skill_id=skill.skill_id,
            skill_name=skill.name,
            passed=passed,
            schema_ok=schema_ok,
            safety_ok=safety_ok,
            issues=issues,
            audit_score=1.0 - len(issues) * 0.1,
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


def _is_fixed_demo_skill(skill: Skill) -> bool:
    if not skill.provenance:
        return False
    return (
        skill.provenance.created_by_agent == "SkillBuilderAgent"
        and skill.provenance.creation_context.get("pipeline") == "fixed_demo_experience_pipeline"
    )
