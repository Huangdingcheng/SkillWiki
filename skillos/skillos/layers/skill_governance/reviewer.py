"""Skill 审核器 — LLM 驱动的 Skill 质量审核和 PR 流程。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill, SkillState
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"
    AUTO_APPROVED = "auto_approved"


@dataclass
class ReviewComment:
    field: str
    severity: str   # error | warning | suggestion
    message: str
    suggestion: str = ""


@dataclass
class ReviewResult:
    review_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    skill_id: str = ""
    skill_version: str = ""
    status: ReviewStatus = ReviewStatus.PENDING
    overall_score: float = 0.0
    comments: List[ReviewComment] = field(default_factory=list)
    summary: str = ""
    reviewer: str = "llm_reviewer"
    reviewed_at: datetime = field(default_factory=datetime.utcnow)
    auto_fix_suggestions: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.status in (ReviewStatus.APPROVED, ReviewStatus.AUTO_APPROVED)

    @property
    def blocking_issues(self) -> List[ReviewComment]:
        return [c for c in self.comments if c.severity == "error"]


_REVIEW_PROMPT = """
请对以下 Skill 进行全面的质量审核，判断是否可以发布（VERIFIED → RELEASED）。

## Skill 详情
名称: {name}
版本: {version}
类型: {skill_type}
领域: {domain}
粒度: {granularity_level}
描述: {description}
标签: {tags}

接口规范:
- 输入 Schema: {input_schema}
- 输出 Schema: {output_schema}
- 前置条件: {preconditions}
- 后置条件: {postconditions}
- 副作用: {side_effects}

实现:
{implementation_info}

测试用例数: {test_count}
成功率: {success_rate:.1%}
使用次数: {usage_count}

## 审核维度（每项 0-10 分）
1. 命名规范性：名称是否准确、符合 snake_case 规范
2. 接口完整性：输入/输出 Schema 是否完整、类型正确
3. 描述质量：描述是否清晰、准确、完整
4. 实现合理性：实现方式是否合理、可执行
5. 测试覆盖：测试用例是否覆盖主要场景
6. 粒度合理性：粒度级别是否与实际功能匹配
7. 可复用性：是否具有足够的通用性和复用价值

## 输出格式（严格 JSON）
{{
  "overall_score": 8.5,
  "status": "approved",
  "dimension_scores": {{
    "naming": 9,
    "interface": 8,
    "description": 8,
    "implementation": 9,
    "testing": 7,
    "granularity": 8,
    "reusability": 9
  }},
  "comments": [
    {{
      "field": "interface.input_schema",
      "severity": "warning",
      "message": "建议添加参数验证规则",
      "suggestion": "在 properties 中添加 minLength/maxLength 等约束"
    }}
  ],
  "summary": "整体质量良好，接口设计清晰，建议补充参数验证",
  "auto_fix_suggestions": {{
    "description": "改进后的描述文本（如有）"
  }}
}}

status 取值: approved（通过）| rejected（拒绝）| needs_revision（需要修改）
只输出 JSON，不要其他内容。
"""


class SkillReviewer:
    """LLM 驱动的 Skill 审核器。

    实现类似 GitHub PR Review 的流程：
    - 自动审核（LLM）
    - 评分和评论
    - 自动修复建议
    - 审核通过后推进状态
    """

    def __init__(
        self,
        llm_client: LLMClient,
        auto_approve_threshold: float = 8.0,
        auto_reject_threshold: float = 4.0,
    ) -> None:
        self._llm = llm_client
        self._auto_approve_threshold = auto_approve_threshold
        self._auto_reject_threshold = auto_reject_threshold

    async def review(self, skill: Skill) -> ReviewResult:
        """对 Skill 进行全面审核。"""
        result = ReviewResult(skill_id=skill.skill_id, skill_version=skill.version)

        impl_info = "无实现"
        if skill.implementation:
            impl = skill.implementation
            host_tools = [tool for tool in impl.tool_calls if str(tool).startswith("host.")]
            if host_tools:
                impl_info = (
                    "Allowlisted host runtime tool implementation. "
                    f"Runtime tool_calls={host_tools}. "
                    "Do not judge this Skill by placeholder seed code length; judge whether the tool contract, "
                    "interface, description, side effects, and tests are coherent."
                )
                if impl.sub_skill_ids:
                    impl_info += f" Composes helper Skill IDs: {impl.sub_skill_ids}."
            elif impl.code:
                impl_info = f"Python 代码 ({len(impl.code)} 字符)"
            elif impl.prompt_template:
                impl_info = f"Prompt 模板: {impl.prompt_template[:150]}"
            elif impl.sub_skill_ids:
                impl_info = f"组合 {len(impl.sub_skill_ids)} 个子 Skill: {impl.sub_skill_ids}"

        prompt = _REVIEW_PROMPT.format(
            name=skill.name,
            version=skill.version,
            skill_type=skill.skill_type.value,
            domain=skill.domain,
            granularity_level=skill.granularity_level,
            description=skill.description,
            tags=skill.tags,
            input_schema=json.dumps(skill.interface.input_schema, ensure_ascii=False)[:1200],
            output_schema=json.dumps(skill.interface.output_schema, ensure_ascii=False)[:1200],
            preconditions=skill.interface.preconditions,
            postconditions=skill.interface.postconditions,
            side_effects=skill.interface.side_effects,
            implementation_info=impl_info,
            test_count=len(skill.test_cases),
            success_rate=skill.metrics.success_rate,
            usage_count=skill.metrics.usage_count,
        )

        response = self._llm.chat([
            Message.system(
                "你是 SkillOS 的高级 Skill 审核专家，负责把关 Skill 的发布质量。"
                "请客观、严格地评审，严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])

        data = self._extract_json(response.content)
        if not data:
            result.status = ReviewStatus.NEEDS_REVISION
            result.summary = "审核服务暂时不可用，请稍后重试"
            return result

        result.overall_score = data.get("overall_score", 5.0)
        result.summary = data.get("summary", "")
        result.auto_fix_suggestions = data.get("auto_fix_suggestions", {})

        for c in data.get("comments", []):
            result.comments.append(ReviewComment(
                field=c.get("field", "general"),
                severity=c.get("severity", "warning"),
                message=c.get("message", ""),
                suggestion=c.get("suggestion", ""),
            ))

        # 自动决策
        llm_status = data.get("status", "needs_revision")
        if result.overall_score >= self._auto_approve_threshold and not result.blocking_issues:
            result.status = ReviewStatus.AUTO_APPROVED
        elif result.overall_score <= self._auto_reject_threshold or len(result.blocking_issues) >= 3:
            result.status = ReviewStatus.REJECTED
        elif llm_status == "approved":
            result.status = ReviewStatus.APPROVED
        elif llm_status == "rejected":
            result.status = ReviewStatus.REJECTED
        else:
            result.status = ReviewStatus.NEEDS_REVISION

        logger.info(
            f"Skill 审核完成: {skill.name} v{skill.version} "
            f"[{result.status.value}] 评分={result.overall_score:.1f}"
        )
        return result

    async def review_and_release(self, skill: Skill) -> tuple[Skill, ReviewResult]:
        """审核并在通过时自动发布。"""
        result = await self.review(skill)
        if result.is_approved and skill.state == SkillState.VERIFIED:
            skill.transition_to(SkillState.RELEASED)
            logger.info(f"Skill 已自动发布: {skill.name} v{skill.version}")
        return skill, result

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
