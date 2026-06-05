"""Skill 执行器 — 按执行计划运行 Skill，管理状态和错误处理。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import platform
import re
import shlex
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
from urllib.parse import quote_plus

from ...models.experience_model import ExecutionStatus, SkillExecutionRecord
from ...models.skill_model import Skill
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger
from .observation import ObservationManager, judge_step_observation
from .planner import ExecutionPlan, PlanStep, StepStatus
from .state_tracker import StateTracker

logger = get_logger(__name__)

# 执行事件类型（用于 WebSocket 实时推送）
ExecutionEventCallback = Callable[[str, Dict[str, Any]], None]


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
        self._observations = ObservationManager()

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
            "task": plan.task_description,
            "total_steps": plan.total_steps,
        })

        while not plan.is_complete and not plan.has_failures:
            ready_steps = plan.get_ready_steps()
            if not ready_steps:
                break

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
                    else:
                        record = result
                        if record:
                            execution_records.append(record)
            else:
                record = await self._execute_step(ready_steps[0], skill_map, tracker)
                if record:
                    execution_records.append(record)

        final_state = tracker.current
        self._emit("plan_completed", {
            "plan_id": plan.plan_id,
            "success": plan.is_complete and not plan.has_failures,
            "summary": plan.to_summary(),
            "final_state": final_state,
        })

        return final_state

    async def _execute_step(
        self,
        step: PlanStep,
        skill_map: Dict[str, Skill],
        tracker: StateTracker,
    ) -> Optional[SkillExecutionRecord]:
        """执行单个步骤（含重试）。"""
        skill = skill_map.get(step.skill_id)
        if not skill:
            step.status = StepStatus.FAILED
            step.error = f"Skill 不存在: {step.skill_id}"
            self._emit("step_failed", {"step_id": step.step_id, "error": step.error})
            return None

        step.status = StepStatus.RUNNING
        step.started_at = datetime.utcnow()
        self._emit("step_started", {
            "step_id": step.step_id,
            "step_index": step.step_index,
            "skill_name": skill.name,
            "input": step.input_mapping,
        })
        before_observation = self._observations.collect(
            phase="before",
            step=step,
            skill=skill,
            state=tracker.current,
        )
        step.observations.append(before_observation)
        self._emit("step_observed", before_observation)

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

        max_attempts = 1 if self._is_non_idempotent_host_skill(skill) else self._max_retries + 1
        for attempt in range(max_attempts):
            try:
                output = await asyncio.wait_for(
                    self._run_skill(skill, step.input_mapping, tracker.current),
                    timeout=self._step_timeout,
                )
                state_changes = output.get("_state_changes", {})
                tracker.update(state_changes)
                tracker.snapshot_after(skill.skill_id, skill.name)

                host_failed = _host_output_failed(output)
                step.status = StepStatus.FAILED if host_failed else StepStatus.SUCCESS
                step.result = output
                step.completed_at = datetime.utcnow()
                after_observation = self._observations.collect(
                    phase="after",
                    step=step,
                    skill=skill,
                    state=tracker.current,
                    output=output,
                )
                step.observations.append(after_observation)
                step.step_judgment = judge_step_observation(step, output, after_observation)
                if host_failed:
                    step.error = _host_failure_reason(output)
                    record.fail(step.error, "HostActionIncomplete")
                    self._emit("step_observed", after_observation)
                    self._emit("step_judged", {
                        "step_id": step.step_id,
                        "skill_name": skill.name,
                        "judgment": step.step_judgment,
                    })
                    self._emit("step_failed", {
                        "step_id": step.step_id,
                        "skill_name": skill.name,
                        "output": output,
                        "observation": after_observation,
                        "judgment": step.step_judgment,
                        "error": step.error,
                    })
                    return record
                if step.step_judgment.get("next_action") == "repair" and attempt + 1 < max_attempts:
                    repair = _repair_step_input(step, output, after_observation, step.step_judgment)
                    self._emit("step_repairing", {
                        "step_id": step.step_id,
                        "skill_name": skill.name,
                        "judgment": step.step_judgment,
                        "repair": repair,
                        "attempt": attempt + 1,
                    })
                    if repair:
                        step.input_mapping.update(repair)
                    step.status = StepStatus.RUNNING
                    continue

                record.complete(output, tracker.current)
                self._emit("step_observed", after_observation)
                self._emit("step_judged", {
                    "step_id": step.step_id,
                    "skill_name": skill.name,
                    "judgment": step.step_judgment,
                })
                self._emit("step_completed", {
                    "step_id": step.step_id,
                    "skill_name": skill.name,
                    "output": output,
                    "observation": after_observation,
                    "judgment": step.step_judgment,
                    "latency_ms": step.latency_ms,
                })
                return record

            except asyncio.TimeoutError:
                error = f"步骤超时 ({self._step_timeout}s)"
                if attempt + 1 < max_attempts:
                    logger.warning(f"步骤超时，重试 {attempt + 1}/{max_attempts - 1}: {skill.name}")
                    continue
                tracker.rollback()
                step.status = StepStatus.FAILED
                step.error = error
                step.completed_at = datetime.utcnow()
                record.fail(error, "TimeoutError")
                error_observation = self._observations.collect(
                    phase="error",
                    step=step,
                    skill=skill,
                    state=tracker.current,
                    error=error,
                )
                step.observations.append(error_observation)
                self._emit("step_observed", error_observation)
                self._emit("step_failed", {"step_id": step.step_id, "error": error})
                return record

            except Exception as e:
                error = str(e)
                if attempt + 1 < max_attempts:
                    logger.warning(f"步骤失败，重试 {attempt + 1}/{max_attempts - 1}: {skill.name} - {error}")
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                tracker.rollback()
                step.status = StepStatus.FAILED
                step.error = error
                step.completed_at = datetime.utcnow()
                record.fail(error, type(e).__name__)
                error_observation = self._observations.collect(
                    phase="error",
                    step=step,
                    skill=skill,
                    state=tracker.current,
                    error=error,
                )
                step.observations.append(error_observation)
                self._emit("step_observed", error_observation)
                self._emit("step_failed", {"step_id": step.step_id, "error": error})
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

        # 0. allowlisted host tool calls -> controlled host-side actions.
        # These are intentionally explicit so a Skill cannot execute arbitrary
        # shell commands just by storing Python code in the repository.
        if impl.tool_calls:
            return await self._run_host_tool_skill(skill, impl, input_data, current_state)

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

    async def _run_host_tool_skill(
        self,
        skill: Skill,
        impl: Any,
        input_data: Dict[str, Any],
        current_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run an explicitly allowlisted host tool referenced by a Skill."""
        effective_input = _merge_schema_defaults(skill, input_data)
        tool_names = {str(name).strip().lower() for name in impl.tool_calls}
        if "host.open_chrome" in tool_names:
            result = await asyncio.to_thread(_open_chrome_browser)
        elif "host.open_application" in tool_names:
            result = await asyncio.to_thread(_open_application, effective_input)
        elif "host.open_url_in_chrome" in tool_names:
            result = await asyncio.to_thread(_open_url_in_chrome, effective_input)
        elif "host.open_file" in tool_names:
            result = await asyncio.to_thread(_open_file, effective_input)
        elif "host.move_to_trash" in tool_names:
            result = await asyncio.to_thread(_move_to_trash, effective_input)
        elif "host.open_or_create_file_in_vscode" in tool_names:
            result = await asyncio.to_thread(_open_or_create_file_in_vscode, effective_input)
        elif "host.create_wps_document_from_text_file" in tool_names:
            result = await asyncio.to_thread(_create_wps_document_from_text_file, effective_input)
        elif "host.write_downloads_text_file" in tool_names:
            result = await asyncio.to_thread(_write_downloads_text_file, effective_input)
        elif "host.open_downloads_folder" in tool_names:
            result = await asyncio.to_thread(_open_downloads_folder)
        elif "host.complete_chatgpt_note_task" in tool_names:
            result = await asyncio.to_thread(_complete_chatgpt_note_task, effective_input)
        elif "host.run_terminal_top" in tool_names:
            result = await asyncio.to_thread(_run_terminal_top, effective_input)
        elif "host.run_terminal_command" in tool_names:
            result = await asyncio.to_thread(_run_terminal_command, effective_input)
        elif "host.open_search_first_result" in tool_names:
            result = await asyncio.to_thread(_open_search_first_result, effective_input)
        elif "host.browser_gui_workflow" in tool_names:
            result = await asyncio.to_thread(_run_browser_gui_workflow, effective_input)
        elif "host.desktop_gui_resume" in tool_names:
            result = await asyncio.to_thread(resume_desktop_gui_workflow, effective_input)
        else:
            result = None

        if result is not None:
            return {
                **result,
                "skill_name": skill.name,
                "_state_changes": {
                    f"{skill.name}_executed": result.get("success", result.get("launched", False)),
                    "last_host_action": result.get("host_action", skill.name),
                    **result,
                },
            }

        logger.info(
            "Skill %s references non-host tool calls %s; falling back to implementation.",
            skill.name,
            impl.tool_calls,
        )
        if impl.prompt_template:
            return await self._run_prompt_skill(skill, impl, input_data)
        if impl.code:
            return await self._run_code_skill(skill, impl, input_data, current_state)
        if impl.sub_skill_ids:
            return await self._run_composite_skill(skill, impl, input_data, current_state)
        raise RuntimeError(f"Skill {skill.name} references unsupported host tools: {impl.tool_calls}")

    @staticmethod
    def _is_non_idempotent_host_skill(skill: Skill) -> bool:
        if not skill.implementation:
            return False
        tool_calls = {str(name).strip().lower() for name in skill.implementation.tool_calls}
        side_effect_tools = {
            "host.open_chrome",
            "host.open_application",
            "host.open_url_in_chrome",
            "host.open_file",
            "host.move_to_trash",
            "host.open_or_create_file_in_vscode",
            "host.create_wps_document_from_text_file",
            "host.write_downloads_text_file",
            "host.open_downloads_folder",
            "host.complete_chatgpt_note_task",
            "host.run_terminal_top",
            "host.run_terminal_command",
            "host.open_search_first_result",
            "host.browser_gui_workflow",
            "host.desktop_gui_resume",
        }
        return bool(tool_calls & side_effect_tools)


