"""健康监控 + 演化路由。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from ...models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState
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
    visibility: str = Query("user", description="user | kernel | all"),
    app: AppState = Depends(get_app_state),
) -> SystemHealthResponse:
    from ...models.skill_model import SkillState
    normalized_visibility = visibility.lower().strip()
    if normalized_visibility not in {"user", "kernel", "all"}:
        raise HTTPException(status_code=400, detail="visibility must be user, kernel, or all")
    skills = await app.wiki.list(state=None, limit=10000)
    if normalized_visibility != "all":
        skills = [skill for skill in skills if skill.visibility.value == normalized_visibility]
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


@router.post("/improve/{skill_id}", response_model=dict)
async def improve_skill_after_evaluation(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> dict:
    """Evaluate a Skill, then let the maintenance agent generalize it when useful."""
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    health = app.monitor.evaluate_skill(skill)
    proposal = _build_generalization_proposal(skill, health)
    if proposal["action"] == "reuse_existing":
        if proposal.get("replacement_id"):
            try:
                await app.wiki.deprecate(skill.skill_id, proposal["reason"], replacement_id=proposal["replacement_id"])
            except Exception:
                pass
        return {
            "skill_id": skill.skill_id,
            "action": "reuse_existing",
            "reason": proposal["reason"],
            "replacement_id": proposal.get("replacement_id"),
            "health_status": health.status.value,
        }
    if proposal["action"] == "no_action":
        return {
            "skill_id": skill.skill_id,
            "action": "no_action",
            "reason": proposal["reason"],
            "health_status": health.status.value,
        }

    new_skill = await app.wiki.create_new_version(
        skill.skill_id,
        "minor",
        description=proposal["description"],
        tags=proposal["tags"],
        interface=proposal["interface"],
        implementation=proposal["implementation"],
        granularity_level=proposal["granularity_level"],
    )
    if app.graph:
        await app.graph.sync_skill(new_skill)
        await app.graph.add_evolution(new_skill.skill_id, skill.skill_id)
    if app.version_ctrl:
        try:
            app.version_ctrl.record_change(
                new_skill,
                change_type="minor",
                summary=proposal["reason"],
                author="EvaluationAgent",
                from_version=skill.version,
                to_version=new_skill.version,
            )
        except Exception:
            pass
    return {
        "skill_id": skill.skill_id,
        "action": "created_generalized_version",
        "reason": proposal["reason"],
        "new_skill_id": new_skill.skill_id,
        "new_version": new_skill.version,
        "health_status": health.status.value,
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


def _build_generalization_proposal(skill: Skill, health: Any) -> Dict[str, Any]:
    tool_calls = {str(name).lower() for name in (skill.implementation.tool_calls if skill.implementation else [])}
    tags = sorted(set(skill.tags + ["agent-improved", "generic"]))

    if "host.run_terminal_command" in tool_calls:
        if skill.name != "run_terminal_command":
            return {
                "action": "reuse_existing",
                "reason": "EvaluationAgent found this terminal-command Skill is too specialized; reuse the generic run_terminal_command Skill instead.",
                "replacement_id": "",
            }
        interface = SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Natural language terminal task"},
                    "command": {"type": "string", "description": "Agent-generated safe read-only command"},
                },
                "required": [],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "launched": {"type": "boolean"},
                    "command": {"type": "string"},
                    "stdout_preview": {"type": "string"},
                },
            },
            preconditions=["Agent has verified the command is safe and directly matches the user task."],
            postconditions=["Terminal runs the generated command and the output is checked against expected outcome."],
        )
        implementation = SkillImplementation(
            language="python",
            code='output["launched"] = True\noutput["command"] = input_data.get("command")',
            tool_calls=["host.run_terminal_command"],
        )
        return {
            "action": "create_version",
            "reason": "EvaluationAgent strengthened generic command semantics and postcondition checking for terminal tasks.",
            "description": "Run an agent-generated safe read-only Terminal command after comparing the user task with expected output and rejecting unrelated Skills.",
            "tags": tags,
            "interface": interface,
            "implementation": implementation,
            "granularity_level": 1,
        }

    if "host.open_url_in_chrome" in tool_calls or "host.open_search_first_result" in tool_calls:
        return {
            "action": "no_action",
            "reason": "EvaluationAgent kept this web Skill unchanged; execution agent already resolves target URLs/queries before using it.",
        }

    if health.issues or "specialized" in skill.tags:
        return {
            "action": "create_version",
            "reason": "EvaluationAgent generalized the Skill description/tags so retrieval treats it as auxiliary knowledge, not a task controller.",
            "description": f"{skill.description} The execution agent must verify relevance against the user's expected outcome before using this Skill.",
            "tags": tags,
            "interface": skill.interface,
            "implementation": skill.implementation,
            "granularity_level": skill.granularity_level,
        }

    return {
        "action": "no_action",
        "reason": "EvaluationAgent found no clear generalization need from the current health report.",
    }
