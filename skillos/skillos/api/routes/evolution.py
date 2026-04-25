"""健康监控 + 演化路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import AppState, get_app_state
from ..schemas import (
    EvolutionCycleResponse,
    HealthReportResponse,
    OKResponse,
    SystemHealthResponse,
)

router = APIRouter(prefix="/evolution", tags=["evolution"])


@router.get("/health/{skill_id}", response_model=HealthReportResponse)
async def get_skill_health(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> HealthReportResponse:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    report = app.monitor.evaluate_skill(skill)
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
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
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
    """触发一次完整演化周期（修复/废弃/合并/拆分）。"""
    report = await app.evolution.run_evolution_cycle()
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
