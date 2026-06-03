"""Skill 执行器 — 按执行计划运行 Skill，管理状态和错误处理。"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Union

from ...models.experience_model import ExecutionStatus, SkillExecutionRecord
from ...models.skill_model import Skill
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger
from .planner import ExecutionPlan, PlanStep, StepStatus
from .state_tracker import StateTracker

logger = get_logger(__name__)

# 执行事件类型（用于 WebSocket 实时推送）
ExecutionEventCallback = Callable[[str, Dict[str, Any]], Union[None, Awaitable[None]]]


class SkillExecutor:
    """Skill 执行引擎。

    职责：
    - 按执行计划顺序/并行执行 Skill
    - 管理状态追踪（前/后快照）
    - 错误处理和重试
    - 实时事件推送（WebSocket）
    - 执行记录持久化
    """

    def __init__(
        self,
        skill_registry: Optional[Any] = None,  # SkillWikiManager
        llm_client: Optional[LLMClient] = None,
        max_retries: int = 2,
        step_timeout_s: float = 30.0,
    ) -> None:
        self._registry = skill_registry
        self._llm = llm_client
        self._max_retries = max_retries
        self._step_timeout = step_timeout_s
        self._event_callbacks: List[ExecutionEventCallback] = []

    def add_event_callback(self, callback: ExecutionEventCallback) -> None:
        """注册事件回调（用于 WebSocket 推送）。"""
        self._event_callbacks.append(callback)

    def remove_event_callback(self, callback: ExecutionEventCallback) -> None:
        self._event_callbacks = [c for c in self._event_callbacks if c is not callback]

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        for cb in self._event_callbacks:
            try:
                result = cb(event_type, data)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception:
                pass

    async def execute_plan(
        self,
        plan: ExecutionPlan,
        skill_map: Dict[str, Skill],
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """执行完整的执行计划。

        Args:
            plan: 执行计划
            skill_map: skill_id → Skill 的映射
            initial_state: 初始状态

        Returns:
            最终状态字典
        """
        tracker = StateTracker(plan.task_id, initial_state)
        execution_records: List[SkillExecutionRecord] = []

        self._emit("plan_started", {
            "plan_id": plan.plan_id,
            "goal": plan.task_description,
            "step_count": plan.total_steps,
            "task": plan.task_description,
            "total_steps": plan.total_steps,
        })

        while _has_pending_steps(plan):
            skipped = self._skip_blocked_steps(plan)
            ready_steps = _ready_steps(plan)
            if not ready_steps:
                if not skipped:
                    break
                continue

            # 并行执行无依赖的步骤
            if len(ready_steps) > 1:
                tasks = [
                    self._execute_step(step, skill_map, tracker)
                    for step in ready_steps
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for step, result in zip(ready_steps, results):
                    if isinstance(result, Exception):
                        step.status = StepStatus.FAILED
                        step.error = str(result)
                        if not step.started_at:
                            step.started_at = datetime.utcnow()
                        step.completed_at = datetime.utcnow()
                        self._emit_step_failed(plan.plan_id, step, step.error)
                    else:
                        record = result
                        if record:
                            execution_records.append(record)
            else:
                record = await self._execute_step(ready_steps[0], skill_map, tracker)
                if record:
                    execution_records.append(record)

        final_state = tracker.current
        total_latency_ms = sum((step.latency_ms or 0.0) for step in plan.steps)
        status = _plan_status(plan)
        self._emit("plan_completed", {
            "plan_id": plan.plan_id,
            "status": status,
            "total_latency_ms": total_latency_ms,
            "success": plan.is_complete and not plan.has_failures,
            "summary": plan.to_summary(),
            "final_state": final_state,
        })

        return final_state

    def _skip_blocked_steps(self, plan: ExecutionPlan) -> bool:
        skipped_any = False
        blocked_step_ids = {
            step.step_id
            for step in plan.steps
            if step.status in (StepStatus.FAILED, StepStatus.SKIPPED)
        }
        if not blocked_step_ids:
            return False

        for step in plan.steps:
            if step.status != StepStatus.PENDING:
                continue
            failed_dependency = next(
                (dep for dep in step.depends_on if dep in blocked_step_ids),
                None,
            )
            if not failed_dependency:
                continue

            now = datetime.utcnow()
            step.status = StepStatus.SKIPPED
            step.started_at = now
            step.completed_at = now
            step.error = f"Skipped because dependency failed: {failed_dependency}"
            self._emit("step_skipped", {
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "step_index": step.step_index,
                "skill_id": step.skill_id,
                "skill_name": step.skill_name or step.skill_id,
                "reason": step.error,
                "failed_dependency": failed_dependency,
            })
            skipped_any = True
        return skipped_any

    def _emit_step_failed(self, plan_id: str, step: PlanStep, error: str) -> None:
        self._emit("step_failed", {
            "plan_id": plan_id,
            "step_id": step.step_id,
            "step_index": step.step_index,
            "skill_id": step.skill_id,
            "skill_name": step.skill_name or step.skill_id,
            "error": error,
            "latency_ms": step.latency_ms,
        })

    async def _execute_step(
        self,
        step: PlanStep,
        skill_map: Dict[str, Skill],
        tracker: StateTracker,
    ) -> Optional[SkillExecutionRecord]:
        """执行单个步骤（含重试）。"""
        skill = skill_map.get(step.skill_id)
        if not skill:
            step.started_at = datetime.utcnow()
            step.status = StepStatus.FAILED
            step.error = f"Skill not found: {step.skill_id}"
            step.completed_at = datetime.utcnow()
            self._emit_step_failed(tracker._task_id, step, step.error)
            return None

        step.status = StepStatus.RUNNING
        step.started_at = datetime.utcnow()
        self._emit("step_started", {
            "plan_id": tracker._task_id,
            "step_id": step.step_id,
            "step_index": step.step_index,
            "skill_name": skill.name,
            "input": step.input_mapping,
        })

        record = SkillExecutionRecord(
            skill_id=skill.skill_id,
            skill_version=skill.version,
            task_id=tracker._task_id,
            input_data=step.input_mapping,
            state_before=tracker.current,
        )
        record.start()

        # 拍摄执行前快照
        tracker.snapshot_before(skill.skill_id, skill.name)
        tracker.push_checkpoint()

        for attempt in range(self._max_retries + 1):
            try:
                output = await asyncio.wait_for(
                    self._run_skill(skill, step.input_mapping, tracker.current),
                    timeout=self._step_timeout,
                )
                # 执行成功
                state_changes = output.get("_state_changes", {})
                tracker.update(state_changes)
                tracker.snapshot_after(skill.skill_id, skill.name)

                step.status = StepStatus.SUCCESS
                step.result = output
                step.completed_at = datetime.utcnow()

                record.complete(output, tracker.current)
                self._emit("step_completed", {
                    "plan_id": tracker._task_id,
                    "step_id": step.step_id,
                    "step_index": step.step_index,
                    "skill_id": skill.skill_id,
                    "skill_name": skill.name,
                    "output": output,
                    "latency_ms": step.latency_ms,
                })
                return record

            except asyncio.TimeoutError:
                error = f"Step timed out ({self._step_timeout}s)"
                if attempt < self._max_retries:
                    logger.warning(f"步骤超时，重试 {attempt + 1}/{self._max_retries}: {skill.name}")
                    continue
                tracker.rollback()
                step.status = StepStatus.FAILED
                step.error = error
                step.completed_at = datetime.utcnow()
                record.fail(error, "TimeoutError")
                self._emit_step_failed(tracker._task_id, step, error)
                return record

            except Exception as e:
                error = str(e)
                if attempt < self._max_retries:
                    logger.warning(f"步骤失败，重试 {attempt + 1}/{self._max_retries}: {skill.name} - {error}")
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                tracker.rollback()
                step.status = StepStatus.FAILED
                step.error = error
                step.completed_at = datetime.utcnow()
                record.fail(error, type(e).__name__)
                self._emit_step_failed(tracker._task_id, step, error)
                return record

        return record

    async def _run_skill(
        self,
        skill: Skill,
        input_data: Dict[str, Any],
        current_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行 Skill：prompt→LLM / code→exec / composite→递归。"""
        if not skill.implementation:
            raise RuntimeError(f"Skill {skill.name} 没有实现")

        impl = skill.implementation

        # 1. prompt_template → LLM 调用
        if impl.prompt_template:
            return await self._run_prompt_skill(skill, impl, input_data)

        # 2. code → 受限沙箱执行
        if impl.code:
            return await self._run_code_skill(skill, impl, input_data, current_state)

        # 3. composite/functional → 递归执行子 Skill
        if impl.sub_skill_ids:
            return await self._run_composite_skill(skill, impl, input_data, current_state)

        raise RuntimeError(f"Skill {skill.name} 没有可执行的实现（无 prompt/code/sub_skills）")

    async def _run_prompt_skill(self, skill: Skill, impl: Any, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """调用 LLM 执行 prompt 类型 Skill。"""
        if not self._llm:
            # 无 LLM 客户端时返回模拟结果（测试/离线模式）
            return {
                "result": f"[mock] {skill.name} executed",
                "skill_name": skill.name,
                "_state_changes": {f"{skill.name}_executed": True},
            }
        try:
            prompt = impl.prompt_template.format(**input_data)
        except KeyError as e:
            raise RuntimeError(f"Skill {skill.name} prompt 模板缺少参数: {e}") from e

        response = await asyncio.to_thread(
            self._llm.chat,
            [Message.system(f"你是 SkillWiki 中的 {skill.name} Skill，请严格按照任务要求执行。"),
             Message.user(prompt)],
        )
        result_text = response.content
        parsed_result = _extract_json_object(result_text)
        if parsed_result is not None:
            return {
                **parsed_result,
                "skill_name": skill.name,
                "_state_changes": {f"{skill.name}_result": parsed_result},
            }
        return {
            "result": result_text,
            "skill_name": skill.name,
            "_state_changes": {f"{skill.name}_result": result_text},
        }

    async def _run_code_skill(
        self, skill: Skill, impl: Any, input_data: Dict[str, Any], current_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """在受限命名空间中执行 Python 代码 Skill。"""
        import builtins
        safe_builtins = {
            k: getattr(builtins, k)
            for k in ("len", "range", "enumerate", "zip", "map", "filter",
                      "sorted", "reversed", "list", "dict", "set", "tuple",
                      "str", "int", "float", "bool", "type", "isinstance",
                      "print", "repr", "abs", "min", "max", "sum", "round",
                      "any", "all", "next", "iter", "hasattr", "getattr")
        }
        namespace: Dict[str, Any] = {
            "__builtins__": safe_builtins,
            "input_data": input_data,
            "state": current_state,
            "output": {},
        }
        try:
            exec(compile(impl.code, f"<skill:{skill.name}>", "exec"), namespace)  # noqa: S102
        except Exception as e:
            raise RuntimeError(f"Skill {skill.name} 代码执行失败: {e}") from e

        output = namespace.get("output", {})
        return {
            **output,
            "skill_name": skill.name,
            "_state_changes": {f"{skill.name}_executed": True, **output},
        }

    async def _run_composite_skill(
        self, skill: Skill, impl: Any, input_data: Dict[str, Any], current_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """递归执行 composite/functional Skill 的子 Skill。"""
        if not self._registry:
            raise RuntimeError(f"Skill {skill.name} 是 composite 类型但执行器未配置 registry")

        sub_results: Dict[str, Any] = {}
        merged_state = dict(current_state)

        for sub_id in impl.sub_skill_ids:
            sub_skill = await self._registry.get(sub_id)
            if not sub_skill and hasattr(self._registry, "get_by_name"):
                sub_skill = await self._registry.get_by_name(sub_id)
            if not sub_skill:
                logger.warning(f"子 Skill 不存在，跳过: {sub_id}")
                continue
            sub_result = await self._run_skill(sub_skill, input_data, merged_state)
            sub_results[sub_id] = sub_result
            # 将子 Skill 的状态变更合并到当前状态
            merged_state.update(sub_result.get("_state_changes", {}))

        return {
            "sub_results": sub_results,
            "skill_name": skill.name,
            "_state_changes": {"composite_executed": True, **merged_state},
        }

    async def execute_single(
        self,
        skill: Skill,
        input_data: Dict[str, Any],
        task_id: Optional[str] = None,
    ) -> SkillExecutionRecord:
        """执行单个 Skill（不需要完整计划）。"""
        record = SkillExecutionRecord(
            skill_id=skill.skill_id,
            skill_version=skill.version,
            task_id=task_id or str(uuid.uuid4()),
            input_data=input_data,
        )
        record.start()
        try:
            output = await asyncio.wait_for(
                self._run_skill(skill, input_data, {}),
                timeout=self._step_timeout,
            )
            record.complete(output, output.get("_state_changes", {}))
        except Exception as e:
            record.fail(str(e), type(e).__name__)
        return record


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Parse a prompt Skill response when the model returns machine-readable JSON."""
    stripped = str(text or "").strip()
    if not stripped:
        return None
    candidates = [stripped]
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    object_match = re.search(r"\{[\s\S]+\}", stripped)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _plan_status(plan: ExecutionPlan) -> str:
    if not plan.steps:
        return "failed"
    if plan.is_complete and not plan.has_failures:
        return "success"
    success_count = sum(1 for step in plan.steps if step.status == StepStatus.SUCCESS)
    return "partial" if success_count else "failed"


def _has_pending_steps(plan: ExecutionPlan) -> bool:
    return any(step.status == StepStatus.PENDING for step in plan.steps)


def _ready_steps(plan: ExecutionPlan) -> List[PlanStep]:
    successful_ids = {
        step.step_id for step in plan.steps if step.status == StepStatus.SUCCESS
    }
    return [
        step
        for step in plan.steps
        if step.status == StepStatus.PENDING
        and all(dep in successful_ids for dep in step.depends_on)
    ]
