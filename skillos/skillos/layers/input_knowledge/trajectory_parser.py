"""轨迹解析器 — 从操作轨迹（JSON/文本）中提取 ExperienceUnit。

支持格式：
- Playwright/Puppeteer 录制的 JSON 轨迹
- 浏览器扩展导出的操作序列
- 自然语言描述的操作步骤
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from ...models.experience_model import (
    ExperienceSourceType,
    ExperienceUnit,
    TrajectoryStep,
)
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from .base_parser import BaseParser, ParseResult

logger = get_logger(__name__)

# LLM 提取轨迹的 Prompt 模板
_TRAJECTORY_EXTRACT_PROMPT = """
请分析以下操作轨迹，提取结构化的经验单元。

## 输入轨迹
{trajectory}

## 任务
1. 识别这段轨迹完成的整体任务（task_description）
2. 将轨迹分解为有意义的操作步骤（steps）
3. 识别每个步骤的：action_type, action_target, action_value, state_before, state_after
4. 判断领域（domain）：web / file / api / code / system / other
5. 提取相关标签（tags）

## 输出格式（严格 JSON）
{{
  "task_description": "...",
  "domain": "web",
  "tags": ["tag1", "tag2"],
  "steps": [
    {{
      "step_index": 0,
      "action_type": "navigate",
      "action_target": "https://example.com",
      "action_value": null,
      "state_before": {{}},
      "state_after": {{"url": "https://example.com"}},
      "success": true
    }}
  ]
}}

只输出 JSON，不要其他内容。
"""

# 轨迹分段的 Prompt（长轨迹拆分为多个 ExperienceUnit）
_TRAJECTORY_SEGMENT_PROMPT = """
以下是一段较长的操作轨迹，请将其分割为 2-5 个独立的、有意义的子任务段落。
每个段落应该是一个完整的、可复用的操作序列。

## 轨迹
{trajectory}

## 输出格式（严格 JSON）
{{
  "segments": [
    {{
      "title": "段落标题",
      "description": "段落描述",
      "start_step": 0,
      "end_step": 5,
      "task_description": "完成的子任务"
    }}
  ]
}}