def _host_output_failed(output: Dict[str, Any]) -> bool:
    host_action = str(output.get("host_action") or "")
    if not host_action:
        return False
    explicit_failure_actions = {
        "browser_gui_workflow",
        "move_to_trash",
        "create_wps_document_from_text_file",
        "open_or_create_file_in_vscode",
        "write_downloads_text_file",
    }
    return host_action in explicit_failure_actions and output.get("success") is False


def _host_failure_reason(output: Dict[str, Any]) -> str:
    actions = output.get("actions")
    if isinstance(actions, list):
        fallback_successes = [
            action for action in actions
            if isinstance(action, dict)
            and isinstance(action.get("execution"), dict)
            and str(action["execution"].get("action", "")).endswith("_fallback")
            and action["execution"].get("status") == "success"
        ]
        for action in reversed(actions):
            if not isinstance(action, dict):
                continue
            execution = action.get("execution")
            if isinstance(execution, dict) and execution.get("status") in {"blocked", "failed"}:
                if fallback_successes:
                    return "Search target fallback opened candidate pages, but DOM/visual verification is still required to confirm and click the final visible target."
                return str(execution.get("reason") or execution.get("error") or action.get("reason") or "Host action blocked.")
            if action.get("status") in {"blocked", "failed"}:
                if fallback_successes:
                    return "Search target fallback opened candidate pages, but DOM/visual verification is still required to confirm and click the final visible target."
                return str(action.get("reason") or "Host action blocked.")
    return str(output.get("reason") or output.get("message") or f"Host action {output.get('host_action')} did not satisfy the task.")

    async def _run_prompt_skill(self, skill: Skill, impl: Any, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """调用 LLM 执行 prompt 类型 Skill。"""
        if not self._llm or _is_demo_llm(self._llm):
            # 无 LLM 客户端时返回模拟结果（测试/离线模式）
            return {
                "result": f"[mock] {skill.name} executed",
                "skill_name": skill.name,
                "_state_changes": {f"{skill.name}_executed": True},
            }
        try:
            prompt = impl.prompt_template.format(**input_data)
        except KeyError as e:
            logger.warning("Skill prompt template missing input %s; using raw template for demo execution.", e)
            prompt = impl.prompt_template

        try:
            response = await asyncio.to_thread(
                self._llm.chat,
                [Message.system(f"你是 SkillOS 中的 {skill.name} Skill，请严格按照任务要求执行。"),
                 Message.user(prompt)],
            )
            result_text = response.content
        except Exception as exc:
            logger.warning("Skill prompt execution fell back to deterministic demo output: %s", exc)
            result_text = f"[demo] {skill.display_name} executed with inputs: {input_data}"
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


def _is_demo_llm(llm_client: LLMClient) -> bool:
    api_key = str(getattr(getattr(llm_client, "_cfg", None), "api_key", ""))
    return api_key.startswith("local-") or api_key.startswith("demo-")


def _repair_step_input(
    step: PlanStep,
    output: Dict[str, Any],
    observation_packet: Dict[str, Any],
    judgment: Dict[str, Any],
) -> Dict[str, Any]:
    """Small deterministic repair hooks before escalating to a future LLM repair agent."""
    host_action = str(output.get("host_action") or judgment.get("host_action") or "")
    if host_action == "move_to_trash":
        path = str(step.input_mapping.get("path") or output.get("path") or "")
        if path and not Path(path).expanduser().exists():
            return {}
    if host_action == "run_terminal_command" and not output.get("success"):
        command = str(step.input_mapping.get("command") or "")
        if command.startswith("ls ") and "~/" in command:
            return {"command": command.replace("~/", f"{Path.home()}/", 1)}
    return {}


def _merge_schema_defaults(skill: Skill, input_data: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(input_data)
    properties = skill.interface.input_schema.get("properties", {}) if skill.interface else {}
    if isinstance(properties, dict):
        for key, spec in properties.items():
            if key in merged and merged[key] not in (None, ""):
                continue
            if isinstance(spec, dict) and "default" in spec:
                merged[key] = spec["default"]
    return merged


def _open_chrome_browser() -> Dict[str, Any]:
    """Open Google Chrome through a small, platform-specific allowlist."""
    result = _open_application({"application": "Google Chrome"})
    return {
        **result,
        "host_action": "open_chrome_browser",
        "chrome_browser_opened": result.get("launched", False),
    }


def _open_application(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Open a host application through a platform-specific allowlist."""
    app_name = str(
        input_data.get("application")
        or input_data.get("app_name")
        or _infer_application_name(str(input_data.get("goal", "")))
        or ""
    ).strip()
    if not app_name:
        raise RuntimeError("Missing application/app_name for host.open_application")

    system = platform.system().lower()
    candidates = _application_name_candidates(app_name)
    if system == "darwin":
        settings_pane_url = str(input_data.get("settings_pane_url") or "").strip()
        if settings_pane_url:
            return _open_macos_settings_pane(
                settings_pane_url,
                feature=str(input_data.get("setting_feature") or ""),
                requested_application=app_name,
            )
        launcher = str(input_data.get("launcher") or "").lower()
        if "spotlight" in launcher or "聚焦" in launcher:
            spotlight_query = str(input_data.get("application_query") or _spotlight_query_for_application(app_name)).strip()
            spotlight_result = _open_application_via_spotlight(spotlight_query or app_name, requested_application=app_name)
            if spotlight_result.get("success"):
                return spotlight_result
        commands = [["open", "-a", candidate] for candidate in candidates]
    elif system == "windows":
        commands = [["cmd", "/c", "start", "", candidate] for candidate in candidates]
    else:
        binary = next(
            (
                shutil.which(candidate) or shutil.which(candidate.lower().replace(" ", "-"))
                for candidate in candidates
                if shutil.which(candidate) or shutil.which(candidate.lower().replace(" ", "-"))
            ),
            None,
        )
        if not binary:
            raise RuntimeError(f"Application executable was not found on this host: {app_name}")
        commands = [[binary]]

    last_error = ""
    launched_name = app_name
    command = commands[0]
    for command in commands:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if completed.returncode == 0:
            if len(command) >= 3 and command[0] == "open" and command[1] == "-a":
                launched_name = command[2]
            break
        last_error = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
    else:
        raise RuntimeError(f"Failed to open application '{app_name}': {last_error}")

    return {
        "success": True,
        "launched": True,
        "host_action": "open_application",
        "application": launched_name,
        "requested_application": app_name,
        "platform": system,
        "command": " ".join(command),
        "message": f"{launched_name} open request was sent to the host OS.",
    }


def _application_name_candidates(app_name: str) -> List[str]:
    normalized = app_name.strip()
    aliases = {
        "wps office": ["WPS Office", "WPS", "wpsoffice"],
        "wps": ["WPS", "WPS Office", "wpsoffice"],
        "system settings": ["System Settings"],
        "settings": ["System Settings"],
        "系统设置": ["System Settings"],
        "设置": ["System Settings"],
        "google chrome": ["Google Chrome", "Chrome"],
        "chrome": ["Google Chrome", "Chrome"],
        "visual studio code": ["Visual Studio Code", "Code", "vscode"],
        "vscode": ["Visual Studio Code", "Code", "vscode"],
        "vs code": ["Visual Studio Code", "Code", "vscode"],
        "terminal": ["Terminal"],
        "finder": ["Finder"],
    }
    candidates = aliases.get(normalized.lower(), [normalized])
    deduped: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _open_macos_settings_pane(url: str, *, feature: str, requested_application: str) -> Dict[str, Any]:
    command = ["open", url]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        fallback = subprocess.run(["open", "-a", "System Settings"], capture_output=True, text=True, timeout=10)
        if fallback.returncode != 0:
            stderr = fallback.stderr.strip() or completed.stderr.strip() or "unknown error"
            raise RuntimeError(f"Failed to open System Settings: {stderr}")
        return {
            "success": True,
            "launched": True,
            "host_action": "open_application",
            "application": "System Settings",
            "requested_application": requested_application,
            "setting_feature": feature or "Settings",
            "settings_pane_url": url,
            "platform": "darwin",
            "command": "open -a System Settings",
            "message": "System Settings open request was sent; pane deep link was unavailable.",
        }
    return {
        "success": True,
        "launched": True,
        "host_action": "open_application",
        "application": "System Settings",
        "requested_application": requested_application,
        "setting_feature": feature or "Settings",
        "settings_pane_url": url,
        "platform": "darwin",
        "command": " ".join(command),
        "message": f"System Settings pane open request was sent: {feature or url}",
    }


def _spotlight_query_for_application(app_name: str) -> str:
    aliases = {
        "wps office": "wps",
        "wps": "wps",
        "wpsoffice": "wps",
        "google chrome": "chrome",
        "chrome": "chrome",
        "visual studio code": "vscode",
        "vscode": "vscode",
        "vs code": "vscode",
        "terminal": "terminal",
        "finder": "finder",
    }
    return aliases.get(app_name.strip().lower(), app_name.strip())


def _open_application_via_spotlight(query: str, *, requested_application: str) -> Dict[str, Any]:
    """Use macOS Spotlight keystrokes when the task explicitly requested Spotlight."""
    script = [
        'tell application "System Events"',
        'key code 49 using {command down}',
        'delay 0.25',
        f'keystroke {json.dumps(query)}',
        'delay 0.35',
        'key code 36',
        'end tell',
    ]
    command: List[str] = ["osascript"]
    for statement in script:
        command.extend(["-e", statement])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        return {
            "success": False,
            "host_action": "open_application",
            "application": requested_application,
            "requested_application": requested_application,
            "launcher": "macOS Spotlight",
            "platform": "darwin",
            "command": " ".join(command),
            "message": completed.stderr.strip() or completed.stdout.strip() or "Spotlight launch failed",
        }
    return {
        "success": True,
        "launched": True,
        "host_action": "open_application",
        "application": requested_application,
        "requested_application": requested_application,
        "launcher": "macOS Spotlight",
        "platform": "darwin",
        "command": " ".join(command),
        "message": f"Spotlight launch request was sent for: {query}",
    }


def _open_url_in_chrome(input_data: Dict[str, Any]) -> Dict[str, Any]:
    url = str(input_data.get("url") or _infer_url(str(input_data.get("goal", "")))).strip()
    if not url:
        raise RuntimeError("Missing url for host.open_url_in_chrome")
    if not url.startswith(("http://", "https://", "chrome://")):
        url = f"https://{url}"

    system = platform.system().lower()
    if system == "darwin":
        command = ["open", "-a", "Google Chrome", url]
    elif system == "windows":
        command = ["cmd", "/c", "start", "", "chrome", url]
    else:
        binary = next(
            (
                candidate
                for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
                if shutil.which(candidate)
            ),
            None,
        )
        if not binary:
            raise RuntimeError("Chrome/Chromium executable was not found on this host")
        command = [binary, url]

    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to open Chrome URL: {stderr}")

    return {
        "success": True,
        "launched": True,
        "host_action": "open_url_in_chrome",
        "application": "Google Chrome",
        "url": url,
        "platform": system,
        "command": " ".join(command),
        "message": f"Chrome URL open request was sent to the host OS: {url}",
    }


def _open_file(input_data: Dict[str, Any]) -> Dict[str, Any]:
    path = _expand_host_path(str(input_data.get("path") or input_data.get("file_path") or ""))
    if not path:
        raise RuntimeError("Missing path/file_path for host.open_file")
    if not path.exists():
        raise RuntimeError(f"File does not exist: {path}")

    system = platform.system().lower()
    if system == "darwin":
        command = ["open", str(path)]
    elif system == "windows":
        command = ["cmd", "/c", "start", "", str(path)]
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("xdg-open was not found on this host")
        command = [opener, str(path)]

    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to open file: {stderr}")

    return {
        "success": True,
        "launched": True,
        "host_action": "open_file",
        "path": str(path),
        "platform": system,
        "command": " ".join(command),
        "message": f"File open request was sent to the host OS: {path}",
    }


def _move_to_trash(input_data: Dict[str, Any]) -> Dict[str, Any]:
    path = _expand_host_path(str(input_data.get("path") or input_data.get("file_path") or ""))
    if not path:
        raise RuntimeError("Missing path/file_path for host.move_to_trash")
    if not path.exists():
        raise RuntimeError(f"File does not exist: {path}")

    system = platform.system().lower()
    if system == "darwin":
        script = (
            'tell application "Finder" to delete '
            f'(POSIX file {json.dumps(str(path))})'
        )
        command = ["osascript", "-e", script]
    elif system == "windows":
        raise RuntimeError("host.move_to_trash is not implemented on Windows yet")
    else:
        trash = shutil.which("gio") or shutil.which("kioclient5") or shutil.which("trash-put")
        if not trash:
            raise RuntimeError("No supported Trash command was found on this host")
        if Path(trash).name == "gio":
            command = [trash, "trash", str(path)]
        elif Path(trash).name == "kioclient5":
            command = [trash, "move", str(path), "trash:/"]
        else:
            command = [trash, str(path)]

    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to move path to Trash: {stderr}")

    return {
        "success": True,
        "launched": False,
        "host_action": "move_to_trash",
        "path": str(path),
        "platform": system,
        "command": " ".join(shlex.quote(part) for part in command),
        "message": f"Path was moved to Trash: {path}",
    }


def _create_wps_document_from_text_file(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a WPS-openable document from a source text file and open it.

    For the demo this uses an RTF document because it is easy to generate
    safely and WPS/Word/TextEdit can all open it. The Skill still represents
    the user-facing workflow: new blank document, copy source text, save to
    Desktop, open in WPS when available.
    """
    source = _expand_host_path(str(
        input_data.get("source_path")
        or input_data.get("path")
        or input_data.get("file_path")
        or ""
    ))
    if not source:
        source = Path.home() / "Desktop" / "111.txt"
    if not source.exists():
        raise RuntimeError(f"Source text file does not exist: {source}")
    if not source.is_file():
        raise RuntimeError(f"Source path is not a file: {source}")

    raw_output = str(input_data.get("output_path") or input_data.get("target_path") or "").strip()
    output_path = Path(raw_output).expanduser() if raw_output else Path.home() / "Desktop" / "wps_111_document.rtf"
    if not output_path.is_absolute():
        output_path = Path.home() / "Desktop" / output_path
    if output_path.suffix.lower() not in {".rtf", ".doc", ".docx", ".txt"}:
        output_path = output_path.with_suffix(".rtf")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = source.read_text(encoding="utf-8", errors="replace")
    output_path.write_text(_rtf_document(text), encoding="utf-8")

    open_result = _open_document_application(output_path, preferred_app=str(input_data.get("application") or "WPS Office"))
    return {
        "success": True,
        "launched": bool(open_result.get("launched")),
        "host_action": "create_wps_document_from_text_file",
        "application": open_result.get("application") or "WPS Office",
        "source_path": str(source),
        "path": str(output_path),
        "output_path": str(output_path),
        "bytes_written": output_path.stat().st_size,
        "source_chars": len(text),
        "platform": platform.system().lower(),
        "command": open_result.get("command"),
        "message": f"Created a WPS-openable document on Desktop from {source.name}: {output_path.name}",
    }


def _open_document_application(path: Path, *, preferred_app: str) -> Dict[str, Any]:
    system = platform.system().lower()
    if system == "darwin":
        candidates = _application_name_candidates(preferred_app)
        last_error = ""
        for app in candidates:
            command = ["open", "-a", app, str(path)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
            if completed.returncode == 0:
                return {
                    "success": True,
                    "launched": True,
                    "application": app,
                    "command": " ".join(shlex.quote(part) for part in command),
                }
            last_error = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        fallback = ["open", str(path)]
        completed = subprocess.run(fallback, capture_output=True, text=True, timeout=10)
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or last_error or "unknown error"
            raise RuntimeError(f"Failed to open generated document: {stderr}")
        return {
            "success": True,
            "launched": True,
            "application": "default document application",
            "command": " ".join(shlex.quote(part) for part in fallback),
        }
    return _open_file({"path": str(path)})


def _rtf_document(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\par\n")
    )
    return "{\\rtf1\\ansi\\deff0\n{\\fonttbl{\\f0 Helvetica;}}\n\\f0\\fs24\n" + escaped + "\n}\n"


def _open_or_create_file_in_vscode(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Check/create a local text file and open it in VS Code via the code command when available."""
    path = _resolve_vscode_file_path(input_data)
    if not path:
        raise RuntimeError("Missing path/filename for host.open_or_create_file_in_vscode")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed_before = path.exists()
    if not existed_before:
        path.write_text("", encoding="utf-8")

    system = platform.system().lower()
    command_text = f"code {shlex.quote(str(path))}"
    terminal_used = False
    fallback_used = False
    if system == "darwin" and shutil.which("code"):
        terminal_script = f"{command_text}; echo; echo '[SkillOS] VS Code file workflow completed.'"
        launch_command = [
            "osascript",
            "-e",
            'tell application "Terminal" to activate',
            "-e",
            f'tell application "Terminal" to do script {json.dumps(terminal_script)}',
        ]
        terminal_used = True
    elif system == "darwin":
        launch_command = ["open", "-a", "Visual Studio Code", str(path)]
        fallback_used = True
    elif system == "windows":
        if shutil.which("code"):
            launch_command = ["cmd", "/c", "start", "", "cmd", "/k", command_text]
            terminal_used = True
        else:
            launch_command = ["cmd", "/c", "start", "", str(path)]
            fallback_used = True
    else:
        code_binary = shutil.which("code")
        if code_binary:
            terminal = next(
                (candidate for candidate in ("gnome-terminal", "konsole", "xfce4-terminal", "xterm") if shutil.which(candidate)),
                None,
            )
            if terminal == "xterm":
                launch_command = [terminal, "-e", command_text]
            elif terminal:
                launch_command = [terminal, "--", "bash", "-lc", f"{command_text}; exec bash"]
            else:
                launch_command = [code_binary, str(path)]
            terminal_used = bool(terminal)
        else:
            raise RuntimeError("VS Code CLI 'code' was not found on this host")

    completed = subprocess.run(launch_command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to open VS Code file workflow: {stderr}")

    return {
        "success": True,
        "launched": True,
        "host_action": "open_or_create_file_in_vscode",
        "application": "Visual Studio Code",
        "path": str(path),
        "filename": path.name,
        "existed_before": existed_before,
        "created": not existed_before,
        "terminal_used": terminal_used,
        "fallback_used": fallback_used,
        "command": command_text,
        "platform": system,
        "message": (
            f"Checked {path.name}, {'created it and ' if not existed_before else ''}"
            "opened it in VS Code."
        ),
    }


def _write_downloads_text_file(input_data: Dict[str, Any]) -> Dict[str, Any]:
    filename = str(
        input_data.get("filename")
        or input_data.get("file_name")
        or _infer_downloads_filename(str(input_data.get("goal", "")))
    ).strip()
    if not filename:
        filename = "skillos_answer.txt"
    if not filename.endswith(".txt"):
        filename = f"{filename}.txt"

    content = str(
        input_data.get("content")
        or input_data.get("answer")
        or _default_answer_content(str(input_data.get("goal", "")))
    )
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    path = downloads / Path(filename).name
    path.write_text(content, encoding="utf-8")
    return {
        "success": True,
        "host_action": "write_downloads_text_file",
        "path": str(path),
        "filename": path.name,
        "bytes_written": len(content.encode("utf-8")),
        "message": f"Text file was written to Downloads: {path.name}",
    }


def _open_downloads_folder() -> Dict[str, Any]:
    return _open_file({"path": str(Path.home() / "Downloads")})


def _complete_chatgpt_note_task(input_data: Dict[str, Any]) -> Dict[str, Any]:
    goal = str(input_data.get("goal", ""))
    question = str(input_data.get("question") or _infer_question(goal)).strip()
    url_result = _open_url_in_chrome({"url": "https://chatgpt.com/", "goal": goal})
    filename = str(input_data.get("filename") or _infer_downloads_filename(goal, default="gpt_task_answer.txt"))
    answer = str(input_data.get("answer") or _default_answer_content(goal, question=question))
    file_result = _write_downloads_text_file({"filename": filename, "content": answer})
    return {
        "success": True,
        "host_action": "complete_chatgpt_note_task",
        "opened_url": url_result.get("url"),
        "saved_path": file_result.get("path"),
        "filename": file_result.get("filename"),
        "question": question,
        "answer_preview": answer[:240],
        "message": "Opened ChatGPT in Chrome and saved the task answer note to Downloads.",
    }


def _run_terminal_top(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Open a terminal and run top long enough for the dashboard runtime to stay visible."""
    duration = _coerce_duration_seconds(input_data.get("duration_seconds") or input_data.get("duration"), default=10)
    sample_count = max(3, min(duration, 30))
    system = platform.system().lower()

    if system == "darwin":
        top_command = f"top -o cpu -l {sample_count}"
        command = [
            "osascript",
            "-e",
            'tell application "Terminal" to activate',
            "-e",
            f'tell application "Terminal" to do script "{top_command}"',
        ]
    elif system == "windows":
        top_command = (
            "for ($i=0; $i -lt "
            f"{sample_count}; $i++) {{ Get-Process | Sort-Object CPU -Descending | Select-Object -First 20; Start-Sleep -Seconds 1; Clear-Host }}"
        )
        command = ["powershell", "-NoExit", "-Command", top_command]
    else:
        terminal = next(
            (candidate for candidate in ("gnome-terminal", "konsole", "xfce4-terminal", "xterm") if shutil.which(candidate)),
            None,
        )
        if not terminal:
            raise RuntimeError("No supported terminal emulator was found on this host")
        top_command = f"top -b -d 1 -n {sample_count}"
        if terminal == "xterm":
            command = [terminal, "-e", top_command]
        else:
            command = [terminal, "--", "bash", "-lc", f"{top_command}; exec bash"]

    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to start terminal top monitor: {stderr}")

    # Keep the host action alive briefly so UI runtime animation can show the active phase.
    time.sleep(min(duration, 20))
    return {
        "success": True,
        "launched": True,
        "host_action": "run_terminal_top",
        "application": "Terminal" if system == "darwin" else "terminal",
        "command": top_command,
        "duration_seconds": duration,
        "platform": system,
        "message": f"Terminal top monitor was started for about {duration} seconds.",
    }


def _run_terminal_command(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Open Terminal and run a generated safe, read-only command."""
    command = str(input_data.get("command") or _infer_terminal_command(str(input_data.get("goal", ""))) or "").strip()
    if not command:
        raise RuntimeError("Missing command for host.run_terminal_command")
    if not _is_safe_terminal_command(command):
        raise RuntimeError(f"Refused unsafe or unsupported terminal command: {command}")

    command_parts = _safe_command_parts(command)
    sensitive_output = Path(command_parts[0]).name in {"printenv", "env"}
    if sensitive_output:
        if not shutil.which(command_parts[0]):
            raise RuntimeError(f"Generated terminal command was not found: {command_parts[0]}")
        stdout_preview = "[redacted] Environment variable values are displayed only in Terminal and are not returned through the API."
        stderr_preview = ""
    else:
        captured = subprocess.run(command_parts, capture_output=True, text=True, timeout=10)
        if captured.returncode != 0:
            stderr = captured.stderr.strip() or captured.stdout.strip() or "unknown error"
            raise RuntimeError(f"Generated terminal command failed: {stderr}")
        stdout_preview = captured.stdout.strip()[:4000]
        stderr_preview = captured.stderr.strip()[:1000]

    system = platform.system().lower()
    if system == "darwin":
        terminal_script = f"{command}; echo; echo '[SkillOS] command completed.'"
        launch_command = [
            "osascript",
            "-e",
            'tell application "Terminal" to activate',
            "-e",
            f'tell application "Terminal" to do script {json.dumps(terminal_script)}',
        ]
    elif system == "windows":
        launch_command = ["cmd", "/c", "start", "", "cmd", "/k", command]
    else:
        terminal = next(
            (candidate for candidate in ("gnome-terminal", "konsole", "xfce4-terminal", "xterm") if shutil.which(candidate)),
            None,
        )
        if not terminal:
            raise RuntimeError("No supported terminal emulator was found on this host")
        if terminal == "xterm":
            launch_command = [terminal, "-e", f"{command}; read -p 'SkillOS command completed. Press Enter to close.'"]
        else:
            launch_command = [terminal, "--", "bash", "-lc", f"{command}; echo; echo '[SkillOS] command completed.'; exec bash"]

    launched = subprocess.run(launch_command, capture_output=True, text=True, timeout=10)
    if launched.returncode != 0:
        stderr = launched.stderr.strip() or launched.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to open Terminal command: {stderr}")

    return {
        "success": True,
        "launched": True,
        "host_action": "run_terminal_command",
        "application": "Terminal" if system == "darwin" else "terminal",
        "command": command,
        "stdout_preview": stdout_preview,
        "stderr_preview": stderr_preview,
        "sensitive_output_redacted": sensitive_output,
        "platform": system,
        "message": f"Terminal command was generated by the agent and launched: {command}",
    }


def _open_search_first_result(input_data: Dict[str, Any]) -> Dict[str, Any]:
    query = str(input_data.get("query") or input_data.get("search_query") or input_data.get("goal") or "").strip()
    if not query:
        raise RuntimeError("Missing query/search_query for host.open_search_first_result")
    target_hint = str(input_data.get("target_hint") or input_data.get("result_hint") or "").strip()
    url = _google_target_result_url(query, target_hint=target_hint)
    result = _open_url_in_chrome({"url": url, "goal": str(input_data.get("goal", ""))})
    return {
        **result,
        "host_action": "open_search_target_result",
        "query": query,
        "target_hint": target_hint,
        "search_url": url,
        "message": f"Requested the search target result for query: {query}",
    }


def _run_browser_gui_workflow(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Run a bounded observe-decide-act browser GUI workflow.

    This is intentionally conservative. It opens a search/navigation entry point,
    records observations after each round, and only performs deterministic safe
    browser actions that can be represented by URL/search navigation in the
    current host runtime. A future browser controller can replace the simulated
    click decisions with accessibility/DOM/screenshot-targeted clicks.
    """
    goal = str(input_data.get("goal") or "")
    query = str(input_data.get("query") or _infer_browser_gui_query(goal) or goal).strip()
    max_rounds = _coerce_duration_seconds(input_data.get("max_rounds"), default=6)
    max_rounds = max(2, min(max_rounds, 8))
    controller = _BrowserGuiController(goal=goal, query=query)
    observations: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []

    entry_url = str(input_data.get("url") or "").strip()
    if not entry_url:
        entry_url = f"https://www.google.com/search?q={quote_plus(query)}"
    open_result = _open_url_in_chrome({"url": entry_url, "goal": goal})
    actions.append({"round": 0, "action": "open_search_or_url", "target": entry_url, "status": "success"})
    time.sleep(0.6)
    observations.append(_browser_workflow_observation(0, "search_results", goal, query, entry_url, controller=controller))

    completed = False
    final_target = ""
    for round_index in range(1, max_rounds + 1):
        before = controller.observe(round_index)
        decision = _browser_workflow_decision(goal, query, round_index, before)
        action_result = controller.act(decision)
        merged_decision = {"round": round_index, **decision, "execution": action_result}
        if action_result.get("status") == "success":
            merged_decision["status"] = "success"
        actions.append(merged_decision)
        time.sleep(0.6 if action_result.get("status") == "success" else 0.15)
        after = controller.observe(round_index)
        observations.append(_browser_workflow_observation(
            round_index,
            decision["observation_type"],
            goal,
            query,
            str(action_result.get("target") or decision.get("target") or entry_url),
            controller=controller,
            dom_snapshot=after,
            action_result=action_result,
        ))
        if decision.get("done"):
            completed = bool(action_result.get("goal_satisfied"))
            final_target = str(decision.get("target") or entry_url)
            break
        if _browser_goal_satisfied(goal, action_result, after):
            completed = True
            final_target = str(action_result.get("target") or decision.get("target") or entry_url)
            break
        time.sleep(0.15)

    return {
        "success": completed,
        "launched": True,
        "host_action": "browser_gui_workflow",
        "application": "Google Chrome",
        "query": query,
        "url": final_target or entry_url,
        "entry_url": entry_url,
        "rounds": len(actions),
        "max_rounds": max_rounds,
        "observations": observations,
        "actions": actions,
        "requires_visual_controller": controller.requires_visual_controller and not completed,
        "controller": controller.summary(),
        "platform": platform.system().lower(),
        "command": open_result.get("command"),
        "message": (
            "Started an observation-driven browser GUI workflow. "
            "This run now attempts DOM-backed browser actions on macOS Chrome and records screenshots/DOM evidence."
        ),
    }


def resume_browser_gui_workflow(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Continue an existing browser GUI workflow from the current page.

    Unlike _run_browser_gui_workflow, this never reopens the entry URL. It
    assumes the previous execution left Chrome at an intermediate state and
    applies the user's guidance to the current page.
    """
    goal = str(input_data.get("goal") or "")
    guidance = str(input_data.get("guidance") or "").strip()
    query = str(input_data.get("query") or _infer_browser_gui_query(goal) or goal).strip()
    controller = _BrowserGuiController(goal=goal, query=query)
    observations: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []

    before = controller.observe(0)
    observations.append(_browser_workflow_observation(
        0,
        "resume_current_page",
        goal,
        query,
        str(before.get("url") or input_data.get("url") or "current browser page"),
        controller=controller,
        dom_snapshot=before,
        action_result={"status": "observed", "guidance": guidance},
    ))

    decision = _browser_resume_decision(goal, query, guidance, before)
    action_result = controller.act(decision)
    actions.append({"round": 1, **decision, "execution": action_result})
    time.sleep(0.6 if action_result.get("status") == "success" else 0.15)
    after = controller.observe(1)
    observations.append(_browser_workflow_observation(
        1,
        decision.get("observation_type", "resume_guided_action"),
        goal,
        query,
        str(action_result.get("target") or decision.get("target") or after.get("url") or "current browser page"),
        controller=controller,
        dom_snapshot=after,
        action_result=action_result,
    ))

    completed = bool(action_result.get("goal_satisfied") or decision.get("goal_satisfied"))
    if not completed:
        completed = _browser_goal_satisfied(goal, action_result, after)
    requires_visual = not completed and (
        action_result.get("status") != "success"
        or controller.requires_visual_controller
        or decision.get("requires_followup", True)
    )
    return {
        "success": completed,
        "launched": True,
        "host_action": "browser_gui_resume",
        "application": "Google Chrome",
        "query": query,
        "guidance": guidance,
        "url": action_result.get("target") or after.get("url") or input_data.get("url") or "current browser page",
        "rounds": 1,
        "observations": observations,
        "actions": actions,
        "requires_visual_controller": requires_visual,
        "controller": controller.summary(),
        "platform": platform.system().lower(),
        "message": "Resumed the browser workflow from the current page using user guidance; no entry URL was reopened.",
        "_state_changes": {
            "last_host_action": "browser_gui_resume",
            "success": completed,
            "host_action": "browser_gui_resume",
            "application": "Google Chrome",
            "query": query,
            "url": action_result.get("target") or after.get("url") or input_data.get("url") or "current browser page",
            "observations": observations,
            "actions": actions,
            "requires_visual_controller": requires_visual,
            "guidance": guidance,
        },
    }


def resume_desktop_gui_workflow(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Continue a paused desktop GUI task from the current host state."""
    goal = str(input_data.get("goal") or "")
    guidance = str(input_data.get("guidance") or "").strip()
    screenshot = _capture_screen_observation(0)
    lowered = f"{goal}\n{guidance}".lower()
    actions: List[Dict[str, Any]] = []

    if "clash" in lowered and ("延迟" in goal or "延迟" in guidance or "latency" in lowered or "delay" in lowered):
        action = _run_clash_latency_test_from_menu()
    else:
        action = {
            "status": "blocked",
            "action": "desktop_visual_controller_needed",
            "reason": "Desktop resume needs a native accessibility/screenshot controller for this application.",
            "target": guidance or goal,
        }
    actions.append(action)
    success = action.get("status") == "success"
    requires_visual = not success
    observations = [{
        "round": 0,
        "type": "desktop_gui",
        "source": "DesktopGuiObservationProvider",
        "status": "observed",
        "observation_type": "desktop_resume",
        "evidence": {
            "goal": goal,
            "guidance": guidance,
            "screenshot": screenshot,
            "action_result": action,
            "available_evidence": ["screenshot_file"] if screenshot.get("available") else [],
            "missing_evidence": [] if success else ["native_accessibility_target"],
        },
        "confidence": 0.78 if success else 0.42,
    }]
    return {
        "success": success,
        "launched": True,
        "host_action": "desktop_gui_resume",
        "application": "macOS Desktop",
        "guidance": guidance,
        "url": "desktop",
        "rounds": 1,
        "observations": observations,
        "actions": actions,
        "requires_visual_controller": requires_visual,
        "platform": platform.system().lower(),
        "message": (
            "Resumed the desktop GUI task from the current host state."
            if success
            else "Desktop GUI resume could not complete without a native visual/accessibility target."
        ),
        "_state_changes": {
            "last_host_action": "desktop_gui_resume",
            "success": success,
            "host_action": "desktop_gui_resume",
            "application": "macOS Desktop",
            "url": "desktop",
            "observations": observations,
            "actions": actions,
            "requires_visual_controller": requires_visual,
            "guidance": guidance,
        },
    }


def _run_clash_latency_test_from_menu() -> Dict[str, Any]:
    if platform.system().lower() != "darwin":
        return {
            "status": "blocked",
            "action": "clash_latency_test",
            "reason": "Clash menu-bar automation is currently implemented for macOS only.",
        }
    if not shutil.which("osascript"):
        return {
            "status": "blocked",
            "action": "clash_latency_test",
            "reason": "osascript is unavailable on this host.",
        }
    script = r'''
tell application "System Events"
    set targetWords to {"延迟测速", "延迟测试", "延迟", "Latency Test", "Latency", "Delay Test", "Delay"}
    set clickedClash to false
    set lastError to ""
    repeat with proc in application processes
        set procName to name of proc as text
        if procName contains "Clash" or procName contains "clash" then
            tell proc
                repeat with mb in menu bars
                    repeat with mbi in menu bar items of mb
                        try
                            click mbi
                            set clickedClash to true
                            delay 0.5
                            repeat with menuWord in targetWords
                                try
                                    click menu item (menuWord as text) of menu 1 of mbi
                                    set clickedAction to true
                                    return "SUCCESS: clicked " & procName & " -> " & (menuWord as text)
                                on error errMsg
                                    set lastError to errMsg
                                end try
                            end repeat
                        on error errMsg
                            set lastError to errMsg
                        end try
                    end repeat
                end repeat
            end tell
        end if
    end repeat
    if clickedClash then
        return "BLOCKED: Clash menu opened, but latency-test menu item was not found. " & lastError
    end if
    return "BLOCKED: no Clash menu bar process/item found. " & lastError
end tell
'''
    try:
        completed = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=8)
    except Exception as exc:
        return {
            "status": "blocked",
            "action": "clash_latency_test",
            "reason": str(exc)[:500],
        }
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode == 0 and stdout.startswith("SUCCESS:"):
        return {
            "status": "success",
            "action": "clash_latency_test",
            "target": "Clash menu bar latency test",
            "reason": stdout,
        }
    return {
        "status": "blocked",
        "action": "clash_latency_test",
        "target": "Clash menu bar latency test",
        "reason": (stdout or stderr or f"osascript exited with {completed.returncode}")[:1000],
    }


def _browser_resume_decision(
    goal: str,
    query: str,
    guidance: str,
    observation: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    text = guidance.lower()
    original = guidance.strip()
    if any(token in text for token in ("done", "success", "finished", "已经完成", "完成了", "成功了")):
        return {
            "action": "stop_for_user_confirmed_success",
            "target": "user-confirmed success",
            "observation_type": "user_success_confirmation",
            "status": "planned",
            "done": True,
            "goal_satisfied": True,
            "requires_followup": False,
            "agent_decision": "User confirmed that the visible state satisfies the task.",
            "observation_required": ["user confirmation"],
            "reason": original or "User confirmed success.",
        }
    if re.search(r"https?://\\S+", guidance):
        url = re.search(r"https?://\\S+", guidance).group(0).rstrip("，,。.;")
        return {
            "action": "open_guided_url",
            "target": url,
            "observation_type": "guided_url_navigation",
            "status": "planned",
            "done": False,
            "requires_followup": True,
            "agent_decision": "User supplied a concrete URL; navigate there from the current browser state.",
            "observation_required": ["browser navigation result", "page screenshot"],
            "reason": original,
        }
    if any(token in guidance for token in ("第一条", "第一个", "首条")) or "first result" in text:
        return {
            "action": "select_search_result_candidate",
            "target": query,
            "observation_type": "guided_search_result_click",
            "status": "planned",
            "done": False,
            "requires_followup": True,
            "agent_decision": "User instructed the agent to open the first/current search result candidate.",
            "observation_required": ["click result feedback", "page screenshot after navigation"],
            "reason": original,
        }
    if any(token in guidance for token in ("登录", "登陆", "邮箱", "统一身份认证")) or any(token in text for token in ("login", "sign in", "mail", "email")):
        return {
            "action": "look_for_mail_login_control",
            "target": original or "Login / mail entry",
            "observation_type": "guided_login_click",
            "status": "planned",
            "done": False,
            "requires_followup": True,
            "agent_decision": "User guidance points to a login/mail entry on the current page.",
            "observation_required": ["login click feedback", "authenticated/mail page evidence"],
            "reason": original,
        }
    if "已发送" in guidance or "sent" in text:
        return {
            "action": "open_sent_mail_folder",
            "target": "Sent / 已发送",
            "observation_type": "guided_sent_folder_click",
            "status": "planned",
            "done": False,
            "requires_followup": True,
            "agent_decision": "User guidance points to the Sent folder.",
            "observation_required": ["Sent folder click feedback", "current folder indicator"],
            "reason": original,
        }
    return {
        "action": "stop_for_visual_controller",
        "target": original or "ambiguous user guidance",
        "observation_type": "resume_guidance_ambiguous",
        "status": "blocked",
        "done": True,
        "requires_followup": True,
        "agent_decision": "Guidance was recorded, but it did not map to a supported current-page action.",
        "observation_required": ["more specific target text", "marked screenshot", "visible button/link name"],
        "reason": original or "Please specify the visible target to click/type.",
    }


def _browser_workflow_observation(
    round_index: int,
    observation_type: str,
    goal: str,
    query: str,
    target: str,
    *,
    controller: Optional["_BrowserGuiController"] = None,
    dom_snapshot: Optional[Dict[str, Any]] = None,
    action_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    screenshot = _capture_screen_observation(round_index)
    dom = dom_snapshot or (controller.observe(round_index) if controller else {"available": False})
    available = ["search_url", "browser_open_request"]
    if screenshot.get("available"):
        available.append("screenshot_file")
    if dom.get("available"):
        available.append("dom_snapshot")
    return {
        "round": round_index,
        "type": "browser_gui",
        "source": "BrowserGuiObservationProvider",
        "status": "observed",
        "observation_type": observation_type,
        "evidence": {
            "goal": goal,
            "query": query,
            "target": target,
            "available_evidence": available,
            "missing_evidence": [
                item for item in ["screenshot_ocr", "clicked_element_result" if not action_result else ""]
                if item
            ],
            "screenshot": screenshot,
            "dom": dom,
            "action_result": action_result or {},
        },
        "confidence": 0.72 if dom.get("available") else 0.52,
    }


def _capture_screen_observation(round_index: int) -> Dict[str, Any]:
    if platform.system().lower() != "darwin":
        return {"available": False, "reason": "screenshot capture is currently implemented for macOS screencapture only"}
    output_dir = Path("/tmp") / "skillos_browser_observations"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"browser_round_{round_index}_{uuid.uuid4().hex[:8]}.png"
    try:
        completed = subprocess.run(["screencapture", "-x", str(path)], capture_output=True, text=True, timeout=5)
        if completed.returncode != 0 or not path.exists():
            return {"available": False, "reason": (completed.stderr or "screencapture failed").strip()[:240]}
        data = path.read_bytes()
        return {
            "available": True,
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "note": "Raw screenshot captured for future visual/OCR controller; this runtime does not yet interpret it.",
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:240]}


def _browser_workflow_decision(
    goal: str,
    query: str,
    round_index: int,
    observation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    lowered = goal.lower()
    visible = str((observation or {}).get("visible_text_preview") or "").lower()
    url = str((observation or {}).get("url") or "").lower()
    wants_sent = "已发送" in goal or "sent" in lowered
    wants_login = "登录" in goal or "login" in lowered or "sign in" in lowered
    if wants_sent and ("已发送" in visible or "sent" in visible):
        return {
            "action": "open_sent_mail_folder",
            "target": "Sent / 已发送",
            "observation_type": "mailbox_navigation_candidate",
            "status": "planned",
            "done": False,
            "agent_decision": "The current page appears to expose the Sent folder; click it now.",
            "observation_required": ["Sent/已发送 folder element", "current folder indicator"],
            "reason": "Visible page text already contains the target folder label.",
        }
    if wants_login and any(token in visible for token in ("登录", "统一身份认证", "sign in", "login")):
        return {
            "action": "look_for_mail_login_control",
            "target": "mail login / unified authentication",
            "observation_type": "login_page_candidate",
            "status": "planned",
            "done": False,
            "agent_decision": "The current page exposes a login/authentication target; use it before mailbox navigation.",
            "observation_required": ["login button/form", "cached credential prompt", "authenticated mailbox state"],
            "reason": "Visible page text contains login/authentication cues.",
        }
    if round_index > 1 and any(token in url for token in ("mail", "email", "webmail", "cas", "auth")) and wants_sent:
        return {
            "action": "open_sent_mail_folder",
            "target": "Sent / 已发送",
            "observation_type": "mailbox_navigation_candidate",
            "status": "planned",
            "done": False,
            "agent_decision": "After navigating into a mail/auth page, try to locate the Sent folder.",
            "observation_required": ["mailbox sidebar", "Sent/已发送 folder element"],
            "reason": "The current URL suggests the workflow is inside a mail/auth surface.",
        }
    if round_index == 1:
        return {
            "action": "select_search_result_candidate",
            "target": query,
            "observation_type": "search_result_candidate",
            "status": "planned",
            "done": False,
            "agent_decision": "Defer the exact click target until search result evidence is available.",
            "observation_required": ["visible search results", "official domain/title", "clickable result element"],
            "reason": "Need page observation to choose the official/login result instead of guessing a hard-coded URL.",
        }
    if round_index == 2 and wants_login:
        return {
            "action": "look_for_mail_login_control",
            "target": "Login / Sign in / authentication entry",
            "observation_type": "login_page_candidate",
            "status": "planned",
            "done": False,
            "agent_decision": "Find the login/sign-in entry after the target result has opened.",
            "observation_required": ["login/sign-in button or link", "authentication page", "cached credential prompt if applicable"],
            "reason": "The user asked to find a Login or Sign in entry after opening the target search result.",
        }
    if round_index == 3 and ("已发送" in goal or "sent" in lowered):
        return {
            "action": "open_sent_mail_folder",
            "target": "Sent / 已发送",
            "observation_type": "mailbox_navigation_candidate",
            "status": "planned",
            "done": False,
            "agent_decision": "Click Sent only when the mailbox navigation element is visible.",
            "observation_required": ["mailbox sidebar", "Sent/已发送 folder element", "current folder indicator"],
            "reason": "Need visual/DOM confirmation that the Sent folder button is visible before clicking.",
        }
    return {
        "action": "stop_for_visual_controller",
        "target": "browser visual controller",
        "observation_type": "needs_visual_controller",
        "status": "blocked",
        "done": True,
        "agent_decision": "Stop rather than fabricate success; the next runtime upgrade needs screenshot/DOM click execution.",
        "observation_required": ["DOM snapshot", "screenshot OCR", "click result feedback"],
        "reason": "Host runtime can open/search, but cannot yet click arbitrary page elements without DOM/screenshot controller feedback.",
    }


class _BrowserGuiController:
    """Cross-platform browser workflow controller with provider-specific depth.

    All platforms support launch/search and target-result URL fallback. macOS
    Chrome additionally supports DOM observation/clicks when the user enables
    "Allow JavaScript from Apple Events" in Chrome's Develop menu.
    """

    def __init__(self, *, goal: str, query: str) -> None:
        self.goal = goal
        self.query = query
        self.platform = platform.system().lower()
        self.provider = "macos_chrome_dom_controller" if self.platform == "darwin" else "generic_browser_url_controller"
        self.dom_supported = self.platform == "darwin"
        self.requires_visual_controller = not self.dom_supported
        self.action_log: List[Dict[str, Any]] = []
        self.last_observation: Dict[str, Any] = {}

    def observe(self, round_index: int) -> Dict[str, Any]:
        if not self.dom_supported:
            observation = {
                "available": False,
                "round": round_index,
                "provider": self.provider,
                "reason": "DOM observation is unavailable for this platform/provider; URL-level search target fallback remains available.",
            }
            self.last_observation = observation
            return observation
        script = r"""
(() => {
  const textOf = (el) => (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
  const pick = (selector, limit) => Array.from(document.querySelectorAll(selector)).slice(0, limit).map((el, index) => ({
    index,
    tag: el.tagName.toLowerCase(),
    text: textOf(el).slice(0, 160),
    href: el.href || '',
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    id: el.id || '',
    className: String(el.className || '').slice(0, 120)
  })).filter(item => item.text || item.href || item.aria || item.id);
  return JSON.stringify({
    title: document.title,
    url: location.href,
    visibleText: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim().slice(0, 3000),
    links: pick('a', 30),
    buttons: pick('button,[role=button],input[type=button],input[type=submit]', 30),
    inputs: pick('input,textarea,[contenteditable=true]', 20)
  });
})()
"""
        result = _chrome_execute_javascript(script)
        if not result.get("ok"):
            self.requires_visual_controller = True
            observation = {
                "available": False,
                "round": round_index,
                "reason": result.get("error") or "Chrome JavaScript observation failed.",
            }
            self.last_observation = observation
            return observation
        try:
            payload = json.loads(str(result.get("value") or "{}"))
        except json.JSONDecodeError:
            payload = {"raw": str(result.get("value") or "")[:2000]}
        observation = {
            "available": True,
            "round": round_index,
            "title": payload.get("title", ""),
            "url": payload.get("url", ""),
            "visible_text_preview": str(payload.get("visibleText", ""))[:1200],
            "links": payload.get("links", [])[:12],
            "buttons": payload.get("buttons", [])[:12],
            "inputs": payload.get("inputs", [])[:8],
        }
        self.last_observation = observation
        return observation

    def act(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        action = str(decision.get("action") or "")
        if not self.dom_supported:
            if action == "select_search_result_candidate":
                result = self._open_target_result_fallback(reason="DOM controller is unavailable on this platform/provider.")
            else:
                result = {"status": "blocked", "reason": "No DOM/visual controller available for this action on the current platform.", "action": action}
            self.action_log.append(result)
            return result
        if action == "select_search_result_candidate":
            result = self._click_first_search_result()
        elif action == "look_for_mail_login_control":
            result = self._click_text_candidates(
                ["登录", "统一身份认证", "Sign in", "Login", "Log in", "mail", "邮箱", "进入邮箱", "邮件系统"],
                action=action,
            )
            if result.get("status") == "blocked" and _is_chrome_apple_event_js_disabled(str(result.get("reason", ""))):
                result = self._open_target_result_fallback(
                    reason="Chrome JavaScript from Apple Events is disabled for login-entry click.",
                    target_hint="Login Sign in",
                    action="open_login_target_url_fallback",
                )
        elif action == "open_sent_mail_folder":
            result = self._click_text_candidates([
                "已发送", "Sent", "Sent Mail", "发件箱", "已发邮件",
            ], action=action)
        elif action == "stop_for_visual_controller":
            result = {"status": "blocked", "reason": decision.get("reason", ""), "action": action}
        else:
            result = {"status": "skipped", "reason": f"Unsupported browser action: {action}", "action": action}
        self.action_log.append(result)
        if result.get("status") not in {"success", "skipped"}:
            self.requires_visual_controller = True
        return result

    def _click_first_search_result(self) -> Dict[str, Any]:
        fallback_url = _google_target_result_url(self.query)
        script = r"""
(() => {
  const candidates = Array.from(document.querySelectorAll('a'))
    .filter(a => a.href && !a.href.includes('/search?') && !a.href.includes('accounts.google') && (a.innerText || a.textContent || '').trim().length > 3);
  const target = candidates.find(a => {
    const href = a.href.toLowerCase();
    const text = (a.innerText || a.textContent || '').toLowerCase();
    return !href.includes('google.com') || text.includes('邮箱') || text.includes('mail');
  }) || candidates[0];
  if (!target) return JSON.stringify({status:'blocked', reason:'No clickable search result link found'});
  const info = {status:'success', action:'click_first_search_result', targetText:(target.innerText || target.textContent || '').trim().slice(0,180), target: target.href};
  target.click();
  return JSON.stringify(info);
})()
"""
        result = _json_js_result(_chrome_execute_javascript(script), fallback_action="click_first_search_result")
        if result.get("status") == "blocked" and _is_chrome_apple_event_js_disabled(str(result.get("reason", ""))):
            return self._open_target_result_fallback(reason="Chrome JavaScript from Apple Events is disabled.")
        return result

    def _open_target_result_fallback(
        self,
        *,
        reason: str,
        target_hint: str = "",
        action: str = "open_target_result_url_fallback",
    ) -> Dict[str, Any]:
        fallback_url = _google_target_result_url(self.query, target_hint=target_hint)
        launch = _open_url_in_chrome({"url": fallback_url, "goal": self.goal})
        return {
            "status": "success",
            "action": action,
            "target": fallback_url,
            "targetText": f"Search target result for {self.query} {target_hint}".strip(),
            "reason": f"{reason} Used the generalized search-target URL fallback.",
            "fallback": launch,
        }

    def _click_text_candidates(self, labels: List[str], *, action: str) -> Dict[str, Any]:
        script = r"""
((labels) => {
  const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const textOf = (el) => norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '');
  const elements = Array.from(document.querySelectorAll('a,button,[role=button],input[type=button],input[type=submit],div,span'))
    .filter(el => {
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && textOf(el).length > 0;
    });
  const lowered = labels.map(norm);
  const target = elements.find(el => {
    const text = textOf(el);
    return lowered.some(label => label && text.includes(label));
  });
  if (!target) return JSON.stringify({status:'blocked', reason:'No visible text target matched', labels});
  const info = {status:'success', action:'click_text_target', targetText:textOf(target).slice(0,180), target: textOf(target).slice(0,180), tag: target.tagName.toLowerCase()};
  target.click();
  return JSON.stringify(info);
})(%s)
""" % json.dumps(labels, ensure_ascii=False)
        result = _json_js_result(_chrome_execute_javascript(script), fallback_action=action)
        result.setdefault("action", action)
        return result

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.provider,
            "platform": self.platform,
            "dom_supported": self.dom_supported,
            "requires_visual_controller": self.requires_visual_controller,
            "action_count": len(self.action_log),
            "last_url": self.last_observation.get("url"),
            "last_title": self.last_observation.get("title"),
        }


def _chrome_execute_javascript(script: str) -> Dict[str, Any]:
    if platform.system().lower() != "darwin":
        return {"ok": False, "error": "Chrome JavaScript execution is currently implemented for macOS only."}
    script_path = Path("/tmp") / f"skillos_chrome_js_{uuid.uuid4().hex}.js"
    try:
        script_path.write_text(script, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"Failed to write Chrome JavaScript payload: {exc}"}
    osa = [
        "osascript",
        "-e",
        f'set jsSource to read POSIX file {json.dumps(str(script_path))} as «class utf8»',
        "-e",
        'tell application "Google Chrome"',
        "-e",
        "if not (exists front window) then return \"\"",
        "-e",
        "execute active tab of front window javascript jsSource",
        "-e",
        "end tell",
    ]
    try:
        completed = subprocess.run(osa, capture_output=True, text=True, timeout=8)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if completed.returncode != 0:
        return {"ok": False, "error": (completed.stderr or completed.stdout or "osascript failed").strip()[:500]}
    return {"ok": True, "value": completed.stdout.strip()}


def _is_chrome_apple_event_js_disabled(reason: str) -> bool:
    lowered = reason.lower()
    return (
        "javascript from apple events" in lowered
        or "apple 事件中的 javascript" in lowered
        or "执行 javascript 的功能已关闭" in lowered
        or "allow javascript from apple events" in lowered
    )


def _google_target_result_url(query: str, *, target_hint: str = "") -> str:
    target = f"{query} {target_hint}".strip()
    return f"https://www.google.com/search?q={quote_plus(target)}&btnI=I"


def _json_js_result(result: Dict[str, Any], *, fallback_action: str) -> Dict[str, Any]:
    if not result.get("ok"):
        return {"status": "blocked", "action": fallback_action, "reason": result.get("error") or "Chrome JS action failed"}
    raw = str(result.get("value") or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"status": "unknown", "raw": raw[:1000]}
    payload.setdefault("action", fallback_action)
    payload.setdefault("status", "success" if payload.get("target") or payload.get("targetText") else "blocked")
    return payload


def _browser_goal_satisfied(goal: str, action_result: Dict[str, Any], observation: Dict[str, Any]) -> bool:
    if action_result.get("status") != "success":
        return False
    text = " ".join([
        str(action_result.get("targetText", "")),
        str(action_result.get("target", "")),
        str(observation.get("title", "")),
        str(observation.get("url", "")),
        str(observation.get("visible_text_preview", ""))[:500],
    ]).lower()
    if ("已发送" in goal or "sent" in goal.lower()) and ("已发送" in text or "sent" in text):
        return True
    if ("登录" in goal or "login" in goal.lower() or "sign in" in goal.lower()) and any(token in text for token in ("mail", "邮箱", "inbox", "收件箱")):
        return True
    return False


def _infer_browser_gui_query(goal: str) -> str:
    query = goal
    remove_tokens = [
        "打开浏览器", "浏览器", "找到", "并登录", "直接登录", "登录", "并打开",
        "打开已发送", "已发送", "我已经有账密缓存", "账密缓存", "我已经有", "缓存",
        "click", "login", "sign in", "open sent", "browser", "chrome",
    ]
    for token in remove_tokens:
        query = query.replace(token, " ")
    query = re.sub(r"\s+", " ", query).strip(" ，。,.()（）")
    if "邮箱" in goal and "邮箱" not in query:
        query = f"{query} 邮箱".strip()
    return query or goal


def _infer_terminal_command(goal: str) -> str:
    lowered = goal.lower()
    if any(token in lowered for token in ("environment", "env", "printenv")) or any(
        token in goal for token in ("环境变量", "环境 变量")
    ):
        return "printenv"
    if "当前目录" in goal or "工作目录" in goal or "pwd" in lowered:
        return "pwd"
    if "用户名" in goal or "whoami" in lowered:
        return "whoami"
    if "日期" in goal or "时间" in goal or "date" in lowered:
        return "date"
    if "系统信息" in goal or "系统版本" in goal or "uname" in lowered:
        return "uname -a"
    if "列出" in goal or "列表" in goal or "ls" in lowered:
        return "ls"
    return ""


def _safe_command_parts(command: str) -> list[str]:
    parts = shlex.split(command)
    if not parts:
        raise RuntimeError("Empty terminal command")
    expanded = []
    for part in parts:
        if part.startswith("~/"):
            expanded.append(str(Path(part).expanduser()))
        else:
            expanded.append(part)
    return expanded


def _is_safe_terminal_command(command: str) -> bool:
    if any(token in command for token in (";", "&", "|", "`", "$(", ">", "<", "\n", "\r")):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    allowed = {
        "printenv",
        "env",
        "pwd",
        "whoami",
        "date",
        "ls",
        "uname",
        "sw_vers",
        "python",
        "python3",
        "node",
        "code",
    }
    base = Path(parts[0]).name
    if base not in allowed:
        return False
    if base == "code":
        return len(parts) == 1 or (len(parts) == 2 and parts[1] == ".")
    if base in {"python", "python3", "node"}:
        return len(parts) == 2 and parts[1] in {"--version", "-v"}
    return True


def _coerce_duration_seconds(value: Any, *, default: int = 10) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(3, min(parsed, 30))


def _infer_application_name(goal: str) -> str:
    lowered = goal.lower()
    if "chrome" in lowered or "browser" in lowered or "浏览器" in goal or "谷歌" in goal:
        return "Google Chrome"
    if "finder" in lowered or "访达" in goal:
        return "Finder"
    if "terminal" in lowered or "终端" in goal:
        return "Terminal"
    return ""


def _infer_url(goal: str) -> str:
    lowered = goal.lower()
    if "chatgpt" in lowered or "gpt" in lowered or "对话" in goal:
        return "https://chatgpt.com/"
    if "openai" in lowered:
        return "https://openai.com/"
    if "github" in lowered:
        return "https://github.com/"
    return ""


def _infer_question(goal: str) -> str:
    if "天气" in goal:
        return "What is today's weather?"
    return goal or "Summarize the requested task."


def _infer_downloads_filename(goal: str, default: str = "gpt_taskname_answer.txt") -> str:
    lowered = goal.lower()
    if "weather" in lowered or "天气" in goal:
        return "gpt_weather_answer.txt"
    if "openai" in lowered or "gpt" in lowered:
        return "gpt_taskname_answer.txt"
    return default


def _infer_filename(goal: str) -> str:
    match = re.search(
        r"([A-Za-z0-9_\-.]+?\.(?:json|csv|txt|md|py|pdf|docx|xlsx|yaml|yml))",
        goal,
        flags=re.IGNORECASE,
    )
    return Path(match.group(1)).name if match else ""


def _default_answer_content(goal: str, *, question: Optional[str] = None) -> str:
    asked = question or _infer_question(goal)
    return (
        "SkillOS host task answer\n"
        "========================\n\n"
        f"Task: {goal or 'No task text provided'}\n"
        f"Question: {asked}\n\n"
        "Answer: This file was created by a SkillOS strategic host workflow. "
        "For live weather or other dynamic facts, provide the target city and connect "
        "a weather/API skill; this run demonstrates the executable desktop-to-file path.\n"
    )


def _expand_host_path(raw_path: str) -> Optional[Path]:
    value = raw_path.strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _resolve_vscode_file_path(input_data: Dict[str, Any]) -> Optional[Path]:
    raw_path = str(input_data.get("path") or input_data.get("file_path") or "").strip()
    if raw_path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.home() / "Desktop" / path
        return path.resolve()
    filename = str(input_data.get("filename") or _infer_filename(str(input_data.get("goal", ""))) or "").strip()
    if not filename:
        return None
    return (Path.home() / "Desktop" / Path(filename).name).resolve()
