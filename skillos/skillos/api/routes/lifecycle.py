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
    propose_skill_change,
    read_skill_snapshot_at_ref,
    release_skill_snapshot,
    review_recommendation_for_diffs,
    restore_skill_snapshot,
    skill_snapshot_path,
    skill_to_snapshot,
    write_skill_snapshot,
)
from ...layers.skill_management import SkillAuditorAgent
from ..deps import AppState, get_app_state
from ..schemas import (
    DeprecateRequest,
    MaintenanceReviewRequest,
    MaintenanceReviewResponse,
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
from ...models.skill_model import Skill, SkillState
from ...models.skill_model import EdgeType

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])
_AUDITED_TARGET_STATES = {SkillState.VERIFIED, SkillState.RELEASED, SkillState.DEGRADED}


def _to_summary(skill):
    from .skills import _to_summary as ts
    return ts(skill)


def _audit_skill_for_target_state(app: AppState, skill: Skill, target_state: SkillState) -> None:
    if target_state not in _AUDITED_TARGET_STATES:
        return

    candidate = skill.model_copy(deep=True)
    object.__setattr__(candidate, "state", target_state)
    auditor = getattr(app, "auditor", None) or SkillAuditorAgent(getattr(app, "llm", None))
    result = auditor.audit(candidate)
    if result.passed:
        return

    raise HTTPException(
        status_code=400,
        detail={
            "message": "Skill failed release audit",
            "issues": result.issues,
            "warnings": result.warnings,
            "recommendations": result.recommendations,
        },
    )


async def _record_replacement_edge(
    app: AppState,
    skill: Skill,
    replacement_id: Optional[str],
    reason: str,
) -> None:
    if not replacement_id:
        return
    graph = getattr(app, "graph", None)
    if graph is None or not hasattr(graph, "add_replacement"):
        return
    try:
        if hasattr(graph, "sync_skill"):
            await graph.sync_skill(skill)
            replacement = await app.wiki.get(replacement_id)
            if replacement:
                await graph.sync_skill(replacement)
        await graph.add_replacement(replacement_id, skill.skill_id, reason=reason)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph replacement edge sync failed: {exc}") from exc


