"""Reflection Agent — 分析执行失败/成功，生成反馈用于 Skill 演化。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Feedback:
    task_id: str
    goal: str
    success: bool
    root_cause: str = ""
    failed_skill_ids: List[str] = field(default_factory=list)
    improvement_suggestions: List[str] = field(default_factory=list)
    skill_update_proposals: List[Dict[str, Any]] = field(default_factory=list)
    experience_summary: str = ""


_REFLECT_PROMPT = """
你是 SkillOS 的 Reflection Agent，负责分析任务执行结果并生成改进反馈。

## 任务目标
{goal}

## 执行结果
成功: {success}

## 执行轨迹
{trace}

## 验证结果
{verification}

## 分析要求
1. 找出失败根因（如果失败）
2. 识别哪些 Skill 需要改进
3. 提出具体的 Skill 更新建议
4. 总结本次执行的经验

## 输出格式（严格 JSON）
{{
  "root_cause": "失败根因描述",
  "failed_skill_ids": ["skill_id_1"],
  "improvement_suggestions": ["建议1", "建议2"],
  "skill_update_proposals": [
    {{
      "skill_id": "skill_id",
      "issue": "问题描述",
      "proposed_fix": "修复方案"
    }}
  ],
  "experience_summary": "本次执行经验总结"
}}

只输出 JSON。
"""


class ReflectionAgent:
    """分析执行轨迹，生成 Skill 改进反馈。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def reflect(
        self,
        task_id: str,
        goal: str,
        trace: Dict[str, Any],
        verification_result: Optional[Any] = None,
    ) -> Feedback:
        """分析执行结果，生成反馈。"""
        success = verification_result.passed if verification_result else bool(trace)
        trace_str = json.dumps(trace, ensure_ascii=False, indent=2)[:600]
        verify_str = (
            f"通过={verification_result.passed}, 评分={verification_result.score:.2f}, "
            f"问题={verification_result.issues}"
            if verification_result else "（无验证结果）"
        )

        prompt = _REFLECT_PROMPT.format(
            goal=goal,
            success=success,
            trace=trace_str,
            verification=verify_str,
        )

        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Reflection Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                return Feedback(
                    task_id=task_id,
                    goal=goal,
                    success=success,
                    root_cause=data.get("root_cause", ""),
                    failed_skill_ids=data.get("failed_skill_ids", []),
                    improvement_suggestions=data.get("improvement_suggestions", []),
                    skill_update_proposals=data.get("skill_update_proposals", []),
                    experience_summary=data.get("experience_summary", ""),
                )
        except Exception as exc:
            logger.warning(f"Reflection LLM 调用失败: {exc}")

        return Feedback(
            task_id=task_id,
            goal=goal,
            success=success,
            root_cause="LLM 反思调用失败" if not success else "",
            experience_summary=f"任务{'成功' if success else '失败'}完成",
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
