"""Health monitoring and evolution routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException

from ...layers.feedback_evolution import (
    EvolutionReport,
    HealthStatus,
    SkillHealthReport,
    SystemHealthReport,
)
from ...utils.logger import get_logger
from ..deps import AppState, get_app_state
from ..schemas import (
    EvolutionCycleResponse,
    HealthReportResponse,
    SystemHealthResponse,
)
from .ws import broadcast

router = APIRouter(prefix="/evolution", tags=["evolution"])
logger = get_logger(__name__)
_HEALTH_EVENT_COOLDOWN_SECONDS = 30
_last_health_event_at: Dict[Tuple[str, str], datetime] = {}


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
        "skill_name": "SkillOS system",
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
        "errors": list(report.errors),
        "timestamp": _event_timestamp(),
    }


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
    return HealthReportResponse(
        skill_id=report.skill_id,
        skill_name=report.skill_name,
        status=report.status.value,
        success_rate=report.success_rate,
        usage_count=report.usage_count,
        avg_latency_ms=report.avg_latency_ms,
        issues=report.issues,
        recommendations=report.recommendations,
    )


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
        skill_reports=[
            HealthReportResponse(
                skill_id=r.skill_id,
                skill_name=r.skill_name,
                status=r.status.value,
                success_rate=r.success_rate,
                usage_count=r.usage_count,
                avg_latency_ms=r.avg_latency_ms,
                issues=r.issues,
                recommendations=r.recommendations,
            )
            for r in sys_report.skill_reports
        ],
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
    )
