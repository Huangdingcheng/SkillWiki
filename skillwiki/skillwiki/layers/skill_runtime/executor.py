"""Skill 执行器 — 按执行计划运行 Skill，管理状态和错误处理。"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import re
from string import Formatter
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Union

from ...models.experience_model import ExecutionStatus, SkillExecutionRecord
from ...models.skill_model import Skill
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger
from .input_contract import InputContractError, validate_skill_input
from .planner import ExecutionPlan, PlanStep, StepStatus
from .state_tracker import StateTracker

logger = get_logger(__name__)

# 执行事件类型（用于 WebSocket 实时推送）
ExecutionEventCallback = Callable[[str, Dict[str, Any]], Union[None, Awaitable[None]]]


class _RuntimeOutput(dict):
    """Business output with execution metadata kept outside the JSON object.

    A dict subclass lets the existing execution flow consume outputs without
    adding reserved runtime keys to a Skill's declared business payload.
    """

    def __init__(self, business: Optional[Dict[str, Any]] = None,
                 *, state_changes: Optional[Dict[str, Any]] = None,
                 execution_evidence: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(business or {})
        self.runtime_state_changes = copy.deepcopy(state_changes or {})
        self.execution_evidence = copy.deepcopy(execution_evidence or {})


def _output_state_changes(output: Dict[str, Any]) -> Dict[str, Any]:
    """Return internal state changes without treating business keys as metadata."""
    changes = getattr(output, "runtime_state_changes", None)
    if isinstance(changes, dict):
        return copy.deepcopy(changes)
    # Compatibility for callers that provide an internal legacy dict.
    legacy = output.get("_state_changes", {})
    return copy.deepcopy(legacy) if isinstance(legacy, dict) else {}


def escape_literal_prompt(text: str) -> str:
    """Escape a text fragment before embedding it into a format template."""
    return str(text).replace("{", "{{").replace("}", "}}")


def prompt_template_fields(template: str) -> set[str]:
    """Extract real format fields while ignoring raw JSON and escaped braces."""
    protected = _protect_literal_json(str(template or ""))
    def collect(value: str) -> set[str]:
        fields: set[str] = set()
        for _, field_name, format_spec, _ in Formatter().parse(value):
            if field_name is not None:
                fields.add(field_name)
                if format_spec:
                    fields.update(collect(format_spec))
        return fields
    return collect(protected)


def is_literal_instruction_skill(skill: Skill) -> bool:
    """Resolve explicit prompt semantics before the legacy component heuristic."""
    prompt_format = getattr(skill.implementation, "prompt_format", None)
    if prompt_format is not None:
        return prompt_format == "literal"
    context = getattr(getattr(skill, "provenance", None), "creation_context", {}) or {}
    return bool(context.get("demo_components") or context.get("component_run_id"))


def render_prompt_template(template: str, input_data: Dict[str, Any]) -> tuple[str, bool]:
    """Render a named template using the same semantics as native execution."""
    protected = _protect_literal_json(str(template or ""))
    fields = prompt_template_fields(template)
    return protected.format(**input_data), bool(fields)


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

        # Input mappings are already concrete in ExecutionPlan. Validate every
        # pending step (including descendants) before spending on any step.
        invalid_inputs = False
        for step in plan.steps:
            if step.status != StepStatus.PENDING:
                continue
            skill = skill_map.get(step.skill_id)
            if skill is None:
                continue  # Retain the existing missing-Skill execution path.
            try:
                await asyncio.wait_for(
                    self._validate_skill_tree_inputs(skill, step.input_mapping),
                    timeout=self._step_timeout,
                )
            except Exception as exc:
                invalid_inputs = True
                step.status = StepStatus.FAILED
                step.error = str(exc) or f"Input validation timed out ({self._step_timeout}s)"
                step.started_at = step.completed_at = datetime.utcnow()
                self._emit_step_failed(plan.plan_id, step, step.error)
        if invalid_inputs:
            for step in plan.steps:
                if step.status == StepStatus.PENDING:
                    step.status = StepStatus.SKIPPED
                    step.error = "Skipped because plan input validation failed"
                    step.started_at = step.completed_at = datetime.utcnow()
                    self._emit("step_skipped", {
                        "plan_id": plan.plan_id, "step_id": step.step_id,
                        "step_index": step.step_index, "skill_id": step.skill_id,
                        "skill_name": step.skill_name or step.skill_id, "reason": step.error,
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
                state_changes = _output_state_changes(output)
                tracker.update(state_changes)
                tracker.snapshot_after(skill.skill_id, skill.name)

                step.status = StepStatus.SUCCESS
                step.result = dict(output)
                step.execution_evidence = copy.deepcopy(
                    getattr(output, "execution_evidence", {})
                )
                step.completed_at = datetime.utcnow()

                record.complete(dict(output), tracker.current)
                record.execution_evidence = copy.deepcopy(
                    getattr(output, "execution_evidence", {})
                )
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

            except InputContractError as e:
                # Contract failures are deterministic and cannot be repaired by
                # retrying model/code dispatch with the same input.
                tracker.rollback()
                step.status = StepStatus.FAILED
                step.error = str(e)
                step.completed_at = datetime.utcnow()
                record.fail(str(e), type(e).__name__)
                record.execution_evidence = copy.deepcopy(getattr(e, "execution_evidence", {}))
                step.execution_evidence = copy.deepcopy(record.execution_evidence)
                self._emit_step_failed(tracker._task_id, step, str(e))
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
        validate_skill_input(skill, input_data)
        if not skill.implementation:
            raise RuntimeError(f"Skill {skill.name} 没有实现")

        impl = skill.implementation
        if impl.request_body_validation is not None and not impl.prompt_template:
            from .request_body_validation import RequestBodyValidationError
            raise RequestBodyValidationError("request_body_validation_requires_prompt")

        # 1. prompt_template → LLM 调用
        if impl.prompt_template:
            return await self._run_prompt_skill(skill, impl, input_data)

        # 2. code → 受限沙箱执行
        if impl.code:
            return await self._run_code_skill(skill, impl, input_data, current_state)

        # 3. composite/functional → 递归执行子 Skill
        if impl.sub_skill_ids:
            return await self._run_composite_skill(skill, impl, input_data, current_state)

        if impl.tool_calls:
            raise RuntimeError(f"Skill {skill.name}: tool-only implementation is unsupported by SkillExecutor")
        raise RuntimeError(f"Skill {skill.name} 没有可执行的实现（无 prompt/code/sub_skills）")

    async def _run_prompt_skill(self, skill: Skill, impl: Any, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """调用 LLM 执行 prompt 类型 Skill。"""
        if not self._llm:
            raise RuntimeError(f"Skill {skill.name} requires an LLM client; use an explicit fixed-response client for offline tests.")
        from .request_body_validation import prepare_request_body_validation
        body_evidence = prepare_request_body_validation(skill, input_data)
        context = getattr(getattr(skill, "provenance", None), "creation_context", {})
        instruction_demo = impl.prompt_format is None and is_literal_instruction_skill(skill)
        if instruction_demo:
            # Optimizers return literal instruction text (often containing JSON
            # braces), not Python format strings. Preserve it and pass the task
            # separately, matching the candidate replay protocol.
            from ...api.component_demo import DIALOGUE_TASK_INSTRUCTION
            task = (context.get("demo_components") or {}).get("task_instruction") or DIALOGUE_TASK_INSTRUCTION
            dialogue = str(input_data.get("dialogue", json.dumps(input_data, ensure_ascii=False)))
            messages = [Message.system(impl.prompt_template),
                        Message.user(task + "\nDialogue:\n" + dialogue)]
        else:
            if impl.prompt_format == "literal":
                prompt, has_fields = impl.prompt_template, False
            else:
                try:
                    prompt, has_fields = render_prompt_template(impl.prompt_template, input_data)
                except KeyError as e:
                    raise RuntimeError(f"Skill {skill.name} prompt 模板缺少参数: {e}") from e
            messages = [Message.system(f"你是 SkillWiki 中的 {skill.name} Skill，请严格按照任务要求执行。"), Message.user(prompt)]
            if not has_fields:
                # Generated instructions may have no Python placeholders. Keep
                # their wording and bind the complete input as separate data.
                # Named templates retain their established message contract.
                messages.append(Message.user(
                    "Input data (JSON):" + chr(10)
                    + json.dumps(input_data, ensure_ascii=False, allow_nan=False)
                ))

        response = await asyncio.to_thread(
            self._llm.chat,
            messages,
        )
        result_text = response.content
        if body_evidence is not None:
            from .request_body_validation import finalize_request_body_validation
            final_result, evidence = finalize_request_body_validation(skill, input_data, result_text, body_evidence)
            return _RuntimeOutput(final_result, state_changes={f"{skill.name}_result": final_result}, execution_evidence=evidence)
        parsed_result = _extract_json_object(result_text)
        if parsed_result is not None:
            return _RuntimeOutput(parsed_result, state_changes={f"{skill.name}_result": parsed_result})
        return _RuntimeOutput({"result": result_text}, state_changes={f"{skill.name}_result": result_text})

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
        if not isinstance(output, dict):
            raise RuntimeError(f"Skill {skill.name} code must produce a dict output")
        return _RuntimeOutput(output, state_changes={f"{skill.name}_executed": True, **output})

    async def _run_composite_skill(
        self, skill: Skill, impl: Any, input_data: Dict[str, Any], current_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """递归执行 composite/functional Skill 的子 Skill。"""
        if not self._registry:
            raise RuntimeError(f"Skill {skill.name} 是 composite 类型但执行器未配置 registry")

        sub_results: Dict[str, Any] = {}
        sub_evidence: Dict[str, Any] = {}
        merged_state = copy.deepcopy(current_state)

        resolved = await self._resolve_subskills(skill)
        for _, sub_skill in resolved:
            await self._validate_skill_tree_inputs(sub_skill, input_data, {skill.skill_id})

        for sub_id, sub_skill in resolved:
            sub_result = await self._run_skill(sub_skill, input_data, merged_state)
            sub_results[sub_id] = dict(sub_result)
            if getattr(sub_result, "execution_evidence", None):
                sub_evidence[sub_id] = copy.deepcopy(sub_result.execution_evidence)
            # 将子 Skill 的状态变更合并到当前状态
            merged_state.update(_output_state_changes(sub_result))

        return _RuntimeOutput({
            "sub_results": sub_results,
        }, state_changes={"composite_executed": True, **merged_state},
           execution_evidence={"sub_skills": sub_evidence} if sub_evidence else {})

    async def _resolve_subskills(self, skill: Skill) -> List[tuple[str, Skill]]:
        if not self._registry:
            raise RuntimeError(f"Skill {skill.name} 是 composite 类型但执行器未配置 registry")
        resolved: List[tuple[str, Skill]] = []
        sub_ids = skill.implementation.sub_skill_ids
        order = skill.implementation.execution_order
        if order is not None:
            from collections import Counter
            if Counter(order) != Counter(sub_ids):
                raise InputContractError("execution_order must be a sequential permutation of sub_skill_ids; parallel markers are unsupported")
            sub_ids = order
        for sub_id in sub_ids:
            sub_skill = await self._registry.get(sub_id)
            if not sub_skill and hasattr(self._registry, "get_by_name"):
                sub_skill = await self._registry.get_by_name(sub_id)
            if not sub_skill:
                raise RuntimeError(f"Required sub-skill not found: {sub_id}")
            resolved.append((sub_id, sub_skill))

        return resolved

    async def _validate_skill_tree_inputs(
        self, skill: Skill, input_data: Dict[str, Any], ancestors: Optional[set[str]] = None,
    ) -> None:
        """Check all concrete child inputs before any sibling dispatch occurs."""
        validate_skill_input(skill, input_data)
        ancestors = ancestors or set()
        if skill.skill_id in ancestors:
            raise InputContractError(f"Cyclic composite input validation at Skill {skill.name}")
        impl = skill.implementation
        if impl and impl.request_body_validation is not None:
            from .request_body_validation import prepare_request_body_validation
            prepare_request_body_validation(skill, input_data)
        # Match native dispatch precedence; inactive child metadata is not run.
        if impl and not impl.prompt_template and not impl.code and impl.sub_skill_ids:
            for _, child in await self._resolve_subskills(skill):
                await self._validate_skill_tree_inputs(child, input_data, ancestors | {skill.skill_id})

    async def execute_single(
        self,
        skill: Skill,
        input_data: Dict[str, Any],
        task_id: Optional[str] = None,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> SkillExecutionRecord:
        """执行单个 Skill（不需要完整计划）。"""
        record = SkillExecutionRecord(
            skill_id=skill.skill_id,
            skill_version=skill.version,
            task_id=task_id or str(uuid.uuid4()),
            input_data=input_data,
            state_before=copy.deepcopy(initial_state or {}),
        )
        record.start()
        try:
            output = await asyncio.wait_for(
                self._run_skill(skill, input_data, copy.deepcopy(initial_state or {})),
                timeout=self._step_timeout,
            )
            tracker = StateTracker(record.task_id, initial_state)
            tracker.update(_output_state_changes(output))
            record.complete(dict(output), tracker.current)
            record.execution_evidence = copy.deepcopy(
                getattr(output, "execution_evidence", {})
            )
        except Exception as e:
            record.state_after = copy.deepcopy(record.state_before)
            record.fail(str(e), type(e).__name__)
            record.execution_evidence = copy.deepcopy(getattr(e, "execution_evidence", {}))
        return record


def _protect_literal_json(template: str) -> str:
    """Escape valid literal JSON objects without swallowing format fields.

    Existing doubled braces keep Python format semantics. A raw JSON object
    is data, including strings such as "{name}" inside it; a neighboring
    {name} remains a named field and missing arguments still raise KeyError.
    """
    decoder = json.JSONDecoder()
    parts: List[str] = []
    index = 0
    while index < len(template):
        pair = template[index:index + 2]
        if pair in ("{{", "}}"):
            parts.append(pair)
            index += 2
            continue
        if template[index] == "{":
            try:
                value, end = decoder.raw_decode(template, index)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(value, dict):
                    literal = template[index:end]
                    parts.append(literal.replace("{", "{{").replace("}", "}}"))
                    index = end
                    continue
        parts.append(template[index])
        index += 1
    return "".join(parts)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Parse only a complete JSON answer, never an example inside prose."""
    stripped = str(text or "").strip()
    if not stripped:
        return None
    candidate = stripped
    if stripped.startswith("```") and stripped.endswith("```"):
        # A single complete fence is an answer; surrounding explanations or
        # multiple fences must remain intact so validation errors are not lost.
        if stripped.count("```") != 2:
            return None
        inner = stripped[3:-3].strip()
        if inner.startswith("json"):
            inner = inner[4:].lstrip()
        candidate = inner
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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
