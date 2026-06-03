"""Skill 规划器 — 将复杂任务分解为 Skill 执行计划。"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """执行计划中的单个步骤。"""
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    step_index: int = 0
    skill_id: str = ""
    skill_name: str = ""
    description: str = ""
    input_mapping: Dict[str, Any] = field(default_factory=dict)  # 参数映射
    depends_on: List[str] = field(default_factory=list)          # 依赖的 step_id
    status: StepStatus = StepStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def latency_ms(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds() * 1000
        return None


@dataclass
class ExecutionPlan:
    """完整的执行计划。"""
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    task_description: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.SUCCESS)

    @property
    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.FAILED)

    @property
    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.SUCCESS, StepStatus.SKIPPED) for s in self.steps)

    @property
    def has_failures(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self.steps)

    def get_ready_steps(self) -> List[PlanStep]:
        """返回所有依赖已满足、可以执行的步骤。"""
        completed_ids = {
            s.step_id for s in self.steps
            if s.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)
        }
        return [
            s for s in self.steps
            if s.status == StepStatus.PENDING
            and all(dep in completed_ids for dep in s.depends_on)
        ]

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task": self.task_description,
            "total": self.total_steps,
            "completed": self.completed_steps,
            "failed": self.failed_steps,
            "steps": [
                {
                    "index": s.step_index,
                    "skill": s.skill_name,
                    "status": s.status.value,
                    "latency_ms": s.latency_ms,
                }
                for s in self.steps
            ],
        }


_PLAN_PROMPT = """
Create a SkillOS execution plan for the task.

Task:
{task_description}

Current state:
{current_state}

Available skills:
{available_skills}

Rules:
- Return JSON only. Do not include Markdown or commentary.
- Use only skill_id values from the available skills list.
- If one skill can complete the task, use one step. Do not over-split.
- Each step must include step_index, skill_id, skill_name, description, input_mapping, and depends_on.
- depends_on may contain prior step indexes as strings or prior step ids.
- input_mapping must be an object. Use an empty object if no parameters are needed.

Example 1:
{{
  "steps": [
    {{
      "step_index": 0,
      "skill_id": "skill_fill_form",
      "skill_name": "fill_form",
      "description": "Fill the requested form.",
      "input_mapping": {{"goal": "task description"}},
      "depends_on": []
    }}
  ],
  "plan_rationale": "A single form-filling skill is sufficient."
}}

Example 2:
{{
  "steps": [
    {{
      "step_index": 0,
      "skill_id": "skill_extract",
      "skill_name": "extract_data",
      "description": "Extract the source data.",
      "input_mapping": {{}},
      "depends_on": []
    }},
    {{
      "step_index": 1,
      "skill_id": "skill_submit",
      "skill_name": "submit_result",
      "description": "Submit the extracted result.",
      "input_mapping": {{"data": "${{step_0.result}}"}},
      "depends_on": ["0"]
    }}
  ],
  "plan_rationale": "Data must be extracted before it can be submitted."
}}

