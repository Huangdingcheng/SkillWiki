"""Health monitoring and evolution routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException

from ...layers.feedback_evolution import (
    EvolutionReport,
    HealthStatus,
    SkillHealthReport,
    SystemHealthReport,
)
from ...models.maintenance_model import (
    MaintenanceProposal,
    MaintenanceProposalStatus,
    MaintenanceRecommendedAction,
    MaintenanceTrigger,
    ReflectionMemoryEntry,
    ReflectionMemoryStatus,
)
from ...utils.logger import get_logger
from ..deps import AppState, get_app_state
from ..schemas import (
    EvolutionCycleResponse,
    HealthReportResponse,
    MaintenanceProposalListResponse,
    MaintenanceProposalNextAction,
    MaintenanceProposalResponse,
    ReflectionMemoryRequest,
    ReflectionMemoryResponse,
    SystemHealthResponse,
)
from .ws import broadcast

router = APIRouter(prefix="/evolution", tags=["evolution"])
logger = get_logger(__name__)
_HEALTH_EVENT_COOLDOWN_SECONDS = 30
_REFLECTION_PROPOSAL_THRESHOLD = 3
_last_health_event_at: Dict[Tuple[str, str], datetime] = {}
_proposal_queue: Dict[str, MaintenanceProposal] = {}
_reflection_memory: Dict[str, ReflectionMemoryEntry] = {}
_proposal_store_path: Optional[Path] = None
_reflection_store_path: Optional[Path] = None


def configure_persistent_stores(base_dir: Optional[Path]) -> None:
    """Configure local JSON persistence for D proposal and reflection queues."""
    global _proposal_store_path, _reflection_store_path
    if base_dir is None:
        _proposal_store_path = None
        _reflection_store_path = None
        _proposal_queue.clear()
        _reflection_memory.clear()
        return

    store_dir = Path(base_dir).resolve() / "metadata" / "maintenance"
    _proposal_store_path = store_dir / "proposal_queue.json"
    _reflection_store_path = store_dir / "reflection_memory.json"
    _load_persistent_stores()


def _load_persistent_stores() -> None:
    _proposal_queue.clear()
    _proposal_queue.update(_load_model_map(_proposal_store_path, MaintenanceProposal, "proposal_id"))
    _reflection_memory.clear()
    _reflection_memory.update(_load_model_map(_reflection_store_path, ReflectionMemoryEntry, "memory_id"))


def _load_model_map(path: Optional[Path], model_cls: Any, key_field: str) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load maintenance store %s: %s", path, exc)
        return {}

    items = raw.get("items", raw if isinstance(raw, list) else [])
    loaded: Dict[str, Any] = {}
    for item in items if isinstance(items, list) else []:
        try:
            model = model_cls.model_validate(item)
        except Exception as exc:
            logger.warning("Skip invalid maintenance store item in %s: %s", path, exc)
            continue
        loaded[getattr(model, key_field)] = model
    return loaded


def _persist_proposal_queue() -> None:
    _persist_model_map(_proposal_store_path, list(_proposal_queue.values()))


def _persist_reflection_memory() -> None:
    _persist_model_map(_reflection_store_path, list(_reflection_memory.values()))


def _persist_model_map(path: Optional[Path], items: List[Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "skillos.maintenance_store.v1",
            "updated_at": datetime.now(UTC).isoformat(),
            "items": [item.model_dump(mode="json") for item in items],
        }
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    except Exception as exc:
        logger.warning("Failed to persist maintenance store %s: %s", path, exc)


def _event_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _health_event_name(status: HealthStatus) -> Optional[str]:
    if status == HealthStatus.DEGRADED:
        return "health_degraded"
    if status == HealthStatus.CRITICAL:
        return "health_critical"
    return None


def _health_payload(report: SkillHealthReport) -> Dict[str, Any]:
    return {
        "skill_id": report.skill_id,
        "skill_name": report.skill_name,
        "status": report.status.value,
        "success_rate": report.success_rate,
        "issues": list(report.issues),
        "timestamp": _event_timestamp(),
    }


def _system_health_payload(report: SystemHealthReport, status: HealthStatus) -> Dict[str, Any]:
    affected = [
        {
            "skill_id": item.skill_id,
            "skill_name": item.skill_name,
            "success_rate": item.success_rate,
            "issues": list(item.issues),
        }
        for item in report.skill_reports
        if item.status == status
    ]
    return {
        "skill_id": "system",
        "skill_name": "SkillWiki system",
        "status": status.value,
        "success_rate": report.health_ratio,
        "issues": [f"{len(affected)} {status.value} skill(s) detected"],
        "total_skills": report.total_skills,
        "healthy_count": report.healthy_count,
        "degraded_count": report.degraded_count,
        "critical_count": report.critical_count,
        "affected_skills": affected[:10],
        "timestamp": _event_timestamp(),
    }


def _cycle_payload(report: EvolutionReport) -> Dict[str, Any]:
    return {
        "cycle_id": report.cycle_id,
        "tasks_total": report.tasks_total,
        "tasks_completed": report.tasks_completed,
        "tasks_failed": report.tasks_failed,
        "repaired": len(report.repaired),
        "deprecated": len(report.deprecated),
        "merged": len(report.merged),
        "split": len(report.split),
        "maintenance_proposals": len(report.maintenance_proposals),
        "errors": list(report.errors),
        "timestamp": _event_timestamp(),
    }


def _proposal_identity(proposal: MaintenanceProposal) -> Tuple[str, str, str, str, str]:
    return (
        proposal.skill_id,
        proposal.trigger.value,
        proposal.recommended_action.value,
        proposal.source,
        str(proposal.metadata.get("failure_signature") or ""),
    )


def _store_proposal(proposal: Optional[MaintenanceProposal]) -> Optional[MaintenanceProposal]:
    if proposal is None:
        return None
    existing = _proposal_queue.get(proposal.proposal_id)
    if existing:
        return existing
    identity = _proposal_identity(proposal)
    for queued in _proposal_queue.values():
        if queued.status == MaintenanceProposalStatus.PENDING and _proposal_identity(queued) == identity:
            return queued
    _proposal_queue[proposal.proposal_id] = proposal
    _persist_proposal_queue()
    return proposal


def _store_proposals(proposals: Iterable[MaintenanceProposal]) -> List[MaintenanceProposal]:
    stored: List[MaintenanceProposal] = []
    for proposal in proposals:
        queued = _store_proposal(proposal)
        if queued is not None:
            stored.append(queued)
    return stored


def _list_queued_proposals(
    status: Optional[MaintenanceProposalStatus] = None,
) -> List[MaintenanceProposal]:
    proposals = sorted(
        _proposal_queue.values(),
        key=lambda item: item.created_at,
        reverse=True,
    )
    if status is None:
        return proposals
    return [proposal for proposal in proposals if proposal.status == status]


def _proposal_or_404(proposal_id: str) -> MaintenanceProposal:
    proposal = _proposal_queue.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Maintenance proposal {proposal_id} does not exist")
    return proposal


def _transition_proposal(
    proposal: MaintenanceProposal,
    target_status: MaintenanceProposalStatus,
) -> MaintenanceProposal:
    if proposal.status == target_status:
        return proposal
    if proposal.status != MaintenanceProposalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Maintenance proposal {proposal.proposal_id} is already {proposal.status.value}",
        )
    if target_status == MaintenanceProposalStatus.ACCEPTED:
        proposal.accept()
    elif target_status == MaintenanceProposalStatus.REJECTED:
        proposal.reject()
    _persist_proposal_queue()
    return proposal


def _proposal_next_action(proposal: MaintenanceProposal) -> MaintenanceProposalNextAction:
    return MaintenanceProposalNextAction(
        endpoint=f"/api/v1/lifecycle/{proposal.skill_id}/propose-maintenance-change",
        required_payload_fields=["proposal_id", "patched_skill", "reason", "author"],
        reason=(
            "Proposal accepted. Submit a patched_skill to B governance so SkillWiki can "
            "create a snapshot, structured diff, and review bundle before any live Skill change."
        ),
    )


def _proposal_response(
    proposal: MaintenanceProposal,
    *,
    include_next_action: bool = False,
) -> MaintenanceProposalResponse:
    response = MaintenanceProposalResponse.model_validate(proposal.model_dump())
    if include_next_action:
        response.next_action = _proposal_next_action(proposal)
    return response


def _reflection_identity(memory: ReflectionMemoryEntry) -> Tuple[str, str]:
    return (memory.skill_id, memory.failure_signature)


def _record_reflection_memory(memory: ReflectionMemoryEntry) -> ReflectionMemoryEntry:
    if not memory.failure_signature:
        memory.failure_signature = _derive_failure_signature(memory)
    _reflection_memory[memory.memory_id] = memory
    _persist_reflection_memory()
    return memory


def _derive_failure_signature(memory: ReflectionMemoryEntry) -> str:
    for value in [*memory.evidence, memory.reflection_text, memory.trajectory_summary]:
        text = str(value or "").strip()
        if text:
            return " ".join(text.lower().split())[:160]
    return "unspecified_runtime_failure"


def _matching_open_reflections(memory: ReflectionMemoryEntry) -> List[ReflectionMemoryEntry]:
    identity = _reflection_identity(memory)
    return [
        item for item in _reflection_memory.values()
        if _reflection_identity(item) == identity
        and item.status == ReflectionMemoryStatus.OBSERVED
    ]


def _proposal_for_reflection_cluster(
    memory: ReflectionMemoryEntry,
    cluster: List[ReflectionMemoryEntry],
) -> MaintenanceProposal:
    evidence = []
    for item in cluster:
        evidence.extend(item.evidence or [item.reflection_text])
    evidence = list(dict.fromkeys([item for item in evidence if item]))
    if not evidence:
        evidence = [memory.failure_signature or "Repeated runtime reflection failure."]

    return MaintenanceProposal(
        skill_id=memory.skill_id,
        trigger=MaintenanceTrigger.RUNTIME_FAILURE,
        recommended_action=MaintenanceRecommendedAction.REPAIR,
        evidence=evidence,
        root_cause=memory.failure_signature or evidence[0],
        patch_hint=(
            "Review repeated reflection memories for this failure signature and propose "
            "a targeted Skill repair through B governance."
        ),
        feedback_sources=["runtime_reflection_memory"],
        targets_to_fix=[memory.failure_signature or evidence[0]],
        invariants_to_preserve=[
            "Preserve successful trajectories and the public Skill interface."
        ],
        validation_plan=[
            "Replay at least one stored failed trajectory after the candidate repair.",
            "Confirm the same failure signature no longer appears.",
        ],
        confidence=min(0.95, 0.45 + 0.15 * len(cluster)),
        source="runtime_reflection_memory",
        metadata={
            "paper_method": "SkillClaw recurring failure pattern over Reflexion/ExpeL memory",
            "failure_signature": memory.failure_signature,
            "reflection_memory_ids": [item.memory_id for item in cluster],
            "occurrence_count": len(cluster),
            "threshold": _REFLECTION_PROPOSAL_THRESHOLD,
        },
    )


def _maybe_create_reflection_proposal(memory: ReflectionMemoryEntry) -> Optional[MaintenanceProposal]:
    if memory.success:
        return None
    cluster = _matching_open_reflections(memory)
    if len(cluster) < _REFLECTION_PROPOSAL_THRESHOLD:
        return None

    proposal = _store_proposal(_proposal_for_reflection_cluster(memory, cluster))
    if proposal is None:
        return None
    for item in cluster:
        item.mark_proposed()
    _persist_reflection_memory()
    return proposal


def _health_response(
    report: SkillHealthReport,
    *,
    persist_proposal: bool = False,
) -> HealthReportResponse:
    proposal = None
    if report.needs_attention or report.status == HealthStatus.STALE:
        proposal = MaintenanceProposal.from_health_report(report)
        if persist_proposal:
            proposal = _store_proposal(proposal)
    return HealthReportResponse(
        skill_id=report.skill_id,
        skill_name=report.skill_name,
        status=report.status.value,
        success_rate=report.success_rate,
        usage_count=report.usage_count,
        avg_latency_ms=report.avg_latency_ms,
        issues=report.issues,
        recommendations=report.recommendations,
        maintenance_proposal=proposal,
    )


async def _safe_broadcast(event: str, payload: Dict[str, Any]) -> None:
    try:
        await broadcast(event, payload)
    except Exception as exc:
        logger.warning("Evolution event broadcast failed: %s", exc)


def _should_emit_health_event(event: str, subject_id: str) -> bool:
    now = datetime.now(UTC)
    key = (event, subject_id)
    last_sent = _last_health_event_at.get(key)
    if last_sent and (now - last_sent).total_seconds() < _HEALTH_EVENT_COOLDOWN_SECONDS:
        return False
    _last_health_event_at[key] = now
    return True


async def _emit_health_event(report: SkillHealthReport) -> None:
    event = _health_event_name(report.status)
    if event and _should_emit_health_event(event, report.skill_id):
        await _safe_broadcast(event, _health_payload(report))


async def _emit_system_health_events(report: SystemHealthReport) -> None:
    if report.critical_count > 0 and _should_emit_health_event("health_critical", "system"):
        await _safe_broadcast(
            "health_critical",
            _system_health_payload(report, HealthStatus.CRITICAL),
        )
    if report.degraded_count > 0 and _should_emit_health_event("health_degraded", "system"):
        await _safe_broadcast(
            "health_degraded",
            _system_health_payload(report, HealthStatus.DEGRADED),
        )


@router.get("/health/{skill_id}", response_model=HealthReportResponse)
async def get_skill_health(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> HealthReportResponse:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    report = app.monitor.evaluate_skill(skill)
    await _emit_health_event(report)
    return _health_response(report, persist_proposal=True)


@router.get("/health", response_model=SystemHealthResponse)
async def get_system_health(
    app: AppState = Depends(get_app_state),
) -> SystemHealthResponse:
    from ...models.skill_model import SkillState

    skills = await app.wiki.list(state=None, limit=500)
    active = [s for s in skills if s.state in (SkillState.RELEASED, SkillState.DEGRADED)]
    sys_report = app.monitor.evaluate_batch(active)
    await _emit_system_health_events(sys_report)
    return SystemHealthResponse(
        total_skills=sys_report.total_skills,
        healthy_count=sys_report.healthy_count,
        degraded_count=sys_report.degraded_count,
        critical_count=sys_report.critical_count,
        stale_count=sys_report.stale_count,
        health_ratio=sys_report.health_ratio,
        skill_reports=[_health_response(r, persist_proposal=True) for r in sys_report.skill_reports],
    )


@router.get("/proposals", response_model=MaintenanceProposalListResponse)
async def list_maintenance_proposals(
    status: Optional[MaintenanceProposalStatus] = None,
) -> MaintenanceProposalListResponse:
    """List queued D-side maintenance proposals for human review."""
    return MaintenanceProposalListResponse.from_proposals(_list_queued_proposals(status))


@router.post("/proposals/{proposal_id}/accept", response_model=MaintenanceProposalResponse)
async def accept_maintenance_proposal(proposal_id: str) -> MaintenanceProposalResponse:
    """Mark a queued maintenance proposal as accepted by a human reviewer."""
    proposal = _proposal_or_404(proposal_id)
    accepted = _transition_proposal(proposal, MaintenanceProposalStatus.ACCEPTED)
    return _proposal_response(accepted, include_next_action=True)


@router.post("/proposals/{proposal_id}/reject", response_model=MaintenanceProposalResponse)
async def reject_maintenance_proposal(proposal_id: str) -> MaintenanceProposalResponse:
    """Mark a queued maintenance proposal as rejected by a human reviewer."""
    proposal = _proposal_or_404(proposal_id)
    return _proposal_response(_transition_proposal(proposal, MaintenanceProposalStatus.REJECTED))


@router.post("/reflection-memory", response_model=ReflectionMemoryResponse)
async def record_reflection_memory(req: ReflectionMemoryRequest) -> ReflectionMemoryResponse:
    """Store runtime reflection memory and create a proposal after repeated failures."""
    memory = _record_reflection_memory(ReflectionMemoryEntry(**req.model_dump()))
    proposal = _maybe_create_reflection_proposal(memory)
    occurrence_count = len([
        item for item in _reflection_memory.values()
        if _reflection_identity(item) == _reflection_identity(memory)
    ])
    return ReflectionMemoryResponse(
        memory=memory,
        occurrence_count=occurrence_count,
        threshold=_REFLECTION_PROPOSAL_THRESHOLD,
        proposal=(
            _proposal_response(proposal)
            if proposal is not None
            else None
        ),
    )


@router.post("/repair/{skill_id}", response_model=dict)
async def repair_skill(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> dict:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    health = app.monitor.evaluate_skill(skill)
    result = await app.repair.repair(skill, health)
    return {
        "skill_id": result.skill_id,
        "success": result.success,
        "fix_type": result.fix_type,
        "root_cause": result.root_cause,
        "repair_notes": result.repair_notes,
        "confidence": result.confidence,
        "should_deprecate": result.should_deprecate,
        "repaired_skill_id": result.repaired_skill.skill_id if result.repaired_skill else None,
        "error": result.error,
    }


@router.post("/cycle", response_model=EvolutionCycleResponse)
async def run_evolution_cycle(
    app: AppState = Depends(get_app_state),
) -> EvolutionCycleResponse:
    """Run one full evolution cycle."""
    report = await app.evolution.run_evolution_cycle()
    report.maintenance_proposals = _store_proposals(report.maintenance_proposals)
    await _safe_broadcast("evolution_cycle_done", _cycle_payload(report))
    return EvolutionCycleResponse(
        cycle_id=report.cycle_id,
        started_at=report.started_at,
        completed_at=report.completed_at,
        tasks_total=report.tasks_total,
        tasks_completed=report.tasks_completed,
        tasks_failed=report.tasks_failed,
        repaired=report.repaired,
        deprecated=report.deprecated,
        merged=report.merged,
        split=report.split,
        errors=report.errors,
        maintenance_proposals=report.maintenance_proposals,
    )
