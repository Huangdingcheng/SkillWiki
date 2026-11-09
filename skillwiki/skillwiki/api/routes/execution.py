"""Skill 执行路由。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...layers.skill_runtime.verifier import evaluate_verifier_specs
from ...layers.skill_runtime.state_tracker import StateTracker
from ...layers.skill_governance.arex_skillguard_adapter import (
    assess_arex_refresh,
    assess_skillguard_contract_drift,
)
from ...models.skill_model import SkillType
from ..deps import AppState, get_app_state
from ..execution_history import history_directory, load as load_execution_history, save as save_execution_history
from ..schemas import (
    ExecutePlanRequest, ExecuteSkillRequest, ExecutionExperienceUnit,
    ExecutionResult, ExecutionStepResult, ExecutionHistoryItem, RetrievedSkill,
    RetrievalTrace,
)

router = APIRouter(prefix="/execution", tags=["execution"])

_execution_history: List[Dict[str, Any]] = []


@router.post("/skill", response_model=ExecutionResult)
async def execute_skill(
    req: ExecuteSkillRequest,
    app: AppState = Depends(get_app_state),
) -> ExecutionResult:
    try:
        governance = _evaluate_governance_context(req.context)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"message": "invalid governance context", "cause": str(exc)}) from exc
    if governance["blocked"]:
        blocked_id = f"blocked:{uuid.uuid4()}"
        _persist_retrieval_trace(
            app=app,
            execution_id=blocked_id,
            goal=f"execute skill {req.skill_id}",
            trace=RetrievalTrace(
                trace_id=blocked_id,
                adapter="skillwiki-direct",
                blocked=["execution blocked by governance gate"],
                governance=governance["checks"],
            ).model_dump(mode="json"),
        )
        raise HTTPException(
            status_code=409,
            detail={"message": "execution blocked by source or environment governance gate", "trace_id": blocked_id, "governance": governance["checks"]},
        )
    skill = await app.wiki.get(req.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_id} not found")
    if skill.state.value != "S4":
        blocked_id = f"blocked:{uuid.uuid4()}"
        _persist_blocked_trace(app=app, execution_id=blocked_id, goal=f"execute {skill.name}",
            reason="Only released skills can use the runtime; validate drafts through candidate replay", governance=governance["checks"])
        raise HTTPException(status_code=409, detail={"message": "Skill is not released", "trace_id": blocked_id})

    from ..component_demo import persisted_checks
    saved = persisted_checks(app, skill)
    governance["checks"]["persisted"] = saved["checks"]
    if saved["blocked"]:
        blocked_id = f"blocked:{uuid.uuid4()}"
        _persist_blocked_trace(app=app, execution_id=blocked_id, goal=f"execute {skill.name}",
            reason="Persisted source/environment checks do not admit this version", governance=governance["checks"])
        raise HTTPException(status_code=409, detail={"message": "Source/environment check required", "trace_id": blocked_id, "governance": governance["checks"]})

    state_before = app.state_tracker.current
    proposed_tracker = StateTracker("single-request", state_before)
    proposed_tracker.update(req.context)
    t0 = time.monotonic()

    try:
        record = await app.executor.execute_single(
            skill=skill, input_data=req.inputs, initial_state=proposed_tracker.current,
        )
    except Exception as exc:
        failed_id = f"failed:{uuid.uuid4()}"
        _persist_blocked_trace(app=app, execution_id=failed_id, goal=f"execute {skill.name}",
            reason=f"Execution failed: {type(exc).__name__}", governance=governance["checks"])
        raise HTTPException(status_code=503, detail={"message": "Execution failed", "trace_id": failed_id}) from exc

    latency = (time.monotonic() - t0) * 1000
    step = ExecutionStepResult(
        step_id="single",
        step_index=0,
        skill_id=skill.skill_id,
        skill_name=skill.name,
        status=record.status.value,
        input_mapping=req.inputs,
        outputs=record.output_data or {},
        result=record.output_data or {},
        execution_evidence=record.execution_evidence,
        latency_ms=record.latency_ms or latency,
        error=record.error_message,
    )
    verifier_summary = _verify_step_specs(
        goal=f"execute {skill.name}",
        skill=skill,
        step=step,
        final_state=record.state_after,
        app=app,
    )
    status = "success" if step.status == "success" else "failed"
    verifier_passed = None if verifier_summary is None else bool(verifier_summary["passed"])
    if verifier_passed is False:
        status = "failed"
    final_state = record.state_after if status == "success" else state_before
    if status == "success":
        app.state_tracker.update(final_state)
    await app.wiki.record_execution(req.skill_id, success=status == "success",
        latency_ms=record.latency_ms or latency)
    execution_id = f"single:{uuid.uuid4()}"
    experience_unit = _build_execution_experience_unit(
        execution_id=execution_id,
        goal=f"execute {skill.name}",
        status=status,
        steps=[step],
        final_state=final_state,
        retrieved_skills=[skill.skill_id],
        retrieved_skill_versions={skill.skill_id: skill.version},
        provenance_refs=list(getattr(getattr(skill, "provenance", None), "source_ids", []) or []),
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
        governance=governance["checks"],
        total_latency_ms=latency,
    )
    _store_execution_history(
        app=app,
        execution_id=experience_unit.source_execution_id,
        goal=f"execute {skill.name}",
        status=status,
        steps=[step],
        success_count=1 if status == "success" else 0,
        total_latency_ms=latency,
        retrieved_skill_count=1,
        experience_unit=experience_unit,
    )
    direct_trace = RetrievalTrace(
        trace_id=execution_id,
        adapter="skillwiki-direct",
        candidates=[{
            "skill_id": skill.skill_id,
            "version": skill.version,
            "content_hash": _skill_content_hash(skill),
        }],
        governance=governance["checks"],
        provenance={
            "skill_id": skill.skill_id,
            "source_type": getattr(getattr(skill, "provenance", None), "source_type", None),
            "source_ids": list(getattr(getattr(skill, "provenance", None), "source_ids", []) or []),
        },
        execution={"status": status, "verifier_passed": verifier_passed,
                   "verifier_summary": verifier_summary,
                   "steps": [step.model_dump(mode="json")],
                   "final_state": final_state},
    )
    _persist_retrieval_trace(
        app=app,
        execution_id=execution_id,
        goal=f"execute {skill.name}",
        trace=direct_trace.model_dump(mode="json"),
    )
    return ExecutionResult(
        plan_id=execution_id,
        goal=f"Execute {skill.name}",
        status=status,
        steps=[step],
        total_latency_ms=latency,
        final_state=final_state,
        retrieved_skills=[RetrievedSkill(
            skill_id=skill.skill_id,
            name=skill.name,
            description=skill.description,
            skill_type=skill.skill_type.value,
            score=1.0,
            match_reason="direct skill execution",
            version=skill.version,
        )],
        experience_recorded=True,
        experience_unit=experience_unit,
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
        retrieval_trace=direct_trace,
    )


@router.post("/plan", response_model=ExecutionResult)
async def execute_plan(
    req: ExecutePlanRequest,
    app: AppState = Depends(get_app_state),
) -> ExecutionResult:
    state_before = app.state_tracker.current
    proposed_tracker = StateTracker("plan-request", state_before)
    proposed_tracker.update(req.context)
    request_state = proposed_tracker.current
    t0 = time.monotonic()
    request_trace_id = f"trace:{uuid.uuid4()}"

    try:
        governance = _evaluate_governance_context(req.context)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "invalid governance context", "cause": str(exc)},
        ) from exc
    if governance["blocked"]:
        _persist_blocked_trace(
            app=app,
            execution_id=request_trace_id,
            goal=req.goal,
            reason="execution blocked by source or environment governance gate",
            governance=governance["checks"],
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "execution blocked by source or environment governance gate",
                "trace_id": request_trace_id,
                "governance": governance["checks"],
            },
        )

    # Fabric 负责候选检索和关系解释；SkillWiki 仍负责状态、版本和执行。
    fabric_trace = RetrievalTrace(trace_id=request_trace_id)
    fabric_trace.governance = governance["checks"]
    fabric_results = None
    try:
        from ...models.skill_model import SkillState
        released_all = await app.wiki.list(state=SkillState.RELEASED, limit=200)
        released = [skill for skill in released_all if _is_runtime_planning_skill(skill)]
        if req.skill_ids is not None:
            requested = set(req.skill_ids)
            missing = requested - {skill.skill_id for skill in released}
            if missing:
                _persist_blocked_trace(app=app, execution_id=request_trace_id, goal=req.goal,
                    reason="Frozen catalog includes missing or unreleased skills", governance=governance["checks"])
                raise HTTPException(status_code=409, detail={"message": "Frozen catalog includes missing or unreleased skills",
                    "skill_ids": sorted(missing), "trace_id": request_trace_id})
            released = [skill for skill in released if skill.skill_id in requested]
        from ..component_demo import persisted_checks
        admitted = []
        catalog_checks = []
        for skill in released:
            saved = persisted_checks(app, skill)
            catalog_checks.extend(saved["checks"])
            if not saved["blocked"]:
                admitted.append(skill)
        released = admitted
        governance["checks"]["persisted"] = catalog_checks
        subgraph = await app.graph.get_subgraph(
            skill_ids=[skill.skill_id for skill in released], depth=2
        ) if app.graph else None
        fabric_results = app.fabric_retriever(
            req.goal,
            released,
            getattr(subgraph, "edges", []) if subgraph else [],
            limit=req.max_skills,
        )
        valid = {skill.skill_id: skill for skill in released}
        missing_ids = [item["skill_id"] for item in fabric_results.candidates if item["skill_id"] not in valid]
        if missing_ids:
            _persist_blocked_trace(
                app=app,
                execution_id=request_trace_id,
                goal=req.goal,
                reason="Fabric candidate cannot be mapped to the released SkillWiki catalog",
                governance=governance["checks"],
                candidates=list(fabric_results.candidates),
                source_commit=fabric_results.source_commit,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Fabric candidate cannot be mapped to the released SkillWiki catalog",
                    "skill_ids": missing_ids,
                    "trace_id": request_trace_id,
                },
            )
        ordered = [valid[item["skill_id"]] for item in fabric_results.candidates]
        if ordered:
            available_skills = ordered
            by_id = {item["skill_id"]: item for item in fabric_results.candidates}
            retrieved = [RetrievedSkill(
                skill_id=skill.skill_id,
                name=skill.name,
                description=skill.description,
                skill_type=skill.skill_type.value,
                score=round(float(by_id[skill.skill_id]["score"]), 3),
                match_reason=("seed retrieval" if by_id[skill.skill_id]["is_seed"] else "semantic graph expansion"),
                version=skill.version,
                graph_depth=int(by_id[skill.skill_id]["graph_depth"]),
                is_seed=bool(by_id[skill.skill_id]["is_seed"]),
            ) for skill in ordered]
            fabric_trace = RetrievalTrace(
                trace_id=request_trace_id,
                adapter="skillnet-fabric-core",
                source_commit=fabric_results.source_commit,
                embedding_model=fabric_results.embedding_model,
                embedding_mode=fabric_results.embedding_mode,
                candidates=[
                    {**item, "version": valid[item["skill_id"]].version}
                    for item in fabric_results.candidates
                ],
                alternatives=list(fabric_results.alternatives),
                governance=governance["checks"],
            )
        else:
            available_skills = []
            retrieved = []
            fabric_trace = RetrievalTrace(
                trace_id=request_trace_id,
                adapter="skillnet-fabric-core",
                source_commit=fabric_results.source_commit,
                embedding_model=fabric_results.embedding_model,
                embedding_mode=fabric_results.embedding_mode,
                blocked=["no released candidate matched the task"],
                governance=governance["checks"],
            )
    except HTTPException:
        # A bad identity/version mapping is a governance failure, not a reason
        # to silently fall back to legacy lexical retrieval.
        raise
    except Exception as exc:
        if not callable(getattr(app, "fabric_retriever", None)):
            # Older test harnesses/AppState shims predate the Fabric hook.
            fabric_results = None
        else:
            _persist_blocked_trace(
                app=app,
                execution_id=request_trace_id,
                goal=req.goal,
                reason="SkillNet-Fabric retrieval is unavailable; execution was not started",
                governance=governance["checks"],
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "SkillNet-Fabric retrieval is unavailable; execution was not started",
                    "cause": type(exc).__name__,
                    "trace_id": request_trace_id,
                },
            ) from exc

    # Only test/legacy AppState implementations without a Fabric hook use the
    # pre-existing lexical path. The real AppState always installs the adapter.
    if not callable(getattr(app, "fabric_retriever", None)):
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
                version=r.skill.version,
            )
            for r in search_results
        ]

    # 生成执行计划
    try:
        planning_options = {}
        from ...layers.skill_runtime.planner import SkillPlanner
        if req.planning_mode == "model" and isinstance(app.planner, SkillPlanner):
            planning_options.update(force_llm=True, fallback_on_llm_error=False,
                fallback_on_invalid_response=False)
        if req.planning_mode == "single_skill" and req.skill_ids and len(set(req.skill_ids)) == 1 and len(available_skills) == 1:
            planning_options["force_fallback"] = True
        plan = await app.planner.plan(
            task_description=req.goal, available_skills=available_skills,
            current_state=request_state,
            **planning_options,
        )
        if planning_options.get("force_fallback"):
            plan.metadata["source"] = "explicit_single_skill_deterministic"
            plan.metadata["model_calls"] = 0
    except Exception as exc:
        _persist_blocked_trace(app=app, execution_id=request_trace_id, goal=req.goal,
            reason=f"Planning failed: {type(exc).__name__}", governance=governance["checks"])
        raise HTTPException(status_code=503, detail={"message": "Planning failed", "trace_id": request_trace_id}) from exc

    # 构建 skill_map
    skill_ids = list({step.skill_id for step in plan.steps})
    skill_map_result = await app.wiki.get_many(skill_ids)
    skill_map: Dict[str, Any] = {k: v for k, v in skill_map_result.items() if v}

    if fabric_results is not None:
        indexed_versions = {
            item["skill_id"]: valid[item["skill_id"]].version
            for item in fabric_results.candidates
            if item["skill_id"] in valid
        }
        changed = [
            skill_id for skill_id in skill_ids
            if skill_id not in skill_map
            or indexed_versions.get(skill_id) != skill_map[skill_id].version
            or skill_map[skill_id].state.value != "S4"
        ]
        from ...layers.skill_runtime.skillnet_fabric_adapter import _skill_text
        indexed_content = {item["skill_id"]: item.get("content_hash") for item in fabric_results.candidates}
        changed.extend(skill_id for skill_id in skill_ids if skill_id in skill_map
            and indexed_content.get(skill_id)
            and indexed_content[skill_id].removeprefix("sha256:") != hashlib.sha256(_skill_text(skill_map[skill_id]).encode("utf-8")).hexdigest())
        changed.extend(skill_id for skill_id in skill_ids if skill_id in skill_map
            and persisted_checks(app, skill_map[skill_id])["blocked"])
        if changed:
            _persist_blocked_trace(
                app=app,
                execution_id=request_trace_id,
                goal=req.goal,
                reason="Selected SkillWiki release changed after retrieval; execution blocked",
                governance=governance["checks"],
                candidates=list(fabric_results.candidates),
                source_commit=fabric_results.source_commit,
            )
            raise HTTPException(
                status_code=409,
                detail={"message": "Selected SkillWiki release changed after retrieval; execution blocked", "skill_ids": changed, "trace_id": request_trace_id},
            )

    # 执行计划
    try:
        final_state = await app.executor.execute_plan(
            plan=plan, skill_map=skill_map, initial_state=request_state,
        )
    except Exception as exc:
        _persist_blocked_trace(app=app, execution_id=request_trace_id, goal=req.goal,
            reason=f"Execution failed: {type(exc).__name__}", governance=governance["checks"])
        raise HTTPException(status_code=503, detail={"message": "Execution failed", "trace_id": request_trace_id}) from exc

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
            execution_evidence=step.execution_evidence,
            latency_ms=step_latency,
            error=step.error,
        ))

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
        final_state=final_state,
        app=app,
    )
    verifier_passed = None if verifier_summary is None else bool(verifier_summary["passed"])
    verdicts = {row["step_id"]: row["passed"] for row in (verifier_summary or {}).get("results", [])}
    for step in steps:
        if step.skill_id in skill_map:
            await app.wiki.record_execution(step.skill_id,
                success=step.status == "success" and verdicts.get(step.step_id) is not False,
                latency_ms=step.latency_ms or 0.0)
    if verifier_passed is False:
        overall_status = "failed"
    if overall_status == "success":
        app.state_tracker.update(final_state)
    else:
        final_state = state_before
    experience_unit = _build_execution_experience_unit(
        execution_id=plan.plan_id,
        goal=req.goal,
        status=overall_status,
        steps=steps,
        final_state=final_state,
        retrieved_skills=[item.skill_id for item in retrieved],
        retrieved_skill_versions={
            item.skill_id: item.version
            for item in retrieved
            if item.version
        },
        provenance_refs=[
            value for value in (
                fabric_trace.source_commit,
                fabric_trace.adapter,
            ) if value
        ],
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
        total_latency_ms=total_latency,
        governance=governance["checks"],
    )

    result = ExecutionResult(
        plan_id=plan.plan_id,
        goal=req.goal,
        status=overall_status,
        steps=steps,
        total_latency_ms=total_latency,
        final_state=final_state,
        retrieved_skills=retrieved,
        experience_recorded=True,
        experience_unit=experience_unit,
        verifier_passed=verifier_passed,
        verifier_summary=verifier_summary,
        retrieval_trace=fabric_trace,
    )

    fabric_trace.trace_id = plan.plan_id
    fabric_trace.plan = {
        "plan_id": plan.plan_id,
        "metadata": plan.metadata,
        "steps": [
            {"step_id": step.step_id, "skill_id": step.skill_id, "step_index": step.step_index,
             "input_mapping": step.input_mapping}
            for step in plan.steps
        ],
    }
    fabric_trace.execution = {
        "status": result.status,
        "verifier_passed": verifier_passed,
        "verifier_summary": verifier_summary,
        "total_latency_ms": total_latency,
        "steps": [step.model_dump(mode="json") for step in steps],
        "final_state": final_state,
    }
    fabric_trace.provenance = {
        "skills": {
            skill.skill_id: {
                "version": skill.version,
                "content_hash": _skill_content_hash(skill),
                "source_type": getattr(getattr(skill, "provenance", None), "source_type", None),
                "source_ids": list(getattr(getattr(skill, "provenance", None), "source_ids", []) or []),
            }
            for skill in skill_map.values()
        }
    }
    result.retrieval_trace = fabric_trace

    _store_execution_history(
        app=app,
        execution_id=plan.plan_id,
        goal=req.goal,
        status=result.status,
        steps=steps,
        success_count=success_count,
        total_latency_ms=total_latency,
        retrieved_skill_count=len(retrieved),
        experience_unit=experience_unit,
    )

    _persist_retrieval_trace(
        app=app,
        execution_id=plan.plan_id,
        goal=req.goal,
        trace=fabric_trace.model_dump(mode="json"),
    )

    return result


def _persist_retrieval_trace(
    *, app: AppState, execution_id: str, goal: str, trace: Dict[str, Any]
) -> None:
    path = getattr(app, "execution_trace_path", None)
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    row = {"execution_id": execution_id, "goal": goal, "retrieval_trace": trace}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _persist_blocked_trace(
    *,
    app: AppState,
    execution_id: str,
    goal: str,
    reason: str,
    governance: Optional[Dict[str, Any]] = None,
    candidates: Optional[List[Dict[str, Any]]] = None,
    source_commit: Optional[str] = None,
) -> None:
    _persist_retrieval_trace(
        app=app,
        execution_id=execution_id,
        goal=goal,
        trace=RetrievalTrace(
            trace_id=execution_id,
            adapter="skillwiki-governance",
            source_commit=source_commit,
            candidates=candidates or [],
            blocked=[reason],
            governance=governance or {},
        ).model_dump(mode="json"),
    )


def _skill_content_hash(skill: Any) -> str:
    payload = {
        "skill_id": str(getattr(skill, "skill_id", "")),
        "version": str(getattr(skill, "version", "")),
        "name": str(getattr(skill, "name", "")),
        "description": str(getattr(skill, "description", "")),
        "implementation": getattr(getattr(skill, "implementation", None), "model_dump", lambda: {})(),
    }
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _evaluate_governance_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Run optional AREX/SkillGuard demo gates from explicit request context.

    Existing callers omit these keys and retain the prior execution behavior.
    A requested gate is fail-closed: ``stale``, ``removed``, ``unknown`` and
    SkillGuard ``block``/``defer`` stop execution before retrieval or planning.
    """
    checks: Dict[str, Any] = {}
    blocked = False
    refresh_state = context.get("source_refresh_state")
    if refresh_state is not None:
        source_commit = context.get("source_commit")
        if not isinstance(source_commit, str) or not source_commit.strip():
            checks["arex"] = {"status": "unknown", "reason": "source_commit is missing"}
            blocked = True
        else:
            assessment = assess_arex_refresh(
                source_commit=source_commit,
                observed_state=str(refresh_state),
                evidence=context.get("source_refresh_evidence", ()),
            )
            checks["arex"] = assessment.to_dict()
            blocked = blocked or assessment.observed_state in {"stale", "removed", "unknown"}

    change_kind = context.get("skillguard_change_kind")
    if change_kind is not None:
        assessment = assess_skillguard_contract_drift(
            change_kind=str(change_kind),
            environment_status=str(context.get("skillguard_environment_status", "known")),
            evidence=context.get("skillguard_evidence", ()),
        )
        checks["skillguard"] = assessment.to_dict()
        blocked = blocked or assessment.status in {"block", "defer"}

    return {"checks": checks, "blocked": blocked}


