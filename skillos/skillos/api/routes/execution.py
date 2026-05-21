"""Skill 执行路由。"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ...layers.skill_runtime import StateTracker
from ..deps import AppState, get_app_state
from .ws import broadcast
from ..schemas import (
    ExecutePlanRequest, ExecuteSkillRequest, ExecutionResult,
    ExecutionStepResult, ExecutionHistoryItem, RetrievedSkill,
)

router = APIRouter(prefix="/execution", tags=["execution"])

_execution_history: List[Dict[str, Any]] = []
_agent_activity_history: List[Dict[str, Any]] = []


@router.post("/skill", response_model=ExecutionResult)
async def execute_skill(
    req: ExecuteSkillRequest,
    app: AppState = Depends(get_app_state),
) -> ExecutionResult:
    skill = await app.wiki.get(req.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_id} 不存在")

    app.state_tracker.update(req.context)
    t0 = time.monotonic()

    record = await app.executor.execute_single(
        skill=skill,
        input_data=req.inputs,
    )

    latency = (time.monotonic() - t0) * 1000
    await app.wiki.record_execution(
        req.skill_id,
        success=record.status.value == "success",
        latency_ms=record.latency_ms or latency,
    )

    step = ExecutionStepResult(
        step_id="single",
        step_index=0,
        skill_id=skill.skill_id,
        skill_name=skill.name,
        status=record.status.value,
        outputs=record.output_data or {},
        result=record.output_data or {},
        latency_ms=record.latency_ms or latency,
        error=record.error_message,
    )
    return ExecutionResult(
        plan_id="single",
        goal=f"执行 {skill.name}",
        status=step.status,
        steps=[step],
        total_latency_ms=latency,
        final_state=app.state_tracker.current,
        retrieved_skills=[RetrievedSkill(
            skill_id=skill.skill_id,
            name=skill.name,
            description=skill.description,
            skill_type=skill.skill_type.value,
            score=1.0,
            match_reason="直接指定",
        )],
        experience_recorded=True,
    )


@router.post("/plan", response_model=ExecutionResult)
async def execute_plan(
    req: ExecutePlanRequest,
    app: AppState = Depends(get_app_state),
) -> ExecutionResult:
    t0 = time.monotonic()

    if not app.host_execution_agent:
        raise HTTPException(status_code=503, detail="Host Execution Agent 未初始化")

    async def publish_agent_activity(payload: Dict[str, Any]) -> None:
        event_payload = {
            "goal": req.goal,
            "time": datetime.utcnow().isoformat(),
            **payload,
        }
        _agent_activity_history.append({
            "time": event_payload["time"],
            "event": "agent_activity",
            "data": event_payload,
        })
        if len(_agent_activity_history) > 100:
            _agent_activity_history.pop(0)
        await broadcast("agent_activity", event_payload)

    run = await app.host_execution_agent.run(
        goal=req.goal,
        context=req.context,
        max_skills=req.max_skills,
        current_state=dict(req.context),
        activity_callback=publish_agent_activity,
    )
    search_results = _visible_retrieved_results(run.retrieved, run.executable_skills, limit=max(req.max_skills + 3, 6))
    retrieved = [
        RetrievedSkill(
            skill_id=r.skill.skill_id,
            name=r.skill.name,
            description=r.skill.description,
            skill_type=r.skill.skill_type.value,
            score=round(r.score, 3),
            match_reason=", ".join(getattr(r, "match_reasons", []) or []) or "ranked by lifecycle and quality signals",
        )
        for r in search_results
    ]

    plan = run.plan
    app.state_tracker = StateTracker(task_id=plan.task_id, initial_state=run.final_state)

    total_latency = (time.monotonic() - t0) * 1000
    steps = []
    skill_map_result = await app.wiki.get_many(list({step.skill_id for step in plan.steps}))
    for step in plan.steps:
        skill = skill_map_result.get(step.skill_id)
        if skill:
            await app.wiki.record_execution(
                skill.skill_id,
                success=(step.status.value if hasattr(step.status, "value") else str(step.status)) == "success",
                latency_ms=step.latency_ms or 0.0,
            )
        steps.append(ExecutionStepResult(
            step_id=step.step_id,
            step_index=step.step_index,
            skill_id=step.skill_id,
            skill_name=skill.name if skill else step.skill_id,
            status=step.status.value if hasattr(step.status, "value") else str(step.status),
            outputs=step.result or {},
            result=step.result or {},
            observations=step.observations,
            step_judgment=step.step_judgment,
            latency_ms=step.latency_ms or 0.0,
            error=step.error,
        ))

    success_count = sum(1 for s in steps if s.status == "success")
    result = ExecutionResult(
        plan_id=plan.plan_id,
        goal=req.goal,
        status="completed" if plan.is_complete else "partial",
        steps=steps,
        total_latency_ms=total_latency,
        final_state=run.final_state,
        retrieved_skills=retrieved,
        experience_recorded=True,
        agent_trace=[
            {
                "agent": trace.agent,
                "action": trace.action,
                "status": trace.status,
                "details": trace.details,
            }
            for trace in run.trace
        ],
    )

    learning = await _learn_from_execution(app, req.goal, result)
    if learning:
        result.suggested_skill = learning.get("suggested_skill")
        result.agent_trace.append({
            "agent": "ExecutionLearningAgent",
            "action": "reflect_and_update_skill_memory",
            "status": learning.get("status", "skipped"),
            "details": learning,
        })

    _execution_history.append({
        "execution_id": plan.plan_id,
        "goal": req.goal,
        "status": result.status,
        "step_count": len(steps),
        "success_count": success_count,
        "total_latency_ms": total_latency,
        "retrieved_skill_count": len(retrieved),
        "created_at": datetime.utcnow().isoformat(),
    })
    if len(_execution_history) > 50:
        _execution_history.pop(0)

    return result


def _visible_retrieved_results(search_results: List[Any], executable_skills: List[Any], *, limit: int) -> List[Any]:
    """Return a compact user-facing retrieval list.

    The execution agent intentionally retrieves a wider candidate pool for the
    second-pass LLM judge. The UI should show what mattered, not every
    exploratory candidate, otherwise users read it as "all of these were used".
    """
    selected_ids = {skill.skill_id for skill in executable_skills}
    selected = [result for result in search_results if result.skill.skill_id in selected_ids]
    selected_names = {result.skill.name for result in selected}
    high_signal = [
        result for result in search_results
        if result.skill.name not in selected_names and result.score >= 0.68
    ]
    fallback = [
        result for result in search_results
        if result.skill.name not in selected_names and result not in high_signal
    ]
    compact = selected + high_signal + fallback
    deduped = []
    seen = set()
    for result in compact:
        if result.skill.skill_id in seen:
            continue
        seen.add(result.skill.skill_id)
        deduped.append(result)
        if len(deduped) >= limit:
            break
    return deduped


async def _learn_from_execution(app: AppState, goal: str, result: ExecutionResult) -> Dict[str, Any]:
    """Reflect on an execution and optionally add a reusable Skill through the ingest agents."""
    if not app.meta_controller or not app.wiki:
        return {"status": "skipped", "reason": "learning agents are not configured"}
    if result.status != "completed":
        return {"status": "skipped", "reason": "only completed executions are learned"}

    proposal = _propose_skill_from_execution(goal, result)
    if not proposal:
        return {"status": "skipped", "reason": "no reusable pattern detected"}

    existing = await app.wiki.get_by_name(proposal["name"]) if hasattr(app.wiki, "get_by_name") else None
    if existing:
        return {
            "status": "reused",
            "reason": "similar skill already exists",
            "suggested_skill": {
                "skill_id": existing.skill_id,
                "name": existing.name,
                "description": existing.description,
            },
        }

    unit = SimpleNamespace(
        unit_id=f"execution:{result.plan_id}:learn:{proposal['name']}",
        source_type="task",
        raw_content=goal,
        extracted_actions=proposal["actions"],
        normalized_actions=[
            {"verb": action.split(" ")[0] if action else "do", "object": action, "description": action, "source": "execution_learning"}
            for action in proposal["actions"]
        ],
        summary=proposal["description"],
        proposed_skill_name=proposal["name"],
        proposed_description=proposal["description"],
        proposed_type=proposal["skill_type"],
        confidence=proposal["confidence"],
        index_keywords=proposal["tags"],
        index_embedding_hint=f"{proposal['name']}: {proposal['description']}",
        metadata={
            "source_id": f"task:execution_learning:{result.plan_id}",
            "source_title": "Execution-derived reusable task pattern",
            "source_description": proposal["description"],
            "source_type": "task",
            "tools": proposal["tools"],
            "version": "1.0.0",
            "interface": proposal["interface"],
            "implementation": proposal["implementation"],
            "capability_scope": proposal["capability_scope"],
            "capability_kind": proposal["capability_kind"],
            "target": proposal["target"],
            "tests": [f"{proposal['name']} learned from execution"],
            "extraction_policy": "execution_learning_agent",
        },
    )

    managed = await app.meta_controller.manage_ingested_unit(
        unit=unit,
        wiki=app.wiki,
        request_source_type="task",
    )
    if managed.skill:
        return {
            "status": "created" if managed.created else "reused",
            "reason": "execution produced a reusable non-overlapping skill",
            "suggested_skill": {
                "skill_id": managed.skill.skill_id,
                "name": managed.skill.name,
                "description": managed.skill.description,
            },
            "graph_nodes_created": managed.graph_nodes_created,
            "graph_edges_created": managed.graph_edges_created,
            "errors": managed.errors,
        }
    return {"status": "skipped", "reason": "learning agents did not create a skill", "errors": managed.errors}


def _propose_skill_from_execution(goal: str, result: ExecutionResult) -> Dict[str, Any] | None:
    final_state = result.final_state or {}
    query = str(final_state.get("query") or _extract_first_result_query(goal)).strip()
    if _is_first_result_goal(goal) and query:
        slug = _learned_slug(query)
        return {
            "name": f"open_first_search_result_for_{slug}",
            "description": f"Open the first web search result for '{query}'.",
            "skill_type": "functional",
            "confidence": 0.86,
            "tags": ["execution", "learned", "search", "first-result", "specialized", slug],
            "actions": [f"search for {query}", "open the first search result in Chrome"],
            "tools": ["Host open_search_first_result"],
            "capability_scope": "specialized",
            "capability_kind": "search_first_result",
            "target": query,
            "interface": _schema(
                {"query": ("string", f"Defaults to {query}", False, query)},
                {"launched": ("boolean", "Whether Chrome accepted the first-result open request"), "search_url": ("string", "First-result search URL")},
            ),
            "implementation": {
                "language": "python",
                "code": f'output["launched"] = True\noutput["query"] = input_data.get("query") or "{query}"',
                "tool_calls": ["host.open_search_first_result"],
            },
        }

    url = str(final_state.get("url") or "").strip()
    if url and _is_website_navigation_goal(goal) and "google.com/search" not in url:
        slug = _learned_slug(_extract_open_target(goal) or url)
        return {
            "name": f"open_{slug}_website",
            "description": f"Open the website resolved from the task '{goal}'.",
            "skill_type": "atomic",
            "confidence": 0.82,
            "tags": ["execution", "learned", "website", "url", "specialized", slug],
            "actions": [f"resolve target website {url}", "open the website in Chrome"],
            "tools": ["Host open_url_in_chrome"],
            "capability_scope": "specialized",
            "capability_kind": "url_open",
            "target": url,
            "interface": _schema(
                {"url": ("string", f"Defaults to {url}", False, url)},
                {"launched": ("boolean", "Whether Chrome accepted the URL open request"), "url": ("string", "Opened URL")},
            ),
            "implementation": {
                "language": "python",
                "code": f'output["launched"] = True\noutput["url"] = input_data.get("url") or "{url}"',
                "tool_calls": ["host.open_url_in_chrome"],
            },
        }

    command = str(final_state.get("command") or "").strip()
    if command and final_state.get("host_action") == "run_terminal_command":
        command_family = _terminal_command_family(command)
        slug = command_family or _terminal_command_slug(goal, command)
        default_path = _terminal_command_path(command)
        description = (
            "List files in a user-specified directory from Terminal using an agent-generated safe ls command."
            if command_family == "list_directory_contents"
            else f"Run the safe terminal command family '{slug}' for simple Terminal tasks."
        )
        input_schema = (
            {
                "path": ("string", "Directory path to list; the agent resolves this from the user task", False, default_path),
                "command": ("string", "Agent-generated safe terminal command", False, command),
            }
            if command_family == "list_directory_contents"
            else {"command": ("string", f"Defaults to {command}", False, command)}
        )
        return {
            "name": f"run_terminal_{slug}",
            "description": description,
            "skill_type": "atomic",
            "confidence": 0.84,
            "tags": ["execution", "learned", "terminal", "command", "safe", "generic", slug],
            "actions": [f"infer terminal command family {slug}", "open Terminal", f"run {command}"],
            "tools": ["Host run_terminal_command"],
            "capability_scope": "generic",
            "capability_kind": "terminal_command",
            "target": slug,
            "interface": _schema(
                input_schema,
                {"launched": ("boolean", "Whether Terminal accepted the generated command"), "stdout_preview": ("string", "Captured command output preview")},
            ),
            "implementation": {
                "language": "python",
                "code": f'output["launched"] = True\noutput["command"] = input_data.get("command") or "{command}"',
                "tool_calls": ["host.run_terminal_command"],
            },
        }
    return None


def _schema(inputs: Dict[str, tuple], outputs: Dict[str, tuple]) -> Dict[str, Any]:
    return {
        "input_schema": {
            "type": "object",
            "properties": {
                name: {
                    "type": spec[0],
                    "description": spec[1],
                    **({"default": spec[3]} if len(spec) >= 4 else {}),
                }
                for name, spec in inputs.items()
            },
            "required": [name for name, spec in inputs.items() if len(spec) >= 3 and spec[2]],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                name: {"type": spec[0], "description": spec[1]}
                for name, spec in outputs.items()
            },
        },
    }


def _is_first_result_goal(goal: str) -> bool:
    lowered = goal.lower()
    return (
        ("第一条" in goal or "第一项" in goal or "第一个" in goal or "首条" in goal)
        and ("搜索" in goal or "搜" in goal or "记录" in goal or "结果" in goal)
    ) or "first result" in lowered


def _extract_first_result_query(goal: str) -> str:
    query = goal
    for token in (
        "打开", "访问", "进入", "搜索出来的第一条记录", "搜索出来的第一条结果",
        "搜索出的第一条记录", "搜索出的第一条结果", "搜索结果第一条", "搜索第一条",
        "第一条记录", "第一条结果", "第一项记录", "第一个结果", "首条记录",
    ):
        query = query.replace(token, " ")
    return re.sub(r"\s+", " ", query).strip(" ，。,.")


def _is_website_navigation_goal(goal: str) -> bool:
    return any(token in goal for token in ("官网", "网站", "网页", "打开", "访问", "进入"))


def _extract_open_target(goal: str) -> str:
    target = goal
    for token in ("打开", "访问", "进入", "官网", "官方网站", "网站", "网页", "的", "页面"):
        target = target.replace(token, " ")
    return re.sub(r"\s+", " ", target).strip(" ，。,.")


def _learned_slug(value: str) -> str:
    aliases = {
        "国科大夏令营": "ucas_summer_camp",
        "哈工大威海": "hitwh",
        "哈尔滨工业大学威海": "hitwh",
        "百度": "baidu",
    }
    for key, slug in aliases.items():
        if key in value:
            return slug
    ascii_slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    if ascii_slug:
        return ascii_slug[:48]
    return "query_" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def _terminal_command_slug(goal: str, command: str) -> str:
    if command in {"printenv", "env"} or "环境变量" in goal:
        return "show_environment_variables"
    if command == "pwd" or "当前目录" in goal or "工作目录" in goal:
        return "show_working_directory"
    if command == "whoami" or "用户名" in goal:
        return "show_current_user"
    if command == "date" or "日期" in goal or "时间" in goal:
        return "show_date_time"
    return _learned_slug(command)


def _terminal_command_family(command: str) -> str:
    parts = command.strip().split()
    if not parts:
        return ""
    if parts[0] == "ls":
        return "list_directory_contents"
    return ""


def _terminal_command_path(command: str) -> str:
    parts = command.strip().split(maxsplit=1)
    return parts[1] if len(parts) > 1 and parts[0] == "ls" else ""


@router.get("/history", response_model=list)
async def get_execution_history() -> list:
    return list(reversed(_execution_history[-20:]))


@router.get("/activity", response_model=list)
async def get_agent_activity() -> list:
    return list(reversed(_agent_activity_history[-50:]))


@router.get("/state", response_model=dict)
async def get_current_state(
    app: AppState = Depends(get_app_state),
) -> dict:
    return app.state_tracker.current


@router.delete("/state", response_model=dict)
async def reset_state(
    app: AppState = Depends(get_app_state),
) -> dict:
    from ...layers.skill_runtime.state_tracker import StateTracker
    app.state_tracker = StateTracker(task_id="session")
    return {"ok": True, "message": "状态已重置"}