@router.post("/{skill_id}/transition", response_model=SkillSummary)
async def transition_state(
    skill_id: str,
    req: TransitionRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    try:
        skill = await app.wiki.get(skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
        _audit_skill_for_target_state(app, skill, req.new_state)
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
    try:
        skill = await app.wiki.get(skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
        _audit_skill_for_target_state(app, skill, SkillState.RELEASED)
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
        await _record_replacement_edge(app, skill, req.replacement_id, req.reason)
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
    if req.tags is not None:
        overrides["tags"] = req.tags
    if req.interface is not None:
        overrides["interface"] = req.interface
    if req.implementation is not None:
        overrides["implementation"] = req.implementation
    if req.evaluation is not None:
        evaluation = req.evaluation.model_copy(deep=True)
        evaluation.harness_validation = {}
        overrides["evaluation"] = evaluation
    if req.test_cases is not None:
        overrides["test_cases"] = req.test_cases
    if req.metadata is not None:
        overrides["metadata"] = req.metadata
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
        if other.name == skill.name and hasattr(app.wiki, "diff_versions"):
            try:
                raw_diff = await app.wiki.diff_versions(skill.name, other.version, skill.version)
                business_diff = _business_skill_diff(other, skill)
                business_summary = _summarize_business_diff(business_diff)
                return {
                    "skill_id": skill_id,
                    "compare_to": compare_to,
                    "diff": _format_unified_diff(raw_diff),
                    "raw_diff": raw_diff,
                    "business_diff": business_diff,
                    "business_summary": business_summary,
                    "breaking": business_summary["breaking"],
                    "suggested_bump": business_summary["suggested_bump"],
                    "source": "git",
                }
            except Exception:
                pass

        diff = app.version_ctrl.compute_diff(other, skill) if app.version_ctrl else {}
        business_diff = _business_skill_diff(other, skill)
        business_summary = _summarize_business_diff(business_diff)
        return {
            "skill_id": skill_id,
            "compare_to": compare_to,
            "diff": _format_diff(diff),
            "business_diff": business_diff,
            "business_summary": business_summary,
            "breaking": business_summary["breaking"],
            "suggested_bump": (
                app.version_ctrl.suggest_version_bump(diff)
                if app.version_ctrl and not business_diff
                else business_summary["suggested_bump"]
            ),
            "source": "version_controller",
        }

    if hasattr(app.wiki, "get_version_history"):
        try:
            repo_history = await app.wiki.get_version_history(skill.name)
            return {
                "skill_id": skill_id,
                "skill_name": skill.name,
                "current_version": skill.version,
                "source": "git",
                "history": [
                    {
                        "record_id": f"{item.name}:{item.version}",
                        "from_version": None,
                        "to_version": item.version,
                        "change_type": "version",
                        "summary": item.description,
                        "author": (
                            item.provenance.created_by_agent
                            if item.provenance and item.provenance.created_by_agent
                            else ""
                        ),
                        "created_at": item.created_at.isoformat(),
                        "diff": [],
                        "is_breaking": False,
                        "skill_id": item.skill_id,
                        "state": item.state.value,
                    }
                    for item in repo_history
                ],
            }
        except Exception:
            pass

    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "current_version": skill.version,
        "source": "version_controller",
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
    message = req.message or f"skill({skill.name}): snapshot v{skill.version}"
    try:
        with store.lock():
            snapshot_path = write_skill_snapshot(repo_path, skill)
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


@router.get("/repository/status", response_model=Dict[str, Any])
async def get_governance_repository_status() -> Dict[str, Any]:
    repo_path = _governance_repo_path()
    store = GitVersionStore(repo_path)
    try:
        return store.repository_status()
    except GitVersionStoreError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{skill_id}/snapshot/diff", response_model=SnapshotDiffResponse)
async def get_skill_snapshot_diff(
    skill_id: str,
    from_ref: str,
    to_ref: str = "HEAD",
    from_version: Optional[str] = None,
    to_version: Optional[str] = None,
    app: AppState = Depends(get_app_state),
) -> SnapshotDiffResponse:
    skill = await _get_skill_or_404(skill_id, app)
    repo_path = _governance_repo_path()
    store = GitVersionStore(repo_path)
    from_snapshot_path = _skill_snapshot_path_for_version(skill, from_version)
    to_snapshot_path = _skill_snapshot_path_for_version(skill, to_version)
    try:
        if from_snapshot_path == to_snapshot_path:
            raw_diff = store.diff_between(from_ref, to_ref, to_snapshot_path)
        else:
            raw_diff = store.diff_between_paths(
                from_ref,
                to_ref,
                [from_snapshot_path, to_snapshot_path],
            )
        old_snapshot = read_skill_snapshot_at_ref(repo_path, from_ref, from_snapshot_path, store)
        try:
            new_snapshot = read_skill_snapshot_at_ref(repo_path, to_ref, to_snapshot_path, store)
        except GitVersionStoreError:
            if to_snapshot_path == skill_snapshot_path(skill):
                new_snapshot = skill_to_snapshot(skill)
            else:
                raise
        diffs = diff_skill_snapshots(old_snapshot, new_snapshot)
    except (GitVersionStoreError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    breaking = has_breaking_changes(diffs)
    return SnapshotDiffResponse(
        skill_id=skill.skill_id,
        snapshot_path=to_snapshot_path,
        from_snapshot_path=from_snapshot_path,
        to_snapshot_path=to_snapshot_path,
        from_ref=from_ref,
        to_ref=to_ref,
        raw_diff=raw_diff,
        diffs=[item.to_dict() for item in diffs],
        has_breaking_changes=breaking,
        review_recommendation=review_recommendation_for_diffs(diffs),
        impacted_skills=await _build_version_impact_list(skill.skill_id, app) if breaking else [],
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


@router.post("/{skill_id}/propose-maintenance-change", response_model=MaintenanceReviewResponse)
async def propose_maintenance_change(
    skill_id: str,
    req: MaintenanceReviewRequest,
    app: AppState = Depends(get_app_state),
) -> MaintenanceReviewResponse:
    skill = await _get_skill_or_404(skill_id, app)
    try:
        patched_skill = _build_patched_skill(skill, req.patched_skill)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    repo_path = _governance_repo_path()
    store = GitVersionStore(repo_path)
    try:
        bundle = propose_skill_change(
            repo_path,
            skill,
            patched_skill,
            store,
            author_name=req.author,
        )
    except (GitVersionStoreError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return MaintenanceReviewResponse(
        skill_id=skill.skill_id,
        proposal_id=req.proposal_id,
        branch_name=bundle.branch_name,
        base_commit=bundle.base_commit,
        head_commit=bundle.head_commit,
        snapshot_path=bundle.snapshot_path,
        structured_diff=[diff.to_dict() for diff in bundle.diffs],
        has_breaking_changes=bundle.has_breaking_changes,
        review_status=bundle.suggested_review_status,
        impacted_skills=await _build_version_impact_list(skill.skill_id, app) if bundle.has_breaking_changes else [],
        reason=req.reason,
        author=req.author,
    )


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


def _format_unified_diff(raw_diff: str) -> List[Dict[str, Any]]:
    """Format a unified diff string for the existing frontend diff viewer."""
    if not raw_diff:
        return []

    rows: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {
        "field": "skill_json",
        "type": "modified",
        "old_value": "",
        "new_value": "",
        "old_lines": [],
        "new_lines": [],
    }
    for line in raw_diff.splitlines():
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            continue
        if line.startswith("-"):
            current["old_lines"].append(line[1:])
        elif line.startswith("+"):
            current["new_lines"].append(line[1:])
    current["old_value"] = "\n".join(current["old_lines"])
    current["new_value"] = "\n".join(current["new_lines"])
    rows.append(current)
    return rows


def _business_skill_diff(old_skill: Skill, new_skill: Skill) -> List[Dict[str, Any]]:
    old_data = old_skill.model_dump(mode="json")
    new_data = new_skill.model_dump(mode="json")
    rows: List[Dict[str, Any]] = []
    for field in (
        "description",
        "tags",
        "skill_type",
        "domain",
        "granularity_level",
        "interface.input_schema",
        "interface.input_schema.required",
        "interface.output_schema",
        "interface.preconditions",
        "interface.postconditions",
        "implementation.language",
        "implementation.code",
        "implementation.prompt_template",
        "implementation.tool_calls",
        "implementation.sub_skill_ids",
        "evaluation.verifier_specs",
        "evaluation.benchmark_task_ids",
    ):
        old_value = _get_nested_value(old_data, field)
        new_value = _get_nested_value(new_data, field)
        if old_value == new_value:
            continue
        row = {
            "field": field,
            "change_type": _business_change_type(old_value, new_value),
            "category": _business_change_category(field),
            "old_value": old_value,
            "new_value": new_value,
            "is_breaking": _business_change_is_breaking(field, old_value, new_value),
        }
        rows.append(row)
    return rows


def _summarize_business_diff(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    breaking = any(row.get("is_breaking") for row in rows)
    categories = sorted({str(row.get("category", "general")) for row in rows})
    suggested_bump = "major" if breaking else ("minor" if any(
        row.get("category") in {"interface", "implementation"} for row in rows
    ) else "patch")
    return {
        "changed_fields": len(rows),
        "breaking": breaking,
        "categories": categories,
        "suggested_bump": suggested_bump,
        "summary": _business_summary_text(rows, breaking, suggested_bump),
    }


def _business_summary_text(rows: List[Dict[str, Any]], breaking: bool, suggested_bump: str) -> str:
    if not rows:
        return "No business-level Skill changes detected."
    fields = ", ".join(str(row["field"]) for row in rows[:4])
    suffix = "" if len(rows) <= 4 else f", and {len(rows) - 4} more"
    risk = "breaking" if breaking else "non-breaking"
    return f"{len(rows)} {risk} field changes: {fields}{suffix}. Suggested bump: {suggested_bump}."


def _business_change_type(old_value: Any, new_value: Any) -> str:
    if old_value is None and new_value is not None:
        return "added"
    if old_value is not None and new_value is None:
        return "removed"
    return "modified"


def _business_change_category(field: str) -> str:
    if field.startswith("interface."):
        return "interface"
    if field.startswith("implementation."):
        return "implementation"
    if field.startswith("evaluation."):
        return "evaluation"
    return "metadata"


def _business_change_is_breaking(field: str, old_value: Any, new_value: Any) -> bool:
    if field == "interface.input_schema.required":
        old_required = set(old_value or [])
        new_required = set(new_value or [])
        return not old_required.issuperset(new_required)
    if field == "interface.input_schema":
        return _schema_required_added(old_value, new_value)
    return False


def _schema_required_added(old_value: Any, new_value: Any) -> bool:
    if not isinstance(old_value, dict) or not isinstance(new_value, dict):
        return False
    old_required = set(old_value.get("required") or [])
    new_required = set(new_value.get("required") or [])
    return not old_required.issuperset(new_required)


def _get_nested_value(data: Dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


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


async def _build_version_impact_list(skill_id: str, app: AppState) -> List[Dict[str, Any]]:
    graph = getattr(app, "graph", None)
    if graph is None:
        return []

    impact_edges = await _incoming_dependency_edges(graph, skill_id)
    impacts: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for edge in impact_edges:
        impacted_id = getattr(edge, "source_id", "")
        if not impacted_id or impacted_id in seen:
            continue
        seen.add(impacted_id)
        impacted_skill = await app.wiki.get(impacted_id)
        impacts.append({
            "skill_id": impacted_id,
            "skill_name": impacted_skill.name if impacted_skill else "",
            "skill_type": impacted_skill.skill_type.value if impacted_skill else "",
            "state": impacted_skill.state.value if impacted_skill else "",
            "via_edge_type": edge.edge_type.value,
            "changed_skill_id": skill_id,
            "method": "hin_meta_path_projection",
            "paper_basis": ["HIN Survey meta-path projection", "SkillX layered skill dependency"],
        })
    return impacts


async def _incoming_dependency_edges(graph: Any, skill_id: str) -> List[Any]:
    edge_types = {EdgeType.DEPENDS_ON, EdgeType.COMPOSES_WITH}
    edges: List[Any] = []

    getter = getattr(graph, "get_edges", None)
    if callable(getter):
        for edge_type in edge_types:
            try:
                edges.extend(await getter(skill_id, direction="in", edge_type=edge_type))
            except TypeError:
                break

    if not edges and hasattr(graph, "_edges"):
        edges = [
            edge
            for edge in getattr(graph, "_edges", [])
            if edge.target_id == skill_id and edge.edge_type in edge_types
        ]
    return edges


def _skill_snapshot_path_for_version(skill, version: Optional[str]) -> str:
    if not version:
        return skill_snapshot_path(skill)
    return f"skills/{skill.skill_id}/{version}.json"


def _build_patched_skill(skill: Skill, patch: Dict[str, Any]) -> Skill:
    if not isinstance(patch, dict):
        raise ValueError("patched_skill must be an object")
    data = skill.model_dump(mode="json")
    _deep_update(data, patch)
    if data.get("skill_id") != skill.skill_id:
        raise ValueError("patched_skill.skill_id must match the path skill_id")
    return Skill.model_validate(data)


def _deep_update(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for key, value in patch.items():
        if (
            isinstance(value, dict)
            and isinstance(target.get(key), dict)
        ):
            _deep_update(target[key], value)
        else:
            target[key] = value
