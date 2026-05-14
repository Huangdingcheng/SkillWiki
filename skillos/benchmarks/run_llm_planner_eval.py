"""Run a SkillOS planner evaluation with fallback and real LLM modes.

The C-P1-1 benchmark checks planner skill selection, not skill execution. It
keeps API failures separate from functional planning failures so rate limits or
auth problems do not distort the planner success rate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from skillos.config.llm_config import LLMConfig  # noqa: E402
from skillos.layers.skill_runtime.planner import ExecutionPlan, SkillPlanner  # noqa: E402
from skillos.models.skill_model import (  # noqa: E402
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillState,
)
from skillos.utils.llm_client import (  # noqa: E402
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)


MODES = ("fallback", "llm")
API_KEY_ENVS = ("SKILLOS_TEAM_API_KEY", "LLM_API_KEY", "SKILLOS_API_KEY")


@dataclass
class PlannerEvalConfig:
    api_url: str = "https://yunwu.ai"
    api_key: str = ""
    model: str = "gpt-5.4-nano"
    temperature: float = 0.0
    max_tokens: int = 2000
    timeout: int = 60
    retry_count: int = 0
    seed: Optional[int] = None
    context: str = ""
    distractor_count: int = 6

    def llm_config(self) -> LLMConfig:
        return LLMConfig(
            api_url=self.api_url,
            api_key=self.api_key,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            retry_count=self.retry_count,
        )

    def llm_extra(self) -> Optional[Dict[str, Any]]:
        if self.seed is None:
            return None
        return {"seed": self.seed}


def load_tasks(path: Path) -> List[Dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError("Planner eval task file must contain a JSON list.")
    return [task for task in tasks if isinstance(task, dict)]


def run_planner_eval(
    tasks: List[Dict[str, Any]],
    config: PlannerEvalConfig,
    modes: Iterable[str] = MODES,
    *,
    llm_client_factory: Callable[[LLMConfig], Any] = LLMClient,
) -> Dict[str, Any]:
    return asyncio.run(
        _run_planner_eval(
            tasks,
            config,
            modes=modes,
            llm_client_factory=llm_client_factory,
        )
    )


async def _run_planner_eval(
    tasks: List[Dict[str, Any]],
    config: PlannerEvalConfig,
    modes: Iterable[str] = MODES,
    *,
    llm_client_factory: Callable[[LLMConfig], Any] = LLMClient,
) -> Dict[str, Any]:
    selected_modes = [mode for mode in modes if mode in MODES]
    catalog = _build_skill_catalog(tasks)
    llm_client = llm_client_factory(config.llm_config()) if config.api_key else None
    fallback_client = _FallbackOnlyLLM()
    results: List[Dict[str, Any]] = []

    for task in tasks:
        available = _available_skills(task, catalog, config.distractor_count)
        for mode in selected_modes:
            if mode == "llm" and llm_client is None:
                results.append(_skipped_record(task, mode, "missing_api_key"))
                continue
            client = fallback_client if mode == "fallback" else llm_client
            assert client is not None
            results.append(await _run_task_mode(task, available, mode, config, client))

    payload: Dict[str, Any] = {
        "generated_at": _utc_now(),
        "benchmark": "llm_planner_eval",
        "task_count": len(tasks),
        "modes": selected_modes,
        "config": {
            "api_url": config.api_url,
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout,
            "retry_count": config.retry_count,
            "seed": config.seed,
            "context": config.context,
            "distractor_count": config.distractor_count,
            "api_key_provided": bool(config.api_key),
        },
        "results": results,
    }
    payload["summary"] = summarize_planner_eval(payload)
    return payload


async def _run_task_mode(
    task: Dict[str, Any],
    available_skills: List[Skill],
    mode: str,
    config: PlannerEvalConfig,
    llm_client: Any,
) -> Dict[str, Any]:
    started = time.perf_counter()
    task_id = str(task.get("task_id") or "unknown_task")
    try:
        planner = SkillPlanner(llm_client)
        plan = await planner.plan(
            str(task.get("goal") or ""),
            available_skills,
            current_state=_current_state(task, config),
            task_id=task_id,
            force_fallback=(mode == "fallback"),
            force_llm=(mode == "llm"),
            llm_extra=config.llm_extra(),
            fallback_on_llm_error=False,
            fallback_on_invalid_response=False,
        )
    except Exception as exc:
        latency_ms = _elapsed_ms(started)
        if mode == "llm" and isinstance(exc, LLMError):
            return _api_failure_record(task, mode, exc, latency_ms)
        return _functional_error_record(task, mode, exc, latency_ms)

    return _plan_record(task, mode, plan, _elapsed_ms(started), config, available_skills)


def _plan_record(
    task: Dict[str, Any],
    mode: str,
    plan: ExecutionPlan,
    latency_ms: float,
    config: PlannerEvalConfig,
    available_skills: List[Skill],
) -> Dict[str, Any]:
    expected = _expected_skills(task)
    selected = [step.skill_id for step in plan.steps]
    missing = [skill_id for skill_id in expected if skill_id not in selected]
    unexpected = [skill_id for skill_id in selected if skill_id not in expected]
    missing_inputs = _missing_required_inputs(plan, available_skills)
    exact_match = selected == expected
    invalid_response = bool(plan.metadata.get("invalid_response"))
    inputs_complete = not missing_inputs
    success = exact_match and inputs_complete and not invalid_response
    failure_reason = ""
    if not success:
        failure_reason = _functional_failure_reason(
            expected,
            selected,
            missing,
            unexpected,
            missing_inputs,
            invalid_response=invalid_response,
            metadata=plan.metadata,
        )

    return {
        "task_id": str(task.get("task_id") or "unknown_task"),
        "domain": task.get("domain", "general"),
        "mode": mode,
        "status": "success" if success else "functional_failure",
        "success": success,
        "api_failure": False,
        "api_error_type": None,
        "latency_ms": latency_ms,
        "expected_skills": expected,
        "selected_skill_ids": selected,
        "missing_expected": missing,
        "unexpected_skills": unexpected,
        "inputs_complete": inputs_complete,
        "missing_required_inputs": missing_inputs,
        "plan_source": plan.metadata.get("source", "unknown"),
        "input_mapping_repairs": plan.metadata.get("input_mapping_repairs", []),
        "llm_model": plan.metadata.get("llm_model"),
        "llm_usage": plan.metadata.get("llm_usage") or {},
        "llm_finish_reason": plan.metadata.get("llm_finish_reason"),
        "plan_id": plan.plan_id,
        "step_count": len(plan.steps),
        "steps": [
            {
                "step_index": step.step_index,
                "skill_id": step.skill_id,
                "skill_name": step.skill_name,
                "depends_on": step.depends_on,
                "input_mapping": step.input_mapping,
            }
            for step in plan.steps
        ],
        "llm_request": _llm_request(mode, config),
        "failure_reason": failure_reason,
    }


def _api_failure_record(
    task: Dict[str, Any],
    mode: str,
    exc: Exception,
    latency_ms: float,
) -> Dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or "unknown_task"),
        "domain": task.get("domain", "general"),
        "mode": mode,
        "status": "api_failure",
        "success": None,
        "api_failure": True,
        "api_error_type": _classify_api_error(exc),
        "api_error_message": str(exc),
        "latency_ms": latency_ms,
        "expected_skills": _expected_skills(task),
        "selected_skill_ids": [],
        "missing_expected": _expected_skills(task),
        "unexpected_skills": [],
        "plan_source": "llm_api_error",
        "llm_model": None,
        "llm_usage": {},
        "llm_finish_reason": None,
        "plan_id": None,
        "step_count": 0,
        "steps": [],
        "llm_request": None,
        "failure_reason": "",
    }


def _functional_error_record(
    task: Dict[str, Any],
    mode: str,
    exc: Exception,
    latency_ms: float,
) -> Dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or "unknown_task"),
        "domain": task.get("domain", "general"),
        "mode": mode,
        "status": "functional_failure",
        "success": False,
        "api_failure": False,
        "api_error_type": None,
        "latency_ms": latency_ms,
        "expected_skills": _expected_skills(task),
        "selected_skill_ids": [],
        "missing_expected": _expected_skills(task),
        "unexpected_skills": [],
        "plan_source": "runtime_error",
        "llm_model": None,
        "llm_usage": {},
        "llm_finish_reason": None,
        "plan_id": None,
        "step_count": 0,
        "steps": [],
        "llm_request": None,
        "failure_reason": str(exc),
    }


def _skipped_record(task: Dict[str, Any], mode: str, reason: str) -> Dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or "unknown_task"),
        "domain": task.get("domain", "general"),
        "mode": mode,
        "status": "skipped",
        "success": None,
        "api_failure": False,
        "api_error_type": reason,
        "latency_ms": 0.0,
        "expected_skills": _expected_skills(task),
        "selected_skill_ids": [],
        "missing_expected": _expected_skills(task),
        "unexpected_skills": [],
        "plan_source": "not_run",
        "llm_model": None,
        "llm_usage": {},
        "llm_finish_reason": None,
        "plan_id": None,
        "step_count": 0,
        "steps": [],
        "llm_request": None,
        "failure_reason": reason,
    }


def summarize_planner_eval(payload: Dict[str, Any]) -> Dict[str, Any]:
    results = [item for item in payload.get("results", []) if isinstance(item, dict)]
    modes = payload.get("modes") or MODES
    rows: Dict[str, Dict[str, Any]] = {}
    for item in results:
        task_id = str(item.get("task_id") or "")
        mode = str(item.get("mode") or "")
        if not task_id or not mode:
            continue
        row = rows.setdefault(task_id, {"task_id": task_id})
        row[f"{mode}_status"] = item.get("status", "missing")
        row[f"{mode}_selected"] = item.get("selected_skill_ids", [])
        if item.get("api_error_type"):
            row[f"{mode}_api_error_type"] = item.get("api_error_type")
        if item.get("failure_reason"):
            row[f"{mode}_failure_reason"] = item.get("failure_reason")

    summary_rows = []
    for task_id in sorted(rows):
        row = rows[task_id]
        row["winner"] = _winner(row)
        summary_rows.append(row)

    mode_totals = {
        mode: _mode_totals(results, mode)
        for mode in modes
    }
    return {
        "task_count": len(summary_rows),
        "mode_totals": mode_totals,
        "rows": summary_rows,
    }


def write_eval_payload(payload: Dict[str, Any], output_path: Path) -> Dict[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = output_path.parent / "llm_eval_latest.json"
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"result": str(output_path), "latest": str(latest_path)}


def _build_skill_catalog(tasks: List[Dict[str, Any]]) -> Dict[str, Skill]:
    skill_ids = sorted({skill_id for task in tasks for skill_id in _expected_skills(task)})
    return {skill_id: _catalog_skill(skill_id) for skill_id in skill_ids}


def _catalog_skill(skill_id: str) -> Skill:
    return Skill(
        skill_id=skill_id,
        name=skill_id,
        description=_skill_description(skill_id),
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema=_skill_input_schema(skill_id),
            output_schema={"type": "object", "properties": {}},
        ),
        implementation=SkillImplementation(code="output['success'] = True"),
    )


def _available_skills(
    task: Dict[str, Any],
    catalog: Dict[str, Skill],
    distractor_count: int,
) -> List[Skill]:
    expected = _expected_skills(task)
    distractors = [
        skill_id
        for skill_id in sorted(catalog)
        if skill_id not in expected
    ][: max(0, distractor_count)]
    candidate_ids = distractors + expected
    return [catalog[skill_id] for skill_id in candidate_ids if skill_id in catalog]


def _expected_skills(task: Dict[str, Any]) -> List[str]:
    return [
        str(skill_id).strip()
        for skill_id in task.get("expected_skills", [])
        if str(skill_id).strip()
    ]


def _current_state(task: Dict[str, Any], config: PlannerEvalConfig) -> Dict[str, Any]:
    return {
        "benchmark_task_id": task.get("task_id"),
        "domain": task.get("domain"),
        "input": task.get("input", {}),
        "raw_context": task.get("raw_context", ""),
        "notes": task.get("notes", ""),
        "eval_context": config.context,
    }


def _llm_request(mode: str, config: PlannerEvalConfig) -> Optional[Dict[str, Any]]:
    if mode != "llm":
        return None
    return {
        "api_url": config.api_url,
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "seed": config.seed,
        "context_provided": bool(config.context),
    }


def _functional_failure_reason(
    expected: List[str],
    selected: List[str],
    missing: List[str],
    unexpected: List[str],
    missing_inputs: List[Dict[str, Any]],
    *,
    invalid_response: bool,
    metadata: Dict[str, Any],
) -> str:
    if invalid_response:
        return str(metadata.get("failure_reason") or "Planner LLM returned invalid JSON.")
    if not selected:
        return "Planner returned no executable steps."
    reasons = []
    if missing:
        reasons.append(f"missing expected skills: {', '.join(missing)}")
    if unexpected:
        reasons.append(f"unexpected skills: {', '.join(unexpected)}")
    if selected != expected:
        reasons.append(
            "skill order mismatch: expected "
            f"{expected}, got {selected}"
        )
    if missing_inputs:
        formatted = [
            f"{item.get('skill_id')}.{item.get('input')}"
            for item in missing_inputs
        ]
        reasons.append(f"missing required inputs: {', '.join(formatted)}")
    return "; ".join(reasons) or "Planner output did not match expected skills."


def _missing_required_inputs(
    plan: ExecutionPlan,
    available_skills: List[Skill],
) -> List[Dict[str, Any]]:
    skill_map = {skill.skill_id: skill for skill in available_skills}
    missing: List[Dict[str, Any]] = []
    for step in plan.steps:
        skill = skill_map.get(step.skill_id)
        if not skill:
            continue
        for input_name in _required_inputs(skill):
            value = step.input_mapping.get(input_name)
            if value is None or value == "" or value == [] or value == {}:
                missing.append({
                    "step_index": step.step_index,
                    "skill_id": step.skill_id,
                    "input": input_name,
                })
    return missing


def _required_inputs(skill: Skill) -> List[str]:
    schema = skill.interface.input_schema or {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        return []
    return [str(name) for name in required if isinstance(name, str)]


def _mode_totals(results: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    items = [item for item in results if item.get("mode") == mode]
    functional_total = sum(1 for item in items if item.get("status") in {"success", "functional_failure"})
    success = sum(1 for item in items if item.get("status") == "success")
    return {
        "total": len(items),
        "success": success,
        "functional_failure": sum(1 for item in items if item.get("status") == "functional_failure"),
        "api_failure": sum(1 for item in items if item.get("status") == "api_failure"),
        "skipped": sum(1 for item in items if item.get("status") == "skipped"),
        "success_rate_excluding_api_failures": (
            success / functional_total
            if functional_total
            else 0.0
        ),
    }


def _winner(row: Dict[str, Any]) -> str:
    fallback = row.get("fallback_status")
    llm = row.get("llm_status")
    if fallback == "success" and llm == "success":
        return "tie"
    if llm == "success":
        return "llm"
    if fallback == "success":
        return "fallback"
    if llm in {"api_failure", "skipped"}:
        return "incomplete_llm"
    return "none"


def _classify_api_error(exc: Exception) -> str:
    if isinstance(exc, LLMRateLimitError):
        return "rate_limit"
    if isinstance(exc, LLMAuthError):
        return "auth"
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMServerError):
        return "server"
    if isinstance(exc, LLMError):
        return "llm_error"
    return "unexpected"


def _skill_description(skill_id: str) -> str:
    words = skill_id.replace("_", " ")
    descriptions = {
        "fill_form": "Fill form fields with supplied values and submit when requested.",
        "click_element": "Click a web page element selected by CSS selector or natural language.",
        "type_text": "Type text into an active or selected web input.",
        "extract_selector": "Extract a stable CSS selector from HTML or DOM context.",
        "submit_form": "Submit a web form and report final page state.",
        "parse_openapi_endpoint": "Parse an OpenAPI endpoint into method, path, and parameters.",
        "build_tool_call": "Build an API or tool call with validated argument mapping.",
        "validate_response_schema": "Validate a response object against a required schema.",
        "extract_steps": "Extract ordered reusable steps from a document or procedure.",
        "extract_function_skill": "Convert a script function into a reusable skill definition.",
        "repair_postcondition": "Repair a skill that failed deterministic postcondition checks.",
        "detect_schema_change": "Detect breaking schema changes between skill versions.",
        "trace_graph_provenance": "Trace provenance between source, skill, execution, validation, and version nodes.",
    }
    return descriptions.get(skill_id, f"Reusable SkillOS capability for {words}.")


def _skill_input_schema(skill_id: str) -> Dict[str, Any]:
    schemas: Dict[str, Dict[str, Any]] = {
        "fill_form": _object_schema({
            "url": "string",
            "form_data": "object",
        }, required=["form_data"]),
        "click_element": _object_schema({
            "selector": "string",
        }, required=["selector"]),
        "type_text": _object_schema({
            "selector": "string",
            "text": "string",
        }, required=["selector", "text"]),
        "extract_selector": _object_schema({
            "html": "string",
        }, required=["html"]),
        "submit_form": _object_schema({
            "selector": "string",
        }, required=["selector"]),
        "parse_openapi_endpoint": _object_schema({
            "openapi_fragment": "object",
        }, required=["openapi_fragment"]),
        "build_tool_call": _object_schema({
            "operation_id": "string",
            "arguments": "object",
        }, required=["operation_id", "arguments"]),
        "validate_response_schema": _object_schema({
            "response": "object",
            "required": "array",
        }, required=["response", "required"]),
        "extract_steps": _object_schema({
            "text": "string",
        }, required=["text"]),
        "extract_function_skill": _object_schema({
            "code": "string",
        }, required=["code"]),
        "reflect_failure": _object_schema({
            "failed_skill_id": "string",
            "issue": "string",
        }, required=["failed_skill_id", "issue"]),
        "detect_schema_change": _object_schema({
            "before": "object",
            "after": "object",
        }, required=["before", "after"]),
        "trace_provenance": _object_schema({
            "start": "string",
            "target": "string",
        }, required=["start", "target"]),
        "repair_postcondition": _object_schema({
            "failed_skill_id": "string",
            "issue": "string",
        }, required=["failed_skill_id", "issue"]),
        "detect_schema_change": _object_schema({
            "before": "object",
            "after": "object",
        }, required=["before", "after"]),
        "trace_graph_provenance": _object_schema({
            "start": "string",
            "target": "string",
        }, required=["start", "target"]),
    }
    return schemas.get(skill_id, _object_schema({}))


def _object_schema(
    properties: Dict[str, str],
    *,
    required: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": kind}
            for name, kind in properties.items()
        },
        "required": required or [],
    }


def _env_first(names: Iterable[str], default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _default_output_path(output_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return output_dir / f"llm_eval_results_{stamp}.json"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


class _FallbackOnlyLLM:
    def __init__(self) -> None:
        self._cfg = SimpleNamespace(api_key="demo")

    def chat(self, messages: object) -> object:
        raise RuntimeError("fallback mode should not call the LLM")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks",
        type=Path,
        default=Path(__file__).parent / "skillos_demo_tasks.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "results",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--api-url", default=_env_first(("LLM_API_URL", "SKILLOS_API_URL"), "https://yunwu.ai"))
    parser.add_argument("--api-key", default=_env_first(API_KEY_ENVS))
    parser.add_argument("--model", default=_env_first(("LLM_MODEL", "SKILLOS_MODEL"), "gpt-5.4-nano"))
    parser.add_argument("--temperature", type=float, default=float(os.getenv("LLM_TEMPERATURE", "0.0")))
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("LLM_MAX_TOKENS", "2000")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("LLM_TIMEOUT", "60")))
    parser.add_argument("--retry-count", type=int, default=int(os.getenv("LLM_RETRY_COUNT", "0")))
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--context", default="")
    parser.add_argument("--distractor-count", type=int, default=6)
    parser.add_argument("--task-limit", type=int, default=0)
    parser.add_argument("--mode", choices=MODES, action="append")
    args = parser.parse_args(list(argv) if argv is not None else None)

    tasks = load_tasks(args.tasks)
    if args.task_limit > 0:
        tasks = tasks[: args.task_limit]
    config = PlannerEvalConfig(
        api_url=args.api_url,
        api_key=args.api_key,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        retry_count=args.retry_count,
        seed=args.seed,
        context=args.context,
        distractor_count=args.distractor_count,
    )
    payload = run_planner_eval(tasks, config, modes=args.mode or MODES)
    output_path = args.output or _default_output_path(args.output_dir)
    print(json.dumps(write_eval_payload(payload, output_path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
