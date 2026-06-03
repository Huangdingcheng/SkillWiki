"""Execution harness verification routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...layers.skill_runtime.harness import HarnessEvidenceStore, VerificationLoop
from ..deps import AppState, get_app_state
from ..schemas import (
    HarnessLoopListResponse,
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


@router.get("/{loop_id}", response_model=dict)
async def get_verify_loop(loop_id: str) -> dict:
    result = HarnessEvidenceStore().get(loop_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Harness loop {loop_id} not found")
    return result
