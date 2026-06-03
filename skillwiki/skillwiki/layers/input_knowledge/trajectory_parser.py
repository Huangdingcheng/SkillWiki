"""Trajectory parser for browser/action traces."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceSourceType, ExperienceUnit, TrajectoryStep
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from .base_parser import BaseParser, ParseResult

logger = get_logger(__name__)

_TRAJECTORY_EXTRACT_PROMPT = """
Analyze the following operation trajectory and extract one structured experience unit.

Trajectory:
{trajectory}

Return only valid JSON with this shape:
{{
  "task_description": "overall task completed by the trajectory",
  "domain": "web",
  "tags": ["web", "login"],
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

Rules:
- domain must be one of web, file, api, code, system, other.
- Use short, stable action_type values such as navigate, click, type, wait, extract, call_api.
- Return JSON only.
"""

_TRAJECTORY_SEGMENT_PROMPT = """
Split this long operation trajectory into 2-5 independent reusable subtask segments.

Trajectory:
{trajectory}

Return only valid JSON with this shape:
{{
  "segments": [
    {{
      "title": "segment_title",
      "description": "what this segment does",
      "start_step": 0,
      "end_step": 5,
      "task_description": "subtask completed by this segment"
    }}
  ]
}}
"""


class TrajectoryParser(BaseParser):
    """Parse JSON or natural-language operation trajectories."""

    def __init__(
        self,
        llm_client: LLMClient,
        auto_segment: bool = True,
        segment_threshold: int = 20,
    ) -> None:
        super().__init__(llm_client)
        self._auto_segment = auto_segment
        self._segment_threshold = segment_threshold

    def _build_system_prompt(self) -> str:
        return (
            "You are the SkillWiki trajectory analysis expert. "
            "Extract reusable operation patterns from action traces. "
            "Return valid JSON only."
        )

    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        result = ParseResult(raw_input=raw_input)

        json_steps = self._try_parse_json_trajectory(raw_input)
        if json_steps is not None:
            unit = self._build_unit_from_json(json_steps, kwargs)
            result.experience_units.append(unit)
            result.metadata["parse_mode"] = "json_direct"
            logger.info("JSON trajectory parsed directly: %s steps", len(json_steps))
            return result

        try:
            llm_result = await self._llm_parse_trajectory(raw_input)
            if llm_result:
                if (
                    self._auto_segment
                    and len(llm_result.get("steps", [])) > self._segment_threshold
                ):
                    units = await self._segment_trajectory(raw_input, llm_result)
                    result.experience_units.extend(units)
                    result.metadata["parse_mode"] = "llm_segmented"
                else:
                    unit = self._build_unit_from_llm(llm_result, kwargs, raw_input)
                    result.experience_units.append(unit)
                    result.metadata["parse_mode"] = "llm_single"
        except Exception as exc:
            logger.error("Trajectory parsing failed: %s", exc)
            result.errors.append(str(exc))
            result.experience_units.append(
                ExperienceUnit(
                    source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
                    raw_content=raw_input,
                    raw_content_format="text",
                    task_description=kwargs.get("title", "Unknown task"),
                    domain=kwargs.get("domain", "general"),
                )
            )

        return result

    def _try_parse_json_trajectory(self, raw_input: str) -> Optional[List[Dict[str, Any]]]:
        raw_input = raw_input.strip()
        if not raw_input.startswith(("[", "{")):
            return None
        try:
            data = json.loads(raw_input)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("steps"), list):
                return data["steps"]
            if isinstance(data, dict) and isinstance(data.get("actions"), list):
                return data["actions"]
        except json.JSONDecodeError:
            pass
        return None

    def _build_unit_from_json(
        self,
        json_steps: List[Dict[str, Any]],
        kwargs: Dict[str, Any],
    ) -> ExperienceUnit:
        steps = []
        for index, step_data in enumerate(json_steps):
            steps.append(
                TrajectoryStep(
                    step_index=index,
                    action_type=step_data.get("type", step_data.get("action", "unknown")),
                    action_target=step_data.get("selector", step_data.get("target")),
                    action_value=step_data.get("value", step_data.get("text")),
                    state_before=step_data.get("state_before", {}),
                    state_after=step_data.get("state_after", {}),
                    success=step_data.get("success", True),
                    duration_ms=step_data.get("duration"),
                )
            )

        return ExperienceUnit(
            source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
            title=kwargs.get("title", f"Trajectory ({len(steps)} steps)"),
            steps=steps,
            raw_content=json.dumps(json_steps, ensure_ascii=False),
            raw_content_format="json",
            task_description=kwargs.get("task_description"),
            domain=kwargs.get("domain", "web"),
            tags=kwargs.get("tags", ["web", "trajectory"]),
        )

    async def _llm_parse_trajectory(self, raw_input: str) -> Optional[Dict[str, Any]]:
        prompt = _TRAJECTORY_EXTRACT_PROMPT.format(trajectory=raw_input[:8000])
        response_text = await self._call_llm(prompt, self._build_system_prompt())
        return self._extract_json(response_text)

    async def _segment_trajectory(
        self,
        raw_input: str,
        full_result: Dict[str, Any],
    ) -> List[ExperienceUnit]:
        prompt = _TRAJECTORY_SEGMENT_PROMPT.format(trajectory=raw_input[:8000])
        response_text = await self._call_llm(prompt, self._build_system_prompt())
        segment_data = self._extract_json(response_text)

        if not segment_data or "segments" not in segment_data:
            return [self._build_unit_from_llm(full_result, {}, raw_input)]

        all_steps = full_result.get("steps", [])
        units = []
        for segment in segment_data["segments"]:
            start = int(segment.get("start_step", 0))
            end = int(segment.get("end_step", len(all_steps)))
            segment_steps = all_steps[start : end + 1]
            steps = [
                TrajectoryStep(
                    step_index=index,
                    action_type=step.get("action_type", "unknown"),
                    action_target=step.get("action_target"),
                    action_value=step.get("action_value"),
                    state_before=step.get("state_before", {}),
                    state_after=step.get("state_after", {}),
                    success=step.get("success", True),
                )
                for index, step in enumerate(segment_steps)
            ]
            units.append(
                ExperienceUnit(
                    source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
                    title=segment.get("title", f"Trajectory segment {start}-{end}"),
                    description=segment.get("description", ""),
                    steps=steps,
                    raw_content=raw_input,
                    raw_content_format="text",
                    task_description=segment.get("task_description"),
                    domain=full_result.get("domain", "web"),
                    tags=full_result.get("tags", []),
                )
            )

        return units

    def _build_unit_from_llm(
        self,
        llm_result: Dict[str, Any],
        kwargs: Dict[str, Any],
        raw_input: str = "",
    ) -> ExperienceUnit:
        steps = [
            TrajectoryStep(
                step_index=index,
                action_type=step.get("action_type", "unknown"),
                action_target=step.get("action_target"),
                action_value=step.get("action_value"),
                state_before=step.get("state_before", {}),
                state_after=step.get("state_after", {}),
                success=step.get("success", True),
            )
            for index, step in enumerate(llm_result.get("steps", []))
        ]

        return ExperienceUnit(
            source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
            title=kwargs.get("title", llm_result.get("task_description", "Trajectory")[:64]),
            steps=steps,
            raw_content=raw_input,
            raw_content_format="text",
            task_description=llm_result.get("task_description"),
            domain=llm_result.get("domain", kwargs.get("domain", "web")),
            tags=llm_result.get("tags", []),
        )

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

        logger.warning("Unable to extract JSON from LLM response: %s", text[:200])
        return None
