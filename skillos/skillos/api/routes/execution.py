"""Skill execution routes."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ..deps import AppState, get_app_state
from ..schemas import (
    ExecutePlanRequest,
    ExecuteSkillRequest,
    ExecutionHistoryItem,
    ExecutionResult,
    ExecutionStepResult,
    RetrievedSkill,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/execution", tags=["execution"])

_execution_history: List[Dict[str, Any]] = []


@router.post("/skill", response_model=ExecutionResult)
async def execute_skill(
    req: ExecuteSkillRequest,
    app: AppState = Depends(get_app_state),
) -> ExecutionResult:
    skill = await app.wiki.get(req.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_id} does not exist")

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

    history_item = {
        "execution_id": str(uuid.uuid4()),
        "goal": f"Execute {skill.name}",
        "status": step.status,
        "step_count": 1,
        "success_count": 1 if step.status == "success" else 0,
        "total_latency_ms": latency,
        "retrieved_skill_count": 1,
        "created_at": datetime.utcnow().isoformat(),
    }
    _execution_history.append(history_item)
    if len(_execution_history) > 50:
        _execution_history.pop(0)

    history_repo = getattr(app, "execution_history_repo", None)
    if history_repo:
        try:
            await history_repo.save_plan_history(history_item)
        except Exception as exc:
            logger.warning("Failed to persist single-skill execution history: %s", exc)

    return ExecutionResult(
        plan_id="single",
        goal=f"Execute {skill.name}",
        status="success" if step.status == "success" else "failed",
        steps=[step],
        total_latency_ms=latency,
        final_state=app.state_tracker.current,
        retrieved_skills=[
            RetrievedSkill(
                skill_id=skill.skill_id,
                name=skill.name,
                description=skill.description,
                skill_type=skill.skill_type.value,
                score=1.0,
                match_reason="direct skill execution",
            )
        ],
        experience_recorded=True,
    )


@router.post("/plan", response_model=ExecutionResult)
async def execute_plan(
    req: ExecutePlanRequest,
    app: AppState = Depends(get_app_state),
) -> ExecutionResult:
    app.state_tracker.update(req.context)
    t0 = time.monotonic()

    from ...layers.skill_repository.indexing import SearchQuery
    from ...layers.skill_runtime import (
        OrchestrationStrategy,
        execution_plan_from_skill_graph,
    )

    search_results = await app.search.search(
        SearchQuery(
            text=req.goal,
            max_results=max(req.max_skills, 50),
        )
    )
    runtime_results = _runtime_execution_results(search_results)[: req.max_skills]
    available_skills = [r.skill for r in runtime_results]

    retrieved = [
        RetrievedSkill(
            skill_id=r.skill.skill_id,
            name=r.skill.name,
            description=r.skill.description,
            skill_type=r.skill.skill_type.value,
            score=round(r.score, 3),
            match_reason=_format_match_reason(r),
        )
        for r in runtime_results
    ]

    strategy = OrchestrationStrategy(req.orchestration_strategy)
    graph = app.composer.compose(
        available_skills,
        task_description=req.goal,
        strategy=strategy,
    )
    plan = execution_plan_from_skill_graph(
        graph,
        task_description=req.goal,
    )
    if not plan.steps:
        plan = await app.planner.plan(
            task_description=req.goal,
            available_skills=available_skills,
            current_state=app.state_tracker.current,
        )
        plan.metadata.setdefault("orchestration_strategy", strategy.value)

    skill_ids = list({step.skill_id for step in plan.steps})
    skill_map_result = await app.wiki.get_many(skill_ids)
    skill_map: Dict[str, Any] = {k: v for k, v in skill_map_result.items() if v}
    _populate_goal_input_mapping(plan, skill_map, req.goal, req.context)

    final_state = await app.executor.execute_plan(
        plan=plan,
        skill_map=skill_map,
        initial_state=app.state_tracker.current,
    )
    app.state_tracker.update(final_state)

    total_latency = (time.monotonic() - t0) * 1000
    steps: List[ExecutionStepResult] = []
    for step in plan.steps:
        skill = skill_map.get(step.skill_id)
        step_output = step.result or {}
        step_status = step.status.value if hasattr(step.status, "value") else str(step.status)
        step_latency = step.latency_ms or 0.0
        steps.append(
            ExecutionStepResult(
                step_id=step.step_id,
                step_index=step.step_index,
                skill_id=step.skill_id,
                skill_name=skill.name if skill else step.skill_id,
                status=step_status,
                outputs=step_output,
                result=step_output,
                latency_ms=step_latency,
                error=step.error,
            )
        )
        if skill:
            await app.wiki.record_execution(
                step.skill_id,
                success=step_status == "success",
                latency_ms=step_latency,
            )

    success_count = sum(1 for s in steps if s.status == "success")
    if steps and success_count == len(steps) and plan.is_complete:
        overall_status = "success"
    elif success_count == 0:
        overall_status = "failed"
    else:
        overall_status = "partial"

    result = ExecutionResult(
        plan_id=plan.plan_id,
        goal=req.goal,
        status=overall_status,
        steps=steps,
        total_latency_ms=total_latency,
        final_state=app.state_tracker.current,
        retrieved_skills=retrieved,
        experience_recorded=True,
        orchestration_strategy=strategy.value,
        parallel_groups=plan.metadata.get("parallel_groups", []),
        composition_source=plan.metadata.get("composition_source", ""),
    )

    history_item = {
        "execution_id": plan.plan_id,
        "goal": req.goal,
        "status": result.status,
        "step_count": len(steps),
        "success_count": success_count,
        "total_latency_ms": total_latency,
        "retrieved_skill_count": len(retrieved),
        "orchestration_strategy": strategy.value,
        "created_at": datetime.utcnow().isoformat(),
    }
    _execution_history.append(history_item)
    if len(_execution_history) > 50:
        _execution_history.pop(0)

    history_repo = getattr(app, "execution_history_repo", None)
    if history_repo:
        try:
            await history_repo.save_plan_history(history_item)
        except Exception as exc:
            logger.warning("Failed to persist execution history: %s", exc)

    return result


@router.get("/history", response_model=List[ExecutionHistoryItem])
async def get_execution_history(
    app: AppState = Depends(get_app_state),
) -> List[ExecutionHistoryItem]:
    history_repo = getattr(app, "execution_history_repo", None)
    if history_repo:
        try:
            return await history_repo.list_history(limit=20)
        except Exception as exc:
            logger.warning("Failed to read PostgreSQL execution history: %s", exc)
    return list(reversed(_execution_history[-20:]))


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
    return {"ok": True, "message": "state reset"}


def _format_match_reason(search_result: Any) -> str:
    reasons = getattr(search_result, "match_reasons", None)
    if reasons:
        return "; ".join(str(reason) for reason in reasons)
    reason = getattr(search_result, "match_reason", "")
    return str(reason or "")


def _runtime_execution_results(search_results: List[Any]) -> List[Any]:
    """Keep user-task execution candidates out of maintenance/meta skills."""

    filtered: List[Any] = []
    for result in search_results:
        skill = result.skill
        skill_type = getattr(getattr(skill, "skill_type", None), "value", "")
        tags = {str(tag).lower() for tag in getattr(skill, "tags", [])}
        name = str(getattr(skill, "name", "")).lower()
        if skill_type == "strategic":
            continue
        if tags & {"meta", "maintenance", "graph", "test"}:
            continue
        if name.startswith("test_graph_"):
            continue
        filtered.append(result)

    return filtered


def _populate_goal_input_mapping(
    plan: Any,
    skill_map: Dict[str, Any],
    goal: str,
    context: Dict[str, Any],
) -> None:
    """Fill simple missing step inputs from request context or task goal."""

    goal_like_fields = {
        "description",
        "goal",
        "task",
        "task_description",
        "query",
        "text",
        "prompt",
        "instruction",
    }
    for step in plan.steps:
        skill = skill_map.get(step.skill_id)
        if not skill or not getattr(skill, "interface", None):
            continue
        schema = getattr(skill.interface, "input_schema", {}) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not isinstance(properties, dict):
            continue
        required = schema.get("required", []) if isinstance(schema, dict) else []
        field_names = list(dict.fromkeys(list(required or []) + list(properties)))
        for field_name in field_names:
            field = str(field_name)
            if field in step.input_mapping:
                continue
            if field in context:
                step.input_mapping[field] = context[field]
            elif field.lower() in goal_like_fields:
                step.input_mapping[field] = goal


def _execution_status(steps: List[ExecutionStepResult]) -> str:
    if not steps:
        return "failed"
    success_count = sum(1 for step in steps if step.status == "success")
    if success_count == len(steps):
        return "success"
    return "partial" if success_count else "failed"
