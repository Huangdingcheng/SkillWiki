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
以下 Skill 在运行时出现了问题，请分析原因并提供修复方案。

## Skill 信息
名称: {name}
描述: {description}
类型: {skill_type}
当前实现:
{implementation}

## 健康报告
成功率: {success_rate:.1%}
使用次数: {usage_count}
平均延迟: {avg_latency_ms:.0f}ms
问题列表:
{issues}

## 失败案例（最近 5 次）
{failure_cases}

## 修复任务
1. 分析失败原因
2. 提供修复后的实现（code 或 prompt_template）
3. 更新前置/后置条件（如有必要）
4. 添加错误处理逻辑

## 输出格式（严格 JSON）
{{
  "root_cause": "失败根本原因分析",
  "fix_type": "code_fix|prompt_fix|interface_fix|deprecate",
  "fixed_implementation": {{
    "language": "python",
    "code": "修复后的代码（如适用）",
    "prompt_template": "修复后的 prompt（如适用）",
    "tool_calls": [],
    "sub_skill_ids": []
  }},
  "updated_preconditions": [],
  "updated_postconditions": [],
  "confidence": 0.8,
  "repair_notes": "修复说明"
}}

如果 fix_type 为 deprecate，说明该 Skill 无法修复，应该废弃。
只输出 JSON，不要其他内容。
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

        impl_info = "无实现"
        if skill.implementation:
            impl = skill.implementation
            if impl.code:
                impl_info = f"```python\n{impl.code[:800]}\n```"
            elif impl.prompt_template:
                impl_info = f"Prompt: {impl.prompt_template[:400]}"
            elif impl.sub_skill_ids:
                impl_info = f"组合子 Skill: {impl.sub_skill_ids}"

        failure_summary = "无失败案例记录"
        if failure_cases:
            lines = []
            for i, case in enumerate(failure_cases[:5]):
                lines.append(
                    f"{i+1}. 输入: {json.dumps(case.get('input', {}), ensure_ascii=False)[:100]}\n"
                    f"   错误: {case.get('error', '未知')}"
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

        response = self._llm.chat([
            Message.system(
                "你是 SkillOS 的 Skill 修复专家，擅长分析 Skill 失败原因并提供修复方案。"
                "严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])

        data = self._extract_json(response.content)
        if not data:
            result.error = "LLM 返回无效响应"
            return result

        result.root_cause = data.get("root_cause", "")
        result.fix_type = data.get("fix_type", "")
        result.confidence = data.get("confidence", 0.5)
        result.repair_notes = data.get("repair_notes", "")

        if result.fix_type == "deprecate":
            result.should_deprecate = True
            result.success = True
            logger.info(f"Skill 建议废弃: {skill.name} - {result.root_cause}")
            return result

        # 应用修复
        try:
            repaired = skill.model_copy(deep=True)
            repaired.bump_version("patch")
            repaired.state = SkillState.DRAFT  # 修复后回到 Draft 重新验证

            impl_data = data.get("fixed_implementation", {})
            if impl_data:
                repaired.implementation = SkillImplementation(
                    language=impl_data.get("language", "python"),
                    code=impl_data.get("code"),
                    prompt_template=impl_data.get("prompt_template"),
                    tool_calls=impl_data.get("tool_calls", []),
                    sub_skill_ids=impl_data.get("sub_skill_ids", []),
                )

            if data.get("updated_preconditions"):
                repaired.interface.preconditions = data["updated_preconditions"]
            if data.get("updated_postconditions"):
                repaired.interface.postconditions = data["updated_postconditions"]

            result.repaired_skill = repaired
            result.success = True
            logger.info(
                f"Skill 修复成功: {skill.name} v{skill.version} → v{repaired.version} "
                f"(置信度={result.confidence:.2f})"
            )
        except Exception as e:
            result.error = str(e)
            logger.error(f"Skill 修复失败: {skill.name} - {e}")

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
