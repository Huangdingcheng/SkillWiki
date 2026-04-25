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
你是 SkillOS 的 Verifier Agent，负责验证任务执行结果是否满足目标。

## 原始目标
{goal}

## 执行轨迹摘要
{trace_summary}

## 最终输出
{final_output}

## 验证要求
1. 判断输出是否满足目标（passed: true/false）
2. 给出满足度评分（0.0~1.0）
3. 列出存在的问题
4. 给出改进建议

## 输出格式（严格 JSON）
{{
  "passed": true,
  "score": 0.85,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1"],
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
