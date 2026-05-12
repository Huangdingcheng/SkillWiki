"""Skill 验证器 — 验证 Draft Skill 的正确性，推进到 S3 状态。

验证维度：
1. 接口规范完整性（JSON Schema 合法性）
2. 实现可执行性（代码语法检查）
3. 测试用例覆盖率
4. LLM 语义验证（逻辑一致性）
5. 粒度合理性检查
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ...models.skill_model import Skill, SkillState, SkillType
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from ...utils.validators import validate_skill_schema

logger = get_logger(__name__)

_SEMANTIC_VALIDATE_PROMPT = """
请验证以下 Skill 定义的语义正确性和完整性。

## Skill 定义
名称: {name}
描述: {description}
类型: {skill_type}
领域: {domain}
粒度: {granularity_level}

接口:
- 输入: {input_schema}
- 输出: {output_schema}
- 前置条件: {preconditions}
- 后置条件: {postconditions}

实现:
{implementation_summary}

测试用例数: {test_case_count}

## 验证维度
1. 名称是否准确反映功能？
2. 接口是否完整（输入/输出是否覆盖所有必要参数）？
3. 前置/后置条件是否合理？
4. 实现是否与接口一致？
5. 粒度级别是否合适（1=原子操作，5=高层策略）？
6. 是否存在明显的逻辑错误？

## 输出格式（严格 JSON）
{{
  "is_valid": true,
  "overall_score": 0.85,
  "issues": [
    {{"severity": "error|warning|info", "field": "interface.input_schema", "message": "问题描述"}}
  ],
  "suggestions": ["改进建议1"],
  "granularity_assessment": {{
    "suggested_level": 1,
    "reason": "判断理由"
  }}
}}

