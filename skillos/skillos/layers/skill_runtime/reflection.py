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
你是 SkillOS 的 Post-Task Learning Agent。

工作方式参考 SkillClaw + SkillOpt：
- 任务结束后再判断是否学习，而不是在执行中盲目改库。
- 如果成功，提炼可复用流程；如果失败，先定位 root cause。
- 对已有 Skill 只能提出 bounded update：最小 diff、泛化输入槽、验证 gate。
- 如果已有 Skill 与任务偏差太大，提出 new_skill_proposal，不要硬合并。
- final/immutable Skill 只能作为参考，不能提出修改。

## 任务目标
{goal}

## 执行结果
成功: {success}

## 执行轨迹
{trace}

## 验证结果
{verification}

## 分析要求
1. 找出失败根因或成功因素。
2. 判断本次任务是否产生 reusable capability。
3. 判断应更新已有 Skill、合并多个 Skill、生成新 Skill，还是不学习。
4. 更新建议必须泛化：不要把本次 URL/path/app/selector 硬编码为唯一目标。
5. 给出 validation_gate，只有再次验证通过才可写入 SkillWiki。

## 输出格式（严格 JSON）
{{
  "root_cause": "失败根因描述",
  "failed_skill_ids": ["skill_id_1"],
  "improvement_suggestions": ["建议1", "建议2"],
  "learning_decision": "update_existing | merge_existing | create_new | no_learning",
  "skill_update_proposals": [
    {{
      "skill_id": "skill_id",
      "issue": "问题描述",
      "proposed_fix": "最小修复方案",
      "bounded_diff": {{
        "interface_changes": [],
        "prompt_changes": [],
        "implementation_changes": []
      }},
      "generalized_inputs": ["target_url", "target_path"],
      "validation_gate": "接受更新前必须通过的验证"
    }}
  ],
  "new_skill_proposal": {{
    "name": "",
    "skill_type": "atomic|functional|strategic",
    "description": "",
    "generic_scope": "",
    "three_layer_decomposition": {{
      "high": [],
      "functional": [],
      "atomic": []
    }}
  }},
  "merge_proposal": {{
    "source_skill_ids": [],
    "merged_name": "",
    "why_merge": "",
    "parameterized_interface": {{}}
  }},
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