只输出 JSON，不要其他内容。
"""


class TrajectoryParser(BaseParser):
    """操作轨迹解析器。

    支持三种输入模式：
    1. JSON 格式轨迹（Playwright/Puppeteer 录制）
    2. 自然语言描述的操作步骤
    3. 混合格式（自动检测）
    """

    def __init__(
        self,
        llm_client: LLMClient,
        auto_segment: bool = True,
        segment_threshold: int = 20,  # 超过此步骤数时自动分段
    ) -> None:
        super().__init__(llm_client)
        self._auto_segment = auto_segment
        self._segment_threshold = segment_threshold

    def _build_system_prompt(self) -> str:
        return (
            "你是 SkillOS 的轨迹分析专家，擅长从操作轨迹中识别可复用的操作模式。"
            "请精确提取每个操作步骤的类型、目标和状态变化。"
            "严格按照 JSON 格式输出，确保 JSON 合法。"
        )

    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        """解析轨迹输入。

        Args:
            raw_input: 轨迹内容（JSON 字符串或自然语言）
            **kwargs: title（可选标题）, domain（可选领域）
        """
        result = ParseResult(raw_input=raw_input)

        # 1. 尝试直接解析 JSON 格式轨迹
        json_steps = self._try_parse_json_trajectory(raw_input)
        if json_steps is not None:
            unit = self._build_unit_from_json(json_steps, kwargs)
            result.experience_units.append(unit)
            result.metadata["parse_mode"] = "json_direct"
            logger.info(f"JSON 轨迹直接解析: {len(json_steps)} 步骤")
            return result

        # 2. 使用 LLM 解析
        try:
            llm_result = await self._llm_parse_trajectory(raw_input, kwargs)
            if llm_result:
                # 长轨迹自动分段
                if (
                    self._auto_segment
                    and len(llm_result.get("steps", [])) > self._segment_threshold
                ):
                    units = await self._segment_trajectory(raw_input, llm_result)
                    result.experience_units.extend(units)
                    result.metadata["parse_mode"] = "llm_segmented"
                else:
                    unit = self._build_unit_from_llm(llm_result, kwargs)
                    result.experience_units.append(unit)
                    result.metadata["parse_mode"] = "llm_single"
        except Exception as e:
            logger.error(f"轨迹解析失败: {e}")
            result.errors.append(str(e))
            # 降级：创建最小 ExperienceUnit
            unit = ExperienceUnit(
                source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
                raw_content=raw_input,
                raw_content_format="text",
                task_description=kwargs.get("title", "未知任务"),
                domain=kwargs.get("domain", "general"),
            )
            result.experience_units.append(unit)

        return result

    def _try_parse_json_trajectory(
        self, raw_input: str
    ) -> Optional[List[Dict[str, Any]]]:
        """尝试将输入解析为 JSON 步骤列表。"""
        raw_input = raw_input.strip()
        if not raw_input.startswith(("[", "{")):
            return None
        try:
            data = json.loads(raw_input)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "steps" in data:
                return data["steps"]
            if isinstance(data, dict) and "actions" in data:
                return data["actions"]
        except json.JSONDecodeError:
            pass
        return None

    def _build_unit_from_json(
        self,
        json_steps: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
    ) -> ExperienceUnit:
        """从 JSON 步骤列表构建 ExperienceUnit。"""
        steps = []
        for i, step_data in enumerate(json_steps):
            step = TrajectoryStep(
                step_index=i,
                action_type=step_data.get("type", step_data.get("action", "unknown")),
                action_target=step_data.get("selector", step_data.get("target")),
                action_value=step_data.get("value", step_data.get("text")),
                state_before=step_data.get("state_before", {}),
                state_after=step_data.get("state_after", {}),
                success=step_data.get("success", True),
                duration_ms=step_data.get("duration"),
            )
            steps.append(step)

        return ExperienceUnit(
            source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
            title=kwargs.get("title", f"轨迹 ({len(steps)} 步骤)"),
            steps=steps,
            raw_content=json.dumps(json_steps, ensure_ascii=False),
            raw_content_format="json",
            task_description=kwargs.get("task_description"),
            domain=kwargs.get("domain", "web"),
            tags=kwargs.get("tags", ["web", "trajectory"]),
        )

    async def _llm_parse_trajectory(
        self,
        raw_input: str,
        kwargs: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """使用 LLM 解析轨迹。"""
        prompt = _TRAJECTORY_EXTRACT_PROMPT.format(trajectory=raw_input[:8000])
        response_text = await self._call_llm(prompt, self._build_system_prompt())
        return self._extract_json(response_text)

    async def _segment_trajectory(
        self,
        raw_input: str,
        full_result: Dict[str, Any],
    ) -> List[ExperienceUnit]:
        """将长轨迹分割为多个 ExperienceUnit。"""
        prompt = _TRAJECTORY_SEGMENT_PROMPT.format(trajectory=raw_input[:8000])
        response_text = await self._call_llm(prompt, self._build_system_prompt())
        seg_data = self._extract_json(response_text)

        if not seg_data or "segments" not in seg_data:
            # 分段失败，返回整体
            return [self._build_unit_from_llm(full_result, {})]

        all_steps = full_result.get("steps", [])
        units = []
        for seg in seg_data["segments"]:
            start = seg.get("start_step", 0)
            end = seg.get("end_step", len(all_steps))
            seg_steps_data = all_steps[start : end + 1]

            steps = [
                TrajectoryStep(
                    step_index=i,
                    action_type=s.get("action_type", "unknown"),
                    action_target=s.get("action_target"),
                    action_value=s.get("action_value"),
                    state_before=s.get("state_before", {}),
                    state_after=s.get("state_after", {}),
                    success=s.get("success", True),
                )
                for i, s in enumerate(seg_steps_data)
            ]

            unit = ExperienceUnit(
                source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
                title=seg.get("title", f"轨迹段 {start}-{end}"),
                description=seg.get("description", ""),
                steps=steps,
                task_description=seg.get("task_description"),
                domain=full_result.get("domain", "web"),
                tags=full_result.get("tags", []),
            )
            units.append(unit)

        return units

    def _build_unit_from_llm(
        self,
        llm_result: Dict[str, Any],
        kwargs: Dict[str, Any],
    ) -> ExperienceUnit:
        """从 LLM 解析结果构建 ExperienceUnit。"""
        steps = [
            TrajectoryStep(
                step_index=i,
                action_type=s.get("action_type", "unknown"),
                action_target=s.get("action_target"),
                action_value=s.get("action_value"),
                state_before=s.get("state_before", {}),
                state_after=s.get("state_after", {}),
                success=s.get("success", True),
            )
            for i, s in enumerate(llm_result.get("steps", []))
        ]

        return ExperienceUnit(
            source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
            title=kwargs.get("title", llm_result.get("task_description", "轨迹")[:64]),
            steps=steps,
            raw_content=kwargs.get("raw_input", ""),
            raw_content_format="text",
            task_description=llm_result.get("task_description"),
            domain=llm_result.get("domain", kwargs.get("domain", "web")),
            tags=llm_result.get("tags", []),
        )

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从 LLM 响应中提取 JSON 对象。"""
        # 尝试直接解析
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 提取 ```json ... ``` 代码块
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # 提取第一个 { ... } 块
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning(f"无法从 LLM 响应中提取 JSON: {text[:200]}")
        return None
