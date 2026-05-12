"""Skill 规划器 — 将复杂任务分解为 Skill 执行计划。"""

from __future__ import annotations

import json
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
    ) -> ExecutionPlan:
        """为任务生成执行计划。"""
        plan = ExecutionPlan(
            task_id=task_id or str(uuid.uuid4()),
            task_description=task_description,
        )

        if not available_skills:
            logger.warning("无可用 Skill，无法生成执行计划")
            return plan

        skills_info = self._format_skills(available_skills)
        prompt = _PLAN_PROMPT.format(
            task_description=task_description,
            current_state=json.dumps(current_state or {}, ensure_ascii=False)[:300],
            available_skills=skills_info,
        )

        try:
            response = self._llm.chat([
                Message.system(
                    "You are the SkillOS Planner Agent. Return strict JSON only."
                ),
                Message.user(prompt),
            ])
        except Exception as exc:
            logger.warning("Planner LLM failed; using fallback plan: %s", exc)
            return self._fallback_plan(plan, available_skills)

        data = self._extract_json(response.content)
        if not data or "steps" not in data:
            logger.warning("规划器 LLM 返回无效响应，使用顺序执行所有 Skill")
            return self._fallback_plan(plan, available_skills)

        plan.steps = _normalize_plan_steps(data["steps"], available_skills)

        plan.metadata["rationale"] = data.get("plan_rationale", "")
        logger.info(f"执行计划生成: {len(plan.steps)} 步骤, 任务={task_description[:50]}")
        return plan

    def _fallback_plan(self, plan: ExecutionPlan, skills: List[Skill]) -> ExecutionPlan:
        """降级方案：顺序执行所有 Skill。"""
        prev_step_id: Optional[str] = None
        for i, skill in enumerate(skills[:5]):
            step = PlanStep(
                step_index=i,
                skill_id=skill.skill_id,
                skill_name=skill.name,
                description=skill.description or f"Execute {skill.name}",
                depends_on=[prev_step_id] if prev_step_id else [],
            )
            plan.steps.append(step)
            prev_step_id = step.step_id
        plan.metadata["rationale"] = "Fallback sequential plan generated from top retrieved skills."
        plan.metadata["source"] = "fallback"
        return plan

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