@router.get("/trace/{execution_id}", response_model=RetrievalTrace)
async def get_retrieval_trace(
    execution_id: str,
    app: AppState = Depends(get_app_state),
) -> RetrievalTrace:
    path = getattr(app, "execution_trace_path", None)
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail=f"Retrieval trace not found: {execution_id}")
    for line in reversed(Path(path).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("execution_id") == execution_id:
            return RetrievalTrace.model_validate(row.get("retrieval_trace", {}))
    raise HTTPException(status_code=404, detail=f"Retrieval trace not found: {execution_id}")


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
        or getattr(implementation, "prompt_template", None)
        or getattr(implementation, "sub_skill_ids", None)
    )


@router.get("/history", response_model=List[ExecutionHistoryItem])
async def get_execution_history(app: AppState = Depends(get_app_state)) -> List[ExecutionHistoryItem]:
    return list(reversed(_history_rows(app)[-20:]))


@router.get("/history/{execution_id}/experience", response_model=ExecutionExperienceUnit)
async def get_execution_experience(execution_id: str, app: AppState = Depends(get_app_state)) -> ExecutionExperienceUnit:
    for item in reversed(_history_rows(app)):
        if item.get("execution_id") != execution_id:
            continue
        experience_unit = item.get("experience_unit")
        if isinstance(experience_unit, ExecutionExperienceUnit):
            return experience_unit
        if isinstance(experience_unit, dict):
            return ExecutionExperienceUnit.model_validate(experience_unit)
    raise HTTPException(status_code=404, detail=f"Execution experience not found: {execution_id}")


