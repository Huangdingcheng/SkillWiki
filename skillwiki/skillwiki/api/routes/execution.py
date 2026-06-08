"""Skill 执行路由。"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...layers.skill_runtime.verifier import evaluate_verifier_specs
from ...models.skill_model import SkillType
from ..deps import AppState, get_app_state
from ..schemas import (
    ExecutePlanRequest, ExecuteSkillRequest, ExecutionExperienceUnit,
    ExecutionResult, ExecutionStepResult, ExecutionHistoryItem, RetrievedSkill,
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
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_id} not found")

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
        input_mapping=req.inputs,
        outputs=record.output_data or {},
        result=record.output_data or {},
        latency_ms=record.latency_ms or latency,
        error=record.error_message,
    )
    verifier_summary = _verify_step_specs(
        goal=f"execute {skill.name}",
        skill=skill,
        step=step,
        final_state=app.state_tracker.current,
        app=app,
    )
    status = "success" if step.status == "success" else "failed"
    verifier_passed = None if verifier_summary is None else bool(verifier_summary["passed"])
    execution_id = f"single:{uuid.uuid4()}"
    experience_unit = _build_execution_experience_unit(
        execution_id=execution_id,
        goal=f"execute {skill.name}",
        status=status,
        steps=[step],
        final_state=app.state_tracker.current,
        retrieved_skills=[skill.skill_id],
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
        total_latency_ms=latency,
    )
    _store_execution_history(
        execution_id=experience_unit.source_execution_id,
        goal=f"execute {skill.name}",
        status=status,
        steps=[step],
        success_count=1 if status == "success" else 0,
        total_latency_ms=latency,
        retrieved_skill_count=1,
        experience_unit=experience_unit,
    )
    return ExecutionResult(
        plan_id=execution_id,
        goal=f"Execute {skill.name}",
        status=status,
        steps=[step],
        total_latency_ms=latency,
        final_state=app.state_tracker.current,
        retrieved_skills=[RetrievedSkill(
            skill_id=skill.skill_id,
            name=skill.name,
            description=skill.description,
            skill_type=skill.skill_type.value,
            score=1.0,
            match_reason="direct skill execution",
        )],
        experience_recorded=True,
        experience_unit=experience_unit,
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
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
    runtime_results = [
        r for r in search_results
        if _is_runtime_planning_skill(r.skill)
    ]
    if runtime_results:
        search_results = runtime_results
    available_skills = [r.skill for r in search_results]

    retrieved = [
        RetrievedSkill(
            skill_id=r.skill.skill_id,
            name=r.skill.name,
            description=r.skill.description,
            skill_type=r.skill.skill_type.value,
            score=round(r.score, 3),
            match_reason=_format_match_reason(r),
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
        step_output = step.result or {}
        step_status = step.status.value if hasattr(step.status, "value") else str(step.status)
        step_latency = step.latency_ms or 0.0
        steps.append(ExecutionStepResult(
            step_id=step.step_id,
            step_index=step.step_index,
            skill_id=step.skill_id,
            skill_name=skill.name if skill else step.skill_id,
            status=step_status,
            input_mapping=step.input_mapping,
            outputs=step_output,
            result=step_output,
            latency_ms=step_latency,
            error=step.error,
        ))
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

    verifier_summary = _verify_plan_steps(
        goal=req.goal,
        steps=steps,
        skill_map=skill_map,
        final_state=app.state_tracker.current,
        app=app,
    )
    verifier_passed = None if verifier_summary is None else bool(verifier_summary["passed"])
    experience_unit = _build_execution_experience_unit(
        execution_id=plan.plan_id,
        goal=req.goal,
        status=overall_status,
        steps=steps,
        final_state=app.state_tracker.current,
        retrieved_skills=[item.skill_id for item in retrieved],
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
        total_latency_ms=total_latency,
    )

    result = ExecutionResult(
        plan_id=plan.plan_id,
        goal=req.goal,
        status=overall_status,
        steps=steps,
        total_latency_ms=total_latency,
        final_state=app.state_tracker.current,
        retrieved_skills=retrieved,
        experience_recorded=True,
        experience_unit=experience_unit,
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
    )

    _store_execution_history(
        execution_id=plan.plan_id,
        goal=req.goal,
        status=result.status,
        steps=steps,
        success_count=success_count,
        total_latency_ms=total_latency,
        retrieved_skill_count=len(retrieved),
        experience_unit=experience_unit,
    )

    return result


def _is_runtime_planning_skill(skill: Any) -> bool:
    """Keep live execution planning focused on executable user-task skills."""
    skill_id = str(getattr(skill, "skill_id", "") or "")
    tags = {str(tag).lower() for tag in (getattr(skill, "tags", []) or [])}
    skill_type = getattr(skill, "skill_type", None)
    if isinstance(skill_type, SkillType):
        skill_type_value = skill_type.value
    else:
        skill_type_value = str(skill_type or "")

    if skill_id.startswith("test_graph_"):
        return False
    if "test" in tags or "meta" in tags:
        return False
    if skill_type_value == SkillType.STRATEGIC.value:
        return False

    implementation = getattr(skill, "implementation", None)
    if not implementation:
        return False
    return bool(
        getattr(implementation, "code", None)
        or getattr(implementation, "sub_skill_ids", None)
        or getattr(implementation, "tool_calls", None)
    )


@router.get("/history", response_model=List[ExecutionHistoryItem])
async def get_execution_history() -> List[ExecutionHistoryItem]:
    return list(reversed(_execution_history[-20:]))


@router.get("/history/{execution_id}/experience", response_model=ExecutionExperienceUnit)
async def get_execution_experience(execution_id: str) -> ExecutionExperienceUnit:
    for item in reversed(_execution_history):
        if item.get("execution_id") != execution_id:
            continue
        experience_unit = item.get("experience_unit")
        if isinstance(experience_unit, ExecutionExperienceUnit):
            return experience_unit
        if isinstance(experience_unit, dict):
            return ExecutionExperienceUnit.model_validate(experience_unit)
    raise HTTPException(status_code=404, detail=f"Execution experience not found: {execution_id}")


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
    return {"ok": True, "message": "State reset"}


def _format_match_reason(search_result: Any) -> str:
    reasons = getattr(search_result, "match_reasons", None)
    if reasons:
        return "; ".join(str(reason) for reason in reasons)
    reason = getattr(search_result, "match_reason", "")
    return str(reason or "")


def _verify_plan_steps(
    goal: str,
    steps: List[ExecutionStepResult],
    skill_map: Dict[str, Any],
    final_state: Dict[str, Any],
    app: AppState,
) -> Optional[Dict[str, Any]]:
    step_summaries = []
    for step in steps:
        skill = skill_map.get(step.skill_id)
        if not skill:
            continue
        summary = _verify_step_specs(goal, skill, step, final_state, app)
        if summary:
            step_summaries.append(summary)

    if not step_summaries:
        return None

    passed = all(bool(item["passed"]) for item in step_summaries)
    return {
        "mode": "deterministic",
        "passed": passed,
        "checked_skills": len(step_summaries),
        "results": step_summaries,
    }


def _verify_step_specs(
    goal: str,
    skill: Any,
    step: ExecutionStepResult,
    final_state: Dict[str, Any],
    app: AppState,
) -> Optional[Dict[str, Any]]:
    evaluation = getattr(skill, "evaluation", None)
    verifier_specs = getattr(evaluation, "verifier_specs", None) or []
    if not verifier_specs:
        return None

    payload = _step_verifier_document(step, final_state)
    verifier = getattr(app, "verifier", None)
    if verifier:
        verification = verifier.verify(goal, payload, verifier_specs=verifier_specs)
    else:
        verification = evaluate_verifier_specs(verifier_specs, payload, goal=goal)

    return {
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "step_id": step.step_id,
        "passed": verification.passed,
        "score": verification.score,
        "issues": verification.issues,
        "suggestions": verification.suggestions,
        "details": verification.details,
    }


def _step_verifier_document(
    step: ExecutionStepResult,
    final_state: Dict[str, Any],
) -> Dict[str, Any]:
    output = dict(step.result or step.outputs or {})
    output.setdefault("success", step.status == "success")
    return {
        "success": step.status == "success",
        "input": dict(step.input_mapping or {}),
        "output": output,
        "final_state": final_state,
        "step": {
            "step_id": step.step_id,
            "step_index": step.step_index,
            "skill_id": step.skill_id,
            "status": step.status,
            "error": step.error,
        },
    }


def _store_execution_history(
    *,
    execution_id: str,
    goal: str,
    status: str,
    steps: List[ExecutionStepResult],
    success_count: int,
    total_latency_ms: float,
    retrieved_skill_count: int,
    experience_unit: ExecutionExperienceUnit,
) -> None:
    _execution_history.append({
        "execution_id": execution_id,
        "goal": goal,
        "status": status,
        "step_count": len(steps),
        "success_count": success_count,
        "total_latency_ms": total_latency_ms,
        "retrieved_skill_count": retrieved_skill_count,
        "created_at": datetime.utcnow(),
        "experience_unit_id": experience_unit.unit_id,
        "experience_source_type": experience_unit.source_type,
        "experience_unit": experience_unit.model_dump(),
    })
    if len(_execution_history) > 50:
        _execution_history.pop(0)


def _build_execution_experience_unit(
    *,
    execution_id: str,
    goal: str,
    status: str,
    steps: List[ExecutionStepResult],
    final_state: Dict[str, Any],
    retrieved_skills: List[str],
    verifier_passed: Optional[bool],
    verifier_summary: Optional[Dict[str, Any]],
    total_latency_ms: float,
) -> ExecutionExperienceUnit:
    extracted_actions = [_step_action_text(step) for step in steps]
    normalized_actions = [
        {
            "verb": "execute",
            "object": step.skill_name or step.skill_id,
            "skill_id": step.skill_id,
            "status": step.status,
            "input_mapping": step.input_mapping,
            "outputs": step.result or step.outputs,
            "error": step.error,
        }
        for step in steps
    ]
    proposed_name = _safe_skill_name_from_goal(goal)
    summary = _execution_experience_summary(goal, status, steps, verifier_passed)
    raw_payload = {
        "execution_id": execution_id,
        "goal": goal,
        "status": status,
        "steps": [
            {
                "step_id": step.step_id,
                "step_index": step.step_index,
                "skill_id": step.skill_id,
                "skill_name": step.skill_name,
                "status": step.status,
                "input_mapping": step.input_mapping,
                "outputs": step.result or step.outputs,
                "error": step.error,
            }
            for step in steps
        ],
        "final_state": final_state,
        "verifier_passed": verifier_passed,
        "verifier_summary": verifier_summary,
    }
    return ExecutionExperienceUnit(
        unit_id=f"execution:{execution_id}",
        source_type="agent_execution",
        source_execution_id=execution_id,
        raw_content=json.dumps(raw_payload, ensure_ascii=False),
        extracted_actions=extracted_actions,
        normalized_actions=normalized_actions,
        summary=summary,
        proposed_skill_name=proposed_name,
        proposed_description=summary,
        proposed_type="functional" if len(steps) > 1 else "atomic",
        confidence=_execution_experience_confidence(status, verifier_passed, steps),
        index_keywords=_execution_keywords(goal, retrieved_skills, steps),
        index_embedding_hint=f"{goal} {' '.join(extracted_actions)}".strip(),
        metadata={
            "paper_method": "XSkill action-level experience stream",
            "paper_backlog_task": "C-P1-2",
            "source_execution_id": execution_id,
            "retrieved_skill_ids": retrieved_skills,
            "total_latency_ms": total_latency_ms,
            "verifier_passed": verifier_passed,
            "step_count": len(steps),
        },
    )


def _step_action_text(step: ExecutionStepResult) -> str:
    return f"{step.status or 'unknown'}: {step.skill_name or step.skill_id} with {step.input_mapping or {}}"


def _execution_experience_summary(
    goal: str,
    status: str,
    steps: List[ExecutionStepResult],
    verifier_passed: Optional[bool],
) -> str:
    skill_names = [step.skill_name or step.skill_id for step in steps]
    skill_phrase = ", ".join(skill_names) if skill_names else "no selected skills"
    verifier_phrase = (
        "no verifier evidence"
        if verifier_passed is None
        else f"verifier {'passed' if verifier_passed else 'failed'}"
    )
    return f"Execution '{goal}' finished with status {status} using {skill_phrase}; {verifier_phrase}."


def _execution_experience_confidence(
    status: str,
    verifier_passed: Optional[bool],
    steps: List[ExecutionStepResult],
) -> float:
    if verifier_passed is True:
        return 0.85
    if verifier_passed is False:
        return 0.35
    if status == "success" and steps:
        return 0.7
    if status == "partial":
        return 0.45
    return 0.25


def _execution_keywords(
    goal: str,
    retrieved_skills: List[str],
    steps: List[ExecutionStepResult],
) -> List[str]:
    values = list(retrieved_skills)
    values.extend(step.skill_name or step.skill_id for step in steps)
    values.extend(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", goal.lower()))
    keywords: List[str] = []
    seen = set()
    for value in values:
        token = str(value).strip().lower()
        if token and token not in seen:
            seen.add(token)
            keywords.append(token)
    return keywords[:12]


def _safe_skill_name_from_goal(goal: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", goal.lower())
    if not words:
        return "skill_from_execution"
    return "skill_from_" + "_".join(words[:6])


def _execution_status(steps: List[ExecutionStepResult]) -> str:
    if not steps:
        return "failed"
    success_count = sum(1 for step in steps if step.status == "success")
    if success_count == len(steps):
        return "success"
    return "partial" if success_count else "failed"
