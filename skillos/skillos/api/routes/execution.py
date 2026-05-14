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

    from ...layers.skill_runtime import (
        OrchestrationStrategy,
        execution_plan_from_skill_graph,
    )

    retrieval = await _retrieve_runtime_skills(app, req.goal, req.context, req.max_skills)
    available_skills = retrieval["skills"]
    retrieved = retrieval["retrieved"]
    skill_group = retrieval["skill_group"]

    strategy = OrchestrationStrategy(req.orchestration_strategy)
    graph = app.composer.compose(
        available_skills,
        task_description=req.goal,
        skill_group=skill_group,
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
    feedback = _verify_and_reflect(app, plan, req.goal, app.state_tracker.current, steps, overall_status)

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
        verification=feedback["verification"],
        reflection=feedback["reflection"],
        failure_type=feedback["failure_type"],
        recovery_route=feedback["recovery_route"],
        runtime_memory=_runtime_memory_summary(app),
        execution_graph=_execution_graph(req.goal, retrieved, plan, steps),
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
        "failure_type": result.failure_type,
        "recovery_route": result.recovery_route,
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


def _verify_and_reflect(
    app: AppState,
    plan: Any,
    goal: str,
    final_state: Dict[str, Any],
    steps: List[ExecutionStepResult],
    status: str,
) -> Dict[str, Any]:
    verification_summary: Optional[Dict[str, Any]] = None
    reflection_summary: Optional[Dict[str, Any]] = None
    failure_type = "none"
    recovery_route = "none"
    trace = {
        "plan_id": plan.plan_id,
        "status": status,
        "steps": [
            {
                "step_id": step.step_id,
                "skill_id": step.skill_id,
                "skill_name": step.skill_name,
                "status": step.status,
                "error": step.error,
            }
            for step in steps
        ],
    }

    verifier = getattr(app, "verifier", None)
    verification = None
    if verifier:
        try:
            verification = verifier.verify(goal, final_state, _trace_summary(trace))
            failure_type = str(getattr(verification, "failure_type", "none") or "none")
            recovery_route = str(getattr(verification, "recovery_route", "none") or "none")
            verification_summary = {
                "passed": bool(getattr(verification, "passed", False)),
                "score": float(getattr(verification, "score", 0.0) or 0.0),
                "issues": list(getattr(verification, "issues", []) or []),
                "suggestions": list(getattr(verification, "suggestions", []) or []),
                "failure_type": failure_type,
                "recovery_route": recovery_route,
            }
        except Exception as exc:
            logger.warning("Runtime verifier failed: %s", exc)

    should_reflect = status != "success" or (
        verification is not None and not bool(getattr(verification, "passed", False))
    )
    reflector = getattr(app, "reflector", None)
    if should_reflect and reflector:
        try:
            feedback = reflector.reflect(plan.plan_id, goal, trace, verification)
            failure_type = str(getattr(feedback, "failure_type", failure_type) or failure_type)
            recovery_route = str(getattr(feedback, "recovery_route", recovery_route) or recovery_route)
            reflection_summary = {
                "root_cause": str(getattr(feedback, "root_cause", "")),
                "failure_type": failure_type,
                "recovery_route": recovery_route,
                "failed_skill_ids": list(getattr(feedback, "failed_skill_ids", []) or []),
                "improvement_suggestions": list(getattr(feedback, "improvement_suggestions", []) or []),
                "skill_update_proposals": list(getattr(feedback, "skill_update_proposals", []) or []),
            }
        except Exception as exc:
            logger.warning("Runtime reflection failed: %s", exc)

    memory = getattr(getattr(app, "executor", None), "last_runtime_memory", None)
    if memory:
        if verification_summary is not None:
            memory.verification_summary = verification_summary
        if reflection_summary is not None:
            memory.reflection_summary = reflection_summary

    return {
        "verification": verification_summary,
        "reflection": reflection_summary,
        "failure_type": failure_type,
        "recovery_route": recovery_route,
    }


def _trace_summary(trace: Dict[str, Any]) -> str:
    return "; ".join(
        f"{step['skill_name']}={step['status']}"
        + (f" error={step['error']}" if step.get("error") else "")
        for step in trace.get("steps", [])
    )


def _runtime_memory_summary(app: AppState) -> Optional[Dict[str, Any]]:
    memory = getattr(getattr(app, "executor", None), "last_runtime_memory", None)
    if not memory:
        return None
    try:
        return memory.to_summary()
    except Exception as exc:
        logger.warning("Failed to summarize runtime memory: %s", exc)
        return None


def _execution_graph(
    goal: str,
    retrieved: List[RetrievedSkill],
    plan: Any,
    steps: List[ExecutionStepResult],
) -> Dict[str, Any]:
    """Build a frontend-friendly retrieval/composition/execution graph."""

    nodes: List[Dict[str, Any]] = [
        {
            "id": "goal",
            "label": goal,
            "kind": "goal",
            "status": "root",
            "level": 0,
        }
    ]
    edges: List[Dict[str, Any]] = []

    skill_node_ids = set()
    for index, skill in enumerate(retrieved):
        node_id = f"skill:{skill.skill_id}"
        skill_node_ids.add(node_id)
        nodes.append({
            "id": node_id,
            "label": skill.name,
            "kind": "retrieved_skill",
            "skill_id": skill.skill_id,
            "skill_type": skill.skill_type,
            "score": skill.score,
            "match_reason": skill.match_reason,
            "level": 1,
            "order": index,
        })
        edges.append({
            "id": f"goal->{node_id}",
            "source": "goal",
            "target": node_id,
            "kind": "retrieved",
        })

    step_result_by_id = {step.step_id: step for step in steps}
    step_node_ids: Dict[str, str] = {}
    for index, step in enumerate(getattr(plan, "steps", [])):
        result = step_result_by_id.get(step.step_id)
        node_id = f"step:{step.step_id}"
        step_node_ids[step.step_id] = node_id
        nodes.append({
            "id": node_id,
            "label": getattr(step, "skill_name", "") or getattr(step, "skill_id", ""),
            "kind": "execution_step",
            "step_id": step.step_id,
            "skill_id": step.skill_id,
            "status": result.status if result else str(getattr(step, "status", "")),
            "latency_ms": result.latency_ms if result else None,
            "error": result.error if result else getattr(step, "error", None),
            "level": 2 + len(getattr(step, "depends_on", []) or []),
            "order": index,
        })

    for step in getattr(plan, "steps", []):
        target = step_node_ids.get(step.step_id)
        if not target:
            continue
        dependencies = list(getattr(step, "depends_on", []) or [])
        if dependencies:
            for dep_id in dependencies:
                source = step_node_ids.get(dep_id)
                if source:
                    edges.append({
                        "id": f"{source}->{target}",
                        "source": source,
                        "target": target,
                        "kind": "depends_on",
                    })
        else:
            skill_source = f"skill:{step.skill_id}"
            edges.append({
                "id": f"{skill_source if skill_source in skill_node_ids else 'goal'}->{target}",
                "source": skill_source if skill_source in skill_node_ids else "goal",
                "target": target,
                "kind": "planned_as",
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "parallel_groups": getattr(plan, "metadata", {}).get("parallel_groups", []),
        "composition_source": getattr(plan, "metadata", {}).get("composition_source", ""),
        "root_id": "goal",
    }


async def _retrieve_runtime_skills(
    app: AppState,
    goal: str,
    context: Dict[str, Any],
    max_skills: int,
) -> Dict[str, Any]:
    """Retrieve executable skills through SkillRetriever, with search fallback."""

    retriever = getattr(app, "retriever", None)
    if retriever:
        try:
            retrieval = await retriever.retrieve(goal, current_state=context)
            skills = list(retrieval.skills[:max_skills])
            return {
                "skills": skills,
                "skill_group": retrieval.skill_group,
                "retrieved": [
                    RetrievedSkill(
                        skill_id=skill.skill_id,
                        name=skill.name,
                        description=skill.description,
                        skill_type=skill.skill_type.value,
                        score=round(float(retrieval.confidence or 0.0), 3),
                        match_reason=retrieval.rationale or "selected by runtime retriever",
                    )
                    for skill in skills
                ],
            }
        except Exception as exc:
            logger.warning("Runtime retriever failed; falling back to search: %s", exc)

    from ...layers.skill_repository.indexing import SearchQuery

    search_results = await app.search.search(
        SearchQuery(
            text=goal,
            max_results=max(max_skills, 50),
        )
    )
    runtime_results = _runtime_execution_results(search_results)[:max_skills]
    return {
        "skills": [r.skill for r in runtime_results],
        "skill_group": None,
        "retrieved": [
            RetrievedSkill(
                skill_id=r.skill.skill_id,
                name=r.skill.name,
                description=r.skill.description,
                skill_type=r.skill.skill_type.value,
                score=round(r.score, 3),
                match_reason=_format_match_reason(r),
            )
            for r in runtime_results
        ],
    }


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