@router.get("/history/{execution_id}", response_model=dict)
async def get_execution_receipt(execution_id: str, app: AppState = Depends(get_app_state)) -> dict:
    for item in reversed(_history_rows(app)):
        if item.get("execution_id") == execution_id:
            return item
    raise HTTPException(status_code=404, detail=f"Execution receipt not found: {execution_id}")


def _history_rows(app: Any) -> List[Dict[str, Any]]:
    directory = history_directory(app)
    if directory is None:
        return list(_execution_history)
    try:
        return load_execution_history(directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Execution history evidence unavailable or corrupt") from exc


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

    context = getattr(getattr(skill, "provenance", None), "creation_context", {}) or {}
    if context.get("demo_components"):
        from ...layers.skill_management.dialogue_demo import verify_graph
        passed, reason = verify_graph(str((step.input_mapping or {}).get("dialogue", "")), payload["output"])
        verification.passed = bool(verification.passed and passed)
        verification.score = min(verification.score, 1.0 if passed else 0.0)
        verification.details["dialogue_instruction_replay"] = {"passed": passed, "reason": reason,
            "scope": "local controlled instruction Demo, not official SkillsBench"}
        if not passed:
            verification.issues.append(reason)

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
    app: Any = None,
    execution_id: str,
    goal: str,
    status: str,
    steps: List[ExecutionStepResult],
    success_count: int,
    total_latency_ms: float,
    retrieved_skill_count: int,
    experience_unit: ExecutionExperienceUnit,
) -> None:
    raw = json.loads(experience_unit.raw_content)
    row = {
        "execution_id": execution_id,
        "goal": goal,
        "status": status,
        "step_count": len(steps),
        "success_count": success_count,
        "total_latency_ms": total_latency_ms,
        "retrieved_skill_count": retrieved_skill_count,
        "created_at": datetime.utcnow().isoformat(),
        "experience_unit_id": experience_unit.unit_id,
        "experience_source_type": experience_unit.source_type,
        "experience_unit": experience_unit.model_dump(mode="json"),
        "steps": [step.model_dump(mode="json") for step in steps],
        "final_state": raw.get("final_state", {}),
        "verifier_passed": raw.get("verifier_passed"),
        "verifier_summary": raw.get("verifier_summary"),
    }
    directory = history_directory(app)
    if directory is not None:
        try:
            save_execution_history(directory, row)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=503, detail="Execution completed but its receipt could not be persisted; do not retry execution") from exc
    else:
        existing = next((item for item in _execution_history if item["execution_id"] == execution_id), None)
        if existing is None:
            _execution_history.append(row)
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
    retrieved_skill_versions: Optional[Dict[str, str]] = None,
    provenance_refs: Optional[List[str]] = None,
    verifier_passed: Optional[bool],
    verifier_summary: Optional[Dict[str, Any]],
    total_latency_ms: float,
    governance: Optional[Dict[str, Any]] = None,
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
            "retrieved_skill_versions": retrieved_skill_versions or {},
            "provenance_refs": provenance_refs or [],
            "total_latency_ms": total_latency_ms,
            "verifier_passed": verifier_passed,
            "step_count": len(steps),
            "governance": governance or {},
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