只输出 JSON，不要其他内容。
"""


@dataclass
class ValidationResult:
    """验证结果。"""
    is_valid: bool
    overall_score: float = 0.0
    issues: List[Dict[str, str]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    static_checks: Dict[str, bool] = field(default_factory=dict)
    semantic_score: float = 0.0

    @property
    def errors(self) -> List[Dict[str, str]]:
        return [i for i in self.issues if i.get("severity") == "error"]

    @property
    def warnings(self) -> List[Dict[str, str]]:
        return [i for i in self.issues if i.get("severity") == "warning"]

    def summary(self) -> str:
        lines = [
            f"验证结果: {'通过' if self.is_valid else '失败'}",
            f"综合评分: {self.overall_score:.2f}",
            f"错误: {len(self.errors)}, 警告: {len(self.warnings)}",
        ]
        for issue in self.issues:
            prefix = "❌" if issue["severity"] == "error" else "⚠️"
            lines.append(f"  {prefix} [{issue.get('field', '?')}] {issue['message']}")
        return "\n".join(lines)


class SkillValidator:
    """Skill 验证器，结合静态检查和 LLM 语义验证。"""

    def __init__(
        self,
        llm_client: LLMClient,
        min_score: float = 0.6,
        require_test_cases: bool = True,
        min_test_cases: int = 1,
    ) -> None:
        self._llm = llm_client
        self._min_score = min_score
        self._require_test_cases = require_test_cases
        self._min_test_cases = min_test_cases

    async def validate(self, skill: Skill) -> ValidationResult:
        """全面验证 Skill。

        Returns:
            ValidationResult，包含是否通过、评分和问题列表
        """
        issues: List[Dict[str, str]] = []
        static_checks: Dict[str, bool] = {}

        # 1. 静态检查
        static_issues, static_checks = self._static_checks(skill)
        issues.extend(static_issues)

        # 2. LLM 语义验证
        semantic_result = await self._semantic_validate(skill)
        semantic_score = semantic_result.get("overall_score", 0.5)
        issues.extend(semantic_result.get("issues", []))
        suggestions = semantic_result.get("suggestions", [])

        # 3. 粒度调整建议
        granularity = semantic_result.get("granularity_assessment", {})
        if granularity.get("suggested_level") and granularity["suggested_level"] != skill.granularity_level:
            issues.append({
                "severity": "warning",
                "field": "granularity_level",
                "message": (
                    f"建议粒度级别为 {granularity['suggested_level']}（当前 {skill.granularity_level}）: "
                    f"{granularity.get('reason', '')}"
                ),
            })

        # 4. 综合评分
        static_score = sum(static_checks.values()) / max(len(static_checks), 1)
        overall_score = static_score * 0.4 + semantic_score * 0.6

        # 5. 判断是否通过
        has_errors = any(i["severity"] == "error" for i in issues)
        is_valid = not has_errors and overall_score >= self._min_score

        return ValidationResult(
            is_valid=is_valid,
            overall_score=overall_score,
            issues=issues,
            suggestions=suggestions,
            static_checks=static_checks,
            semantic_score=semantic_score,
        )

    async def validate_and_advance(self, skill: Skill) -> Tuple[Skill, ValidationResult]:
        """验证并推进状态（通过则 DRAFT → VERIFIED）。"""
        result = await self.validate(skill)
        if result.is_valid:
            skill.transition_to(SkillState.VERIFIED)
            logger.info(f"Skill 验证通过: {skill.name} (评分={result.overall_score:.2f})")
        else:
            logger.warning(
                f"Skill 验证失败: {skill.name} (评分={result.overall_score:.2f}, "
                f"错误={len(result.errors)})"
            )
        return skill, result

    # ------------------------------------------------------------------
    # Static Checks
    # ------------------------------------------------------------------

    def _static_checks(
        self, skill: Skill
    ) -> Tuple[List[Dict[str, str]], Dict[str, bool]]:
        """静态规则检查（无需 LLM）。"""
        issues: List[Dict[str, str]] = []
        checks: Dict[str, bool] = {}

        # 名称检查
        checks["name_valid"] = bool(skill.name and len(skill.name) >= 3)
        if not checks["name_valid"]:
            issues.append({"severity": "error", "field": "name", "message": "名称过短或为空"})

        # 描述检查
        checks["description_present"] = len(skill.description) >= 10
        if not checks["description_present"]:
            issues.append({"severity": "warning", "field": "description", "message": "描述过短"})

        # 接口检查
        input_ok, input_errors = validate_skill_schema(skill.interface.input_schema)
        checks["input_schema_valid"] = input_ok
        for err in input_errors:
            issues.append({"severity": "error", "field": "interface.input_schema", "message": err})

        output_ok, output_errors = validate_skill_schema(skill.interface.output_schema)
        checks["output_schema_valid"] = output_ok
        for err in output_errors:
            issues.append({"severity": "warning", "field": "interface.output_schema", "message": err})

        # 前置/后置条件
        checks["has_preconditions"] = len(skill.interface.preconditions) > 0
        if not checks["has_preconditions"]:
            issues.append({"severity": "info", "field": "interface.preconditions", "message": "建议添加前置条件"})

        # 实现检查
        checks["has_implementation"] = skill.implementation is not None
        if not checks["has_implementation"]:
            issues.append({"severity": "error", "field": "implementation", "message": "缺少实现"})
        elif skill.implementation:
            impl = skill.implementation
            if impl.code:
                syntax_ok, syntax_err = self._check_python_syntax(impl.code)
                checks["code_syntax_valid"] = syntax_ok
                if not syntax_ok:
                    issues.append({
                        "severity": "error",
                        "field": "implementation.code",
                        "message": f"代码语法错误: {syntax_err}",
                    })
            # Functional Skill 必须有 sub_skill_ids
            if skill.skill_type == SkillType.FUNCTIONAL:
                checks["functional_has_subs"] = len(impl.sub_skill_ids) > 0
                if not checks["functional_has_subs"]:
                    issues.append({
                        "severity": "warning",
                        "field": "implementation.sub_skill_ids",
                        "message": "Composite Skill 建议指定子 Skill ID",
                    })

        # 测试用例检查
        checks["has_test_cases"] = len(skill.test_cases) >= self._min_test_cases
        if self._require_test_cases and not checks["has_test_cases"]:
            issues.append({
                "severity": "warning",
                "field": "test_cases",
                "message": f"建议至少提供 {self._min_test_cases} 个测试用例",
            })

        return issues, checks

    def _check_python_syntax(self, code: str) -> Tuple[bool, str]:
        """检查 Python 代码语法。"""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Semantic Validation
    # ------------------------------------------------------------------

    async def _semantic_validate(self, skill: Skill) -> Dict[str, Any]:
        """LLM 语义验证。"""
        impl_summary = "无实现"
        if skill.implementation:
            impl = skill.implementation
            if impl.code:
                impl_summary = f"代码实现 ({len(impl.code)} 字符)"
            elif impl.prompt_template:
                impl_summary = f"Prompt 模板: {impl.prompt_template[:100]}"
            elif impl.sub_skill_ids:
                impl_summary = f"组合 {len(impl.sub_skill_ids)} 个子 Skill"

        prompt = _SEMANTIC_VALIDATE_PROMPT.format(
            name=skill.name,
            description=skill.description,
            skill_type=skill.skill_type.value,
            domain=skill.domain,
            granularity_level=skill.granularity_level,
            input_schema=json.dumps(skill.interface.input_schema, ensure_ascii=False)[:500],
            output_schema=json.dumps(skill.interface.output_schema, ensure_ascii=False)[:300],
            preconditions=json.dumps(skill.interface.preconditions, ensure_ascii=False),
            postconditions=json.dumps(skill.interface.postconditions, ensure_ascii=False),
            implementation_summary=impl_summary,
            test_case_count=len(skill.test_cases),
        )

        from ...utils.llm_client import Message
        response = self._llm.chat([
            Message.system(
                "你是 SkillOS 的 Skill 质量审核专家。"
                "请客观评估 Skill 定义的质量，严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])

        result = self._extract_json(response.content)
        return result or {"overall_score": 0.5, "issues": [], "suggestions": []}

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        import re
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
