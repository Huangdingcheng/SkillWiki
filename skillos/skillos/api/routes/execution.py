"""Skill 执行路由。"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ..deps import AppState, get_app_state
from ..schemas import (
    ExecutePlanRequest, ExecuteSkillRequest, ExecutionResult,
    ExecutionStepResult, ExecutionHistoryItem, RetrievedSkill,
)

router = APIRouter(prefix="/execution", tags=["execution"])

_execution_history: List[Dict[str, Any]] = []


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
        result=record.output_data or {},
        latency_ms=record.latency_ms or latency,
        error=record.error_message,
    )
    return ExecutionResult(
        plan_id="single",
        goal=f"执行 {skill.name}",
        status="success" if step.status == "success" else "failed",
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
    app.state_tracker.update(req.context)
    t0 = time.monotonic()

    # 检索可用 Skill
    from ...layers.skill_repository.indexing import SearchQuery
    search_results = await app.search.search(SearchQuery(
        text=req.goal,
        max_results=req.max_skills,
    ))
    available_skills = [r.skill for r in search_results]

    retrieved = [
        RetrievedSkill(
            skill_id=r.skill.skill_id,
            name=r.skill.name,
            description=r.skill.description,
            skill_type=r.skill.skill_type.value,
            score=round(r.score, 3),
            match_reason="; ".join(r.match_reasons),
        )
        for r in search_results
    ]

    # 生成执行计划
    plan = await app.planner.plan(
        task_description=req.goal,
        available_skills=available_skills,
        current_state=app.state_tracker.current,
    )

    # 构建 skill_map
    skill_ids = list({step.skill_id for step in plan.steps})
    skill_map_result = await app.wiki.get_many(skill_ids)
    skill_map: Dict[str, Any] = {k: v for k, v in skill_map_result.items() if v}

    # 执行计划
    final_state = await app.executor.execute_plan(
        plan=plan,
        skill_map=skill_map,
        initial_state=app.state_tracker.current,
    )
    app.state_tracker.update(final_state)

    total_latency = (time.monotonic() - t0) * 1000
    steps = []
    for step in plan.steps:
        skill = skill_map.get(step.skill_id)
        steps.append(ExecutionStepResult(
            step_id=step.step_id,
            step_index=step.step_index,
            skill_id=step.skill_id,
            skill_name=skill.name if skill else step.skill_id,
            status=step.status.value if hasattr(step.status, "value") else str(step.status),
            result=step.result or {},
            latency_ms=step.latency_ms or 0.0,
            error=step.error,
        ))

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
    )

    _execution_history.append({
        "execution_id": plan.plan_id,
        "goal": req.goal,
        "status": result.status,
        "step_count": len(steps),
        "success_count": success_count,
        "total_latency_ms": total_latency,
        "retrieved_skill_count": len(retrieved),
        "created_at": datetime.utcnow(),
    })
    if len(_execution_history) > 50:
        _execution_history.pop(0)

    return result


@router.get("/history", response_model=List[ExecutionHistoryItem])
async def get_execution_history() -> List[ExecutionHistoryItem]:
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
    return {"ok": True, "message": "状态已重置"}
