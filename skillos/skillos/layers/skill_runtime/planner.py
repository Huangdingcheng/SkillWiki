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
    observations: List[Dict[str, Any]] = field(default_factory=list)
    step_judgment: Dict[str, Any] = field(default_factory=dict)
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
你是 SkillOS 的 DAG Planning Agent。

请根据用户任务、预期结果和已通过 relevance filtering 的 Skill，生成一个可执行 DAG。
Skill 是辅助能力，不是任务本身。计划必须保留用户目标，并在每个关键步骤标明需要什么 observation。

## 任务描述
{task_description}

## 当前状态
{current_state}

## 用户任务预期输出
{expected_outcome}

## 可用 Skill
{available_skills}

## 规划要求
1. 使用 SkillX 风格三层规划：strategic/high outcome -> functional workflow -> atomic observable actions。
2. 每个步骤可以引用一个 Skill，也可以说明该步骤需要 agent 生成具体 action。
3. 明确 depends_on，形成 DAG；不要无意义重复同一任务。
4. input_mapping 必须绑定用户任务里的参数、当前状态、前序步骤输出或 observation，不要硬编码旧 Skill 示例。
5. 每个步骤 description 要说明“为什么做这步”和“用什么证据判断完成”。
6. 如果 Skill 只覆盖局部能力，仍然可以使用，但不能让它改变最终目标。
7. 最终步骤必须验证“用户任务预期输出”，不是验证 Skill 是否执行过。

## 输出格式（严格 JSON）
{{
  "steps": [
    {{
      "step_index": 0,
      "skill_id": "skill_id_here",
      "skill_name": "skill_name",
      "description": "步骤描述，包括目标和完成证据",
      "input_mapping": {{
        "param1": "值或 ${{step_0.result.field}} 引用"
      }},
      "depends_on": [],
      "layer": "high | functional | atomic",
      "observation_required": ["screen | stdout | filesystem | browser_dom | app_state | api_response"],
      "fallback_if_unmatched": "如果 Skill 不适用，agent 应如何生成动作"
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
  "plan_rationale": "规划理由，说明如何防止 task drift",
  "skill_usage_policy": "哪些 Skill 是主执行，哪些只是参考知识",
  "validation_plan": ["最终如何验证是否达到用户目标"]
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
            expected_outcome=json.dumps((current_state or {}).get("expected_outcome", {}), ensure_ascii=False)[:500],
            available_skills=skills_info,
        )

        if _is_demo_llm(self._llm):
            logger.info("规划器检测到本地 demo key，使用顺序执行降级计划")
            return self._fallback_plan(plan, available_skills)

        try:
            response = self._llm.chat([
                Message.system(
                    "你是 SkillOS DAG Planning Agent。Skill 是辅助知识，用户目标是最高优先级。"
                    "严格按照 JSON 格式输出。"
                ),
                Message.user(prompt),
            ])
        except Exception as exc:
            logger.warning("规划器 LLM 调用失败，使用顺序执行降级计划: %s", exc)
            return self._fallback_plan(plan, available_skills)

        data = self._extract_json(response.content)
        if not data or "steps" not in data:
            logger.warning("规划器 LLM 返回无效响应，使用顺序执行所有 Skill")
            return self._fallback_plan(plan, available_skills)

        skill_map = {s.skill_id: s for s in available_skills}
        step_id_map: Dict[str, str] = {}  # step_index → step_id
        skipped_non_executable: List[Dict[str, Any]] = []

        for step_data in data["steps"]:
            raw_skill_id = str(step_data.get("skill_id") or "").strip()
            raw_skill_name = str(step_data.get("skill_name") or "").strip()
            if raw_skill_id not in skill_map:
                skipped_non_executable.append({
                    "step_index": step_data.get("step_index", len(plan.steps)),
                    "skill_id": raw_skill_id,
                    "skill_name": raw_skill_name,
                    "description": step_data.get("description", ""),
                    "reason": "Planner step is validation/agent narrative or references an unavailable Skill; it is kept as metadata, not executed.",
                })
                continue
            step = PlanStep(
                step_index=step_data.get("step_index", len(plan.steps)),
                skill_id=raw_skill_id,
                skill_name=raw_skill_name or skill_map[raw_skill_id].name,
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
        plan.metadata["skipped_non_executable_steps"] = skipped_non_executable
        if not plan.steps and available_skills:
            logger.warning("规划器没有返回可执行 Skill step，使用顺序执行降级计划")
            return self._fallback_plan(plan, available_skills)
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


def _is_demo_llm(llm_client: LLMClient) -> bool:
    api_key = str(getattr(getattr(llm_client, "_cfg", None), "api_key", ""))
    return api_key.startswith("local-") or api_key.startswith("demo-")
