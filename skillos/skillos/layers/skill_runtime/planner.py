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
请为以下任务制定详细的 Skill 执行计划。

## 任务描述
{task_description}

## 当前状态
{current_state}

## 可用 Skill
{available_skills}

## 规划要求
1. 将任务分解为有序的执行步骤
2. 每个步骤对应一个 Skill
3. 明确步骤间的依赖关系
4. 为每个步骤提供参数映射（从任务描述或前序步骤结果中提取）
5. 如果某个步骤可以并行执行，在 depends_on 中不列出其他步骤

## 输出格式（严格 JSON）
{{
  "steps": [
    {{
      "step_index": 0,
      "skill_id": "skill_id_here",
      "skill_name": "skill_name",
      "description": "步骤描述",
      "input_mapping": {{
        "param1": "值或 ${{step_0.result.field}} 引用"
      }},
      "depends_on": []
    }},
    {{
      "step_index": 1,
      "skill_id": "skill_id_here",
      "skill_name": "skill_name",
      "description": "步骤描述",
      "input_mapping": {{}},
      "depends_on": ["step_0_id"]
    }}
  ],
  "plan_rationale": "规划理由"
}}

只输出 JSON，不要其他内容。
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

        response = self._llm.chat([
            Message.system(
                "你是 SkillOS 的任务规划专家，擅长将复杂任务分解为有序的 Skill 执行步骤。"
                "严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])

        data = self._extract_json(response.content)
        if not data or "steps" not in data:
            logger.warning("规划器 LLM 返回无效响应，使用顺序执行所有 Skill")
            return self._fallback_plan(plan, available_skills)

        skill_map = {s.skill_id: s for s in available_skills}
        step_id_map: Dict[str, str] = {}  # step_index → step_id

        for step_data in data["steps"]:
            step = PlanStep(
                step_index=step_data.get("step_index", len(plan.steps)),
                skill_id=step_data.get("skill_id", ""),
                skill_name=step_data.get("skill_name", ""),
                description=step_data.get("description", ""),
                input_mapping=step_data.get("input_mapping", {}),
            )
            step_id_map[str(step.step_index)] = step.step_id

            # 解析依赖（支持 step_id 或 step_index 引用）
            for dep in step_data.get("depends_on", []):
                dep_str = str(dep)
                if dep_str in step_id_map:
                    step.depends_on.append(step_id_map[dep_str])
                else:
                    step.depends_on.append(dep_str)

            plan.steps.append(step)

        plan.metadata["rationale"] = data.get("plan_rationale", "")
        logger.info(f"执行计划生成: {len(plan.steps)} 步骤, 任务={task_description[:50]}")
        return plan

    def _fallback_plan(self, plan: ExecutionPlan, skills: List[Skill]) -> ExecutionPlan:
        """降级方案：顺序执行所有 Skill。"""
        prev_step_id: Optional[str] = None
        for i, skill in enumerate(skills):
            step = PlanStep(
                step_index=i,
                skill_id=skill.skill_id,
                skill_name=skill.name,
                description=skill.description,
                depends_on=[prev_step_id] if prev_step_id else [],
            )
            plan.steps.append(step)
            prev_step_id = step.step_id
        return plan

    def _format_skills(self, skills: List[Skill]) -> str:
        lines = []
        for s in skills[:10]:
            lines.append(
                f"- [{s.skill_id}] {s.name}: {s.description[:80]}\n"
                f"  输入: {list(s.interface.input_schema.get('properties', {}).keys())}"
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
