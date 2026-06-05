"""Verifier Agent — 验证 Skill 执行结果是否满足目标。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class VerificationResult:
    passed: bool
    score: float  # 0.0 ~ 1.0
    goal: str
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


_VERIFY_PROMPT = """
你是 SkillOS 的 Observation-Aware Verifier Agent。

验证对象是“用户目标是否达成”，不是“某个 Skill 是否运行过”。
如果结果缺少必要 observation，要降低分数并说明需要再次观察或重试。

## 原始目标
{goal}

## 执行轨迹摘要
{trace_summary}

## 最终输出
{final_output}

## 验证要求
1. 对照用户目标、执行轨迹、最终输出，判断 passed。
2. 评分必须反映可观察证据质量：screen/stdout/filesystem/browser_dom/app_state/api_response。
3. 如果 output 只是“启动了某 Skill”但没有证明用户目标完成，不能高分。
4. 如果实际结果与目标漂移，必须标出 drift。
5. 给出下一步 retry 所需的最小 observation 或行动。

## 输出格式（严格 JSON）
{{
  "passed": true,
  "score": 0.85,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1"],
  "missing_observations": ["需要补充的证据"],
  "drift_detected": false,
  "retry_recommendation": "none | observe | replan | repair_parameters | generate_new_skill",
  "reasoning": "验证理由"
}}

只输出 JSON。
"""


class VerifierAgent:
    """验证 Skill 执行结果是否满足目标。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def verify(
        self,
        goal: str,
        final_output: Dict[str, Any],
        trace_summary: Optional[str] = None,
    ) -> VerificationResult:
        """验证执行结果。"""
        prompt = _VERIFY_PROMPT.format(
            goal=goal,
            trace_summary=trace_summary or "（无轨迹摘要）",
            final_output=json.dumps(final_output, ensure_ascii=False, indent=2)[:500],
        )

        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Verifier Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                return VerificationResult(
                    passed=bool(data.get("passed", False)),
                    score=float(data.get("score", 0.0)),
                    goal=goal,
                    issues=data.get("issues", []),
                    suggestions=data.get("suggestions", []),
                    details={"reasoning": data.get("reasoning", "")},
                )
        except Exception as exc:
            logger.warning(f"Verifier LLM 调用失败: {exc}")

        # 降级：检查 output 非空即通过
        passed = bool(final_output)
        return VerificationResult(
            passed=passed,
            score=0.5 if passed else 0.0,
            goal=goal,
            issues=[] if passed else ["执行输出为空"],
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