Return this JSON shape:
{{
  "steps": [
    {{
      "step_index": 0,
      "skill_id": "available_skill_id",
      "skill_name": "skill_name",
      "description": "step description",
      "input_mapping": {{}},
      "depends_on": []
    }}
  ],
  "plan_rationale": "why this plan was selected"
}}
"""


class SkillPlanner:
    """将复杂任务分解为 Skill 执行计划。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def plan(
        self,
        task_description: str,
        available_skills: List[Skill],
        current_state: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        *,
        force_fallback: bool = False,
        force_llm: bool = False,
        llm_extra: Optional[Dict[str, Any]] = None,
        fallback_on_llm_error: bool = True,
        fallback_on_invalid_response: bool = True,
    ) -> ExecutionPlan:
        """为任务生成执行计划。"""
        plan = ExecutionPlan(
            task_id=task_id or str(uuid.uuid4()),
            task_description=task_description,
        )

        if not available_skills:
            logger.warning("无可用 Skill，无法生成执行计划")
            return plan

        if force_fallback or (not force_llm and self._should_use_fallback_only()):
            plan.metadata["source"] = "local_demo_fallback"
            return self._fallback_plan(
                plan,
                available_skills,
                current_state=current_state,
                prefer_offline=True,
            )

        skills_info = self._format_skills(available_skills)
        prompt = _PLAN_PROMPT.format(
            task_description=task_description,
            current_state=json.dumps(current_state or {}, ensure_ascii=False)[:300],
            available_skills=skills_info,
        )

        chat_kwargs = {"extra": llm_extra} if llm_extra is not None else {}
        try:
            response = self._llm.chat([
                Message.system(
                    "You are the SkillOS Planner Agent. Return strict JSON only."
                ),
                Message.user(prompt),
            ], **chat_kwargs)
        except Exception as exc:
            if not fallback_on_llm_error:
                raise
            logger.warning("Planner LLM failed; using fallback plan: %s", exc)
            return self._fallback_plan(plan, available_skills, current_state=current_state)

        plan.metadata["llm_model"] = getattr(response, "model", None)
        plan.metadata["llm_usage"] = getattr(response, "usage", {}) or {}
        plan.metadata["llm_finish_reason"] = getattr(response, "finish_reason", None)
        data = self._extract_json(response.content)
        if not data or "steps" not in data:
            if not fallback_on_invalid_response:
                plan.metadata["source"] = "llm"
                plan.metadata["invalid_response"] = True
                plan.metadata["failure_reason"] = "Planner LLM returned invalid JSON."
                return plan
            logger.warning("规划器 LLM 返回无效响应，使用顺序执行所有 Skill")
            return self._fallback_plan(plan, available_skills, current_state=current_state)

        plan.steps = _normalize_plan_steps(data["steps"], available_skills)
        repairs = _repair_required_input_mappings(
            plan.steps,
            available_skills,
            current_state or {},
            task_description,
        )
        if repairs:
            plan.metadata["input_mapping_repairs"] = repairs

        plan.metadata["rationale"] = data.get("plan_rationale", "")
        plan.metadata["source"] = "llm"
        logger.info(f"执行计划生成: {len(plan.steps)} 步骤, 任务={task_description[:50]}")
        return plan

    def _fallback_plan(
        self,
        plan: ExecutionPlan,
        skills: List[Skill],
        *,
        current_state: Optional[Dict[str, Any]] = None,
        prefer_offline: bool = False,
    ) -> ExecutionPlan:
        """降级方案：顺序执行所有 Skill。"""
        selected_skills = skills
        if prefer_offline:
            offline_skills = [
                skill for skill in skills
                if skill.implementation
                and (skill.implementation.code or skill.implementation.sub_skill_ids)
            ]
            selected_skills = offline_skills or skills

        prev_step_id: Optional[str] = None
        for i, skill in enumerate(selected_skills[:5]):
            step = PlanStep(
                step_index=i,
                skill_id=skill.skill_id,
                skill_name=skill.name,
                description=skill.description or f"Execute {skill.name}",
                depends_on=[prev_step_id] if prev_step_id else [],
            )
            plan.steps.append(step)
            prev_step_id = step.step_id
        repairs = _repair_required_input_mappings(
            plan.steps,
            skills,
            current_state or {},
            plan.task_description,
        )
        if repairs:
            plan.metadata["input_mapping_repairs"] = repairs
        plan.metadata["rationale"] = "Fallback sequential plan generated from top retrieved skills."
        plan.metadata["source"] = "fallback"
        return plan

    def _should_use_fallback_only(self) -> bool:
        """Skip LLM planning for local demo keys so preview runs are immediate."""
        if os.getenv("SKILLOS_FORCE_PLANNER_FALLBACK", "").lower() in {"1", "true", "yes"}:
            return True

        cfg = getattr(self._llm, "_cfg", None)
        api_key = str(getattr(cfg, "api_key", "") or "").strip().lower()
        return api_key in {"demo", "dummy", "test", "test_key", "your_api_key_here"}

    def _format_skills(self, skills: List[Skill]) -> str:
        lines = []
        for s in skills[:10]:
            lines.append(
                f"- [{s.skill_id}] {s.name}: {s.description[:80]}\n"
                f"  inputs: {list(s.interface.input_schema.get('properties', {}).keys())}"
            )
        return "\n".join(lines)

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


