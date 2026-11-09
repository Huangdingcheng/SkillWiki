"""Execution harness verification routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...layers.skill_runtime.harness import HarnessEvidenceStore, VerificationLoop
from ...layers.skill_runtime.harness.benchflow_registry import (
    get_historical_benchflow_run,
    list_historical_benchflow_runs,
)
from ...layers.skill_runtime.harness.benchflow_current_registry import (
    formal_benchflow_matrix,
    get_current_benchflow_run,
    list_current_benchflow_runs,
)
from ..deps import AppState, get_app_state
from ..schemas import (
    HarnessLoopListResponse,
    HistoricalBenchFlowRun,
    HistoricalBenchFlowRunListResponse,
    CurrentBenchFlowRun,
    CurrentBenchFlowRunListResponse,
    HarnessVerifyLoopRequest,
    HarnessVerifyLoopResponse,
)

router = APIRouter(prefix="/harness", tags=["harness"])


@router.post("/{skill_id}/verify-loop", response_model=HarnessVerifyLoopResponse)
async def run_verify_loop(
    skill_id: str,
    req: HarnessVerifyLoopRequest,
    app: AppState = Depends(get_app_state),
) -> HarnessVerifyLoopResponse:
    """Run Draft -> harness -> verifier -> repair/retry -> S3 gate."""

    # External Codex execution has its own provider and billing path. When the
    # M1 budget guard is enabled, fail before dispatch so every paid route stays
    # inside the single DeepSeek ledger.
    if req.harness.value == "codex_cli":
        llm = getattr(app, "llm", None)
        if llm is not None and getattr(llm, "_budget", None) is not None:
            raise HTTPException(
                status_code=403,
                detail="codex_cli harness is disabled while the M1 budget guard is enabled",
            )

    loop = VerificationLoop(
        wiki=app.wiki,
        graph=getattr(app, "graph", None),
        executor=getattr(app, "executor", None),
        repair=getattr(app, "repair", None),
    )
    try:
        result = await loop.run(
            skill_id,
            harness_kind=req.harness,
            max_attempts=req.max_attempts,
            promote_on_pass=req.promote_on_pass,
            test_cases=req.test_cases or None,
            allow_repair=req.allow_repair,
            timeout_s=req.timeout_s,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return HarnessVerifyLoopResponse(
        loop_id=result.loop_id,
        skill_id=result.skill_id,
        status=result.status,
        promotion_allowed=result.promotion_allowed,
        attempt_count=len(result.attempts),
        score=result.score,
        attempts=result.attempts,
        repairs=result.repairs,
        initial_version=result.initial_version,
        final_version=result.final_version,
        final_state=result.final_state,
        evidence_path=result.evidence_path,
    )


@router.get("", response_model=HarnessLoopListResponse)
async def list_verify_loops(
    limit: int = Query(default=20, ge=1, le=100),
) -> HarnessLoopListResponse:
    loops = HarnessEvidenceStore().list_recent(limit=limit)
    return HarnessLoopListResponse(loops=loops, total=len(loops))


@router.get("/benchflow/runs", response_model=HistoricalBenchFlowRunListResponse)
async def list_historical_benchflow() -> HistoricalBenchFlowRunListResponse:
    runs = list_historical_benchflow_runs()
    return HistoricalBenchFlowRunListResponse(runs=runs, total=len(runs))


@router.get("/benchflow/current-runs", response_model=CurrentBenchFlowRunListResponse)
async def list_current_benchflow() -> CurrentBenchFlowRunListResponse:
    runs = list_current_benchflow_runs()
    return CurrentBenchFlowRunListResponse(
        runs=[CurrentBenchFlowRun.model_validate(run) for run in runs],
        total=len(runs),
        formal_matrix=formal_benchflow_matrix(runs),
    )


@router.get("/benchflow/current-runs/{run_id:path}", response_model=CurrentBenchFlowRun)
async def get_current_benchflow(run_id: str) -> CurrentBenchFlowRun:
    result = get_current_benchflow_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Current BenchFlow run {run_id} not found")
    return CurrentBenchFlowRun.model_validate(result)


@router.get("/benchflow/runs/{run_id:path}", response_model=HistoricalBenchFlowRun)
async def get_historical_benchflow(run_id: str) -> HistoricalBenchFlowRun:
    result = get_historical_benchflow_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"BenchFlow historical run {run_id} not found")
    return HistoricalBenchFlowRun.model_validate(result)


@router.get("/{loop_id}", response_model=dict)
async def get_verify_loop(loop_id: str) -> dict:
    result = HarnessEvidenceStore().get(loop_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Harness loop {loop_id} not found")
    return result
