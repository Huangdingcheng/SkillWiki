"""Skill 生命周期管理路由。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...layers.skill_governance import (
    GitVersionStore,
    GitVersionStoreError,
    diff_skill_snapshots,
    has_breaking_changes,
    read_skill_snapshot_at_ref,
    release_skill_snapshot,
    restore_skill_snapshot,
    skill_snapshot_path,
    skill_to_snapshot,
    write_skill_snapshot,
)
from ..deps import AppState, get_app_state
from ..schemas import (
    DeprecateRequest,
    NewVersionRequest,
    OKResponse,
    ReleaseRequest,
    ReleaseTagRequest,
    RollbackRequest,
    SkillSummary,
    SnapshotCommitRequest,
    SnapshotCommitResponse,
    SnapshotDiffResponse,
    SnapshotHistoryResponse,
    TransitionRequest,
)

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


def _to_summary(skill):
    from .skills import _to_summary as ts
    return ts(skill)


@router.post("/{skill_id}/transition", response_model=SkillSummary)
async def transition_state(
    skill_id: str,
    req: TransitionRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    try:
        skill = await app.wiki.transition_state(skill_id, req.new_state, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(skill)


@router.post("/{skill_id}/release", response_model=SkillSummary)
async def release_skill(
    skill_id: str,
    req: ReleaseRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    """发布 Skill。若当前为 Draft，自动推进到 Verified 再发布。"""
    from ...models.skill_model import SkillState
    try:
        skill = await app.wiki.get(skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
        if skill.state == SkillState.DRAFT:
            await app.wiki.transition_state(skill_id, SkillState.VERIFIED)
        skill = await app.wiki.release(skill_id)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(skill)


@router.post("/{skill_id}/deprecate", response_model=SkillSummary)
async def deprecate_skill(
    skill_id: str,
    req: DeprecateRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    try:
        skill = await app.wiki.deprecate(skill_id, req.reason, req.replacement_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(skill)


@router.post("/{skill_id}/new-version", response_model=SkillSummary)
async def create_new_version(
    skill_id: str,
    req: NewVersionRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    overrides = {}
    if req.description:
        overrides["description"] = req.description
    skill = await app.wiki.create_new_version(skill_id, req.bump, **overrides)
    return _to_summary(skill)


@router.post("/{skill_id}/review", response_model=dict)
async def review_skill(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> dict:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    result = await app.reviewer.review(skill)
    return {
        "review_id": result.review_id,
        "status": result.status.value,
        "overall_score": result.overall_score,
        "summary": result.summary,
        "comments": [
            {"field": c.field, "severity": c.severity, "message": c.message, "score": c.score}
            for c in result.comments
        ],
        "is_approved": result.is_approved,
    }


@router.post("/{skill_id}/review-and-release", response_model=SkillSummary)
async def review_and_release(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    try:
        released = await app.reviewer.review_and_release(skill, app.wiki)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(released)


@router.post("/{skill_id}/record-execution", response_model=OKResponse)
async def record_execution(
    skill_id: str,
    success: bool,
    latency_ms: float,
    app: AppState = Depends(get_app_state),
) -> OKResponse:
    await app.wiki.record_execution(skill_id, success, latency_ms)
    return OKResponse(message="执行记录已更新")


@router.get("/{skill_id}/diff", response_model=Dict[str, Any])
async def get_skill_diff(
    skill_id: str,
    compare_to: Optional[str] = None,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    """获取 Skill 的变更历史和 diff 信息。

    compare_to: 可选的另一个 skill_id（同名旧版本），不传则返回变更历史。
    """
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    history = app.version_ctrl.get_history(skill_id) if app.version_ctrl else []

    if compare_to:
        other = await app.wiki.get(compare_to)
        if not other:
            raise HTTPException(status_code=404, detail=f"对比 Skill {compare_to} 不存在")
        diff = app.version_ctrl.compute_diff(other, skill) if app.version_ctrl else {}
        return {
            "skill_id": skill_id,
            "compare_to": compare_to,
            "diff": _format_diff(diff),
            "suggested_bump": app.version_ctrl.suggest_version_bump(diff) if app.version_ctrl else "patch",
        }

    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "current_version": skill.version,
        "history": [
            {
                "record_id": r.record_id,
                "from_version": r.from_version,
                "to_version": r.to_version,
                "change_type": r.change_type.value,
                "summary": r.summary,
                "author": r.author,
                "created_at": r.created_at.isoformat(),
                "diff": _format_diff(r.diff),
                "is_breaking": r.is_breaking(),
            }
            for r in history
        ],
    }


@router.get("/{skill_id}/diff/versions", response_model=Dict[str, Any])
async def diff_two_versions(
    skill_id: str,
    version_a: str,
    version_b: str,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    """对比同一 Skill 的两个版本（通过版本历史中的快照）。"""
    history = app.version_ctrl.get_history(skill_id) if app.version_ctrl else []
    records_a = [r for r in history if r.to_version == version_a or r.from_version == version_a]
    records_b = [r for r in history if r.to_version == version_b or r.from_version == version_b]

    return {
        "skill_id": skill_id,
        "version_a": version_a,
        "version_b": version_b,
        "changes_in_a": [r.summary for r in records_a],
        "changes_in_b": [r.summary for r in records_b],
        "history_count": len(history),
    }


@router.post("/{skill_id}/snapshot", response_model=SnapshotCommitResponse)
async def commit_skill_snapshot_endpoint(
    skill_id: str,
    req: SnapshotCommitRequest,
    app: AppState = Depends(get_app_state),
) -> SnapshotCommitResponse:
    skill = await _get_skill_or_404(skill_id, app)
    repo_path = _governance_repo_path()
    store = GitVersionStore(repo_path)
    snapshot_path = write_skill_snapshot(repo_path, skill)
    message = req.message or f"skill({skill.name}): snapshot v{skill.version}"
    try:
        commit = store.commit_paths([snapshot_path], message, author_name=req.author)
    except (GitVersionStoreError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SnapshotCommitResponse(
        skill_id=skill.skill_id,
        skill_name=skill.name,
        version=skill.version,
        snapshot_path=snapshot_path,
        commit=commit,
        message=message,
    )


@router.get("/{skill_id}/snapshot/history", response_model=SnapshotHistoryResponse)
async def get_skill_snapshot_history(
    skill_id: str,
    max_count: int = 20,
    app: AppState = Depends(get_app_state),
) -> SnapshotHistoryResponse:
    skill = await _get_skill_or_404(skill_id, app)
    repo_path = _governance_repo_path()
    store = GitVersionStore(repo_path)
    snapshot_path = skill_snapshot_path(skill)
    try:
        history = store.commit_history(snapshot_path, max_count=max_count)
    except (GitVersionStoreError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SnapshotHistoryResponse(
        skill_id=skill.skill_id,
        snapshot_path=snapshot_path,
        history=[
            {
                "commit_hash": item.commit_hash,
                "author": item.author,
                "authored_at": item.authored_at,
                "subject": item.subject,
                "changed_paths": list(item.changed_paths),
            }
            for item in history
        ],
    )


@router.get("/{skill_id}/snapshot/diff", response_model=SnapshotDiffResponse)
async def get_skill_snapshot_diff(
    skill_id: str,
    from_ref: str,
    to_ref: str = "HEAD",
    app: AppState = Depends(get_app_state),
) -> SnapshotDiffResponse:
    skill = await _get_skill_or_404(skill_id, app)
    repo_path = _governance_repo_path()
    store = GitVersionStore(repo_path)
    snapshot_path = skill_snapshot_path(skill)
    try:
        raw_diff = store.diff_between(from_ref, to_ref, snapshot_path)
        old_snapshot = read_skill_snapshot_at_ref(repo_path, from_ref, snapshot_path, store)
        try:
            new_snapshot = read_skill_snapshot_at_ref(repo_path, to_ref, snapshot_path, store)
        except GitVersionStoreError:
            new_snapshot = skill_to_snapshot(skill)
        diffs = diff_skill_snapshots(old_snapshot, new_snapshot)
    except (GitVersionStoreError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SnapshotDiffResponse(
        skill_id=skill.skill_id,
        snapshot_path=snapshot_path,
        from_ref=from_ref,
        to_ref=to_ref,
        raw_diff=raw_diff,
        diffs=[item.to_dict() for item in diffs],
        has_breaking_changes=has_breaking_changes(diffs),
    )


@router.post("/{skill_id}/release-tag", response_model=Dict[str, Any])
async def create_skill_release_tag(
    skill_id: str,
    req: ReleaseTagRequest,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    skill = await _get_skill_or_404(skill_id, app)
    repo_path = _governance_repo_path()
    try:
        return release_skill_snapshot(repo_path, skill, ref=req.ref).to_dict()
    except (GitVersionStoreError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{skill_id}/rollback", response_model=Dict[str, Any])
async def rollback_skill_snapshot(
    skill_id: str,
    req: RollbackRequest,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    skill = await _get_skill_or_404(skill_id, app)
    repo_path = _governance_repo_path()
    try:
        return restore_skill_snapshot(repo_path, skill, req.source_ref).to_dict()
    except (GitVersionStoreError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _format_diff(diff: Dict[str, Any]) -> List[Dict[str, Any]]:
    """将 diff 字典格式化为前端友好的行列表。"""
    lines = []
    for field, change in diff.items():
        if isinstance(change, dict) and "old" in change and "new" in change:
            old_str = str(change["old"])
            new_str = str(change["new"])
            lines.append({
                "field": field,
                "type": "modified",
                "old_value": old_str,
                "new_value": new_str,
                "old_lines": old_str.splitlines() or [old_str],
                "new_lines": new_str.splitlines() or [new_str],
            })
        else:
            lines.append({
                "field": field,
                "type": "added",
                "old_value": "",
                "new_value": str(change),
                "old_lines": [],
                "new_lines": str(change).splitlines() or [str(change)],
            })
    return lines


async def _get_skill_or_404(skill_id: str, app: AppState):
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    return skill


def _governance_repo_path() -> Path:
    configured = os.environ.get("SKILLOS_GOVERNANCE_REPO")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[4]