def _normalize_plan_steps(raw_steps: Any, available_skills: List[Skill]) -> List[PlanStep]:
    if not isinstance(raw_steps, list):
        return []

    skill_map = {skill.skill_id: skill for skill in available_skills}
    normalized: List[PlanStep] = []
    index_to_step_id: Dict[str, str] = {}

    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        skill_id = str(raw.get("skill_id") or "")
        skill = skill_map.get(skill_id)
        if not skill:
            continue

        step = PlanStep(
            step_index=len(normalized),
            skill_id=skill.skill_id,
            skill_name=str(raw.get("skill_name") or skill.name),
            description=str(raw.get("description") or skill.description or f"Execute {skill.name}"),
            input_mapping=raw.get("input_mapping") if isinstance(raw.get("input_mapping"), dict) else {},
        )

        old_index = str(raw.get("step_index", len(normalized)))
        index_to_step_id[old_index] = step.step_id
        depends_on = raw.get("depends_on", [])
        if not isinstance(depends_on, list):
            depends_on = []
        for dep in depends_on:
            dep_str = str(dep)
            if dep_str in index_to_step_id:
                step.depends_on.append(index_to_step_id[dep_str])
            elif any(existing.step_id == dep_str for existing in normalized):
                step.depends_on.append(dep_str)

        normalized.append(step)

    return normalized


_MISSING = object()


def _repair_required_input_mappings(
    steps: List[PlanStep],
    available_skills: List[Skill],
    current_state: Dict[str, Any],
    task_description: str,
) -> List[Dict[str, Any]]:
    """Fill missing required inputs from task state or earlier step mappings."""
    skill_map = {skill.skill_id: skill for skill in available_skills}
    task_input = current_state.get("input", {})
    if not isinstance(task_input, dict):
        task_input = {}

    repairs: List[Dict[str, Any]] = []
    for index, step in enumerate(steps):
        skill = skill_map.get(step.skill_id)
        if not skill:
            continue
        for input_name in _required_input_names(skill):
            if _has_value(step.input_mapping.get(input_name)):
                continue
            value = _infer_input_value(
                input_name,
                previous_steps=steps[:index],
                task_input=task_input,
                current_state=current_state,
                task_description=task_description,
            )
            if value is _MISSING:
                continue
            step.input_mapping[input_name] = value
            repairs.append({
                "step_index": step.step_index,
                "skill_id": step.skill_id,
                "input": input_name,
                "source": "planner_input_repair",
            })
    return repairs


def _required_input_names(skill: Skill) -> List[str]:
    schema = getattr(getattr(skill, "interface", None), "input_schema", {}) or {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        return []
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    return [
        str(name)
        for name in required
        if isinstance(name, str)
        and (not properties or name in properties)
    ]


def _infer_input_value(
    input_name: str,
    *,
    previous_steps: List[PlanStep],
    task_input: Dict[str, Any],
    current_state: Dict[str, Any],
    task_description: str,
) -> Any:
    if input_name in task_input and _has_value(task_input.get(input_name)):
        return task_input[input_name]
    if input_name in current_state and _has_value(current_state.get(input_name)):
        return current_state[input_name]

    for prev_step in reversed(previous_steps):
        if input_name in prev_step.input_mapping and _has_value(prev_step.input_mapping.get(input_name)):
            return prev_step.input_mapping[input_name]

    if input_name == "description":
        raw_context = current_state.get("raw_context")
        if _has_value(raw_context):
            return str(raw_context)
        if _has_value(task_description):
            return task_description

    return _MISSING


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}
