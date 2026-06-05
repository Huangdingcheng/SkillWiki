"""知识导入路由 — 支持轨迹、文档、API文档、代码四种输入源。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from ..deps import AppState, get_app_state

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestRequest(BaseModel):
    source_type: str  # trajectory | document | api_doc | script
    content: str
    metadata: Optional[Dict[str, Any]] = None


class ExperienceUnitOut(BaseModel):
    unit_id: str
    source_type: str
    raw_content: str
    extracted_actions: List[str]
    proposed_skill_name: Optional[str]
    proposed_description: Optional[str]
    proposed_type: Optional[str]
    confidence: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: List[str]
    units: List[ExperienceUnitOut]
    created_skill_ids: List[str] = Field(default_factory=list)
    graph_nodes_created: int = 0
    graph_edges_created: int = 0
    agent_trace: List[Dict[str, Any]] = Field(default_factory=list)


class AnthropicSkillImportRequest(BaseModel):
    path: str = Field(..., description="Local path to anthropics/skills repo, a skills directory, or one SKILL.md")
    namespace: str = Field(default="anthropic", description="Prefix used to avoid name collisions")
    overwrite_existing: bool = Field(default=False, description="Deprecated. Final Anthropic Skills are never overwritten.")


class AnthropicSkillImportResponse(BaseModel):
    success: bool
    imported_count: int
    skipped_count: int
    errors: List[str] = Field(default_factory=list)
    created_skill_ids: List[str] = Field(default_factory=list)
    imported_skills: List[Dict[str, Any]] = Field(default_factory=list)


def _unit_to_out(unit: Any) -> ExperienceUnitOut:
    return ExperienceUnitOut(
        unit_id=unit.unit_id,
        source_type=unit.source_type if isinstance(unit.source_type, str) else str(unit.source_type),
        raw_content=unit.raw_content[:500],
        extracted_actions=getattr(unit, "extracted_actions", []) or [],
        proposed_skill_name=getattr(unit, "proposed_skill_name", None),
        proposed_description=getattr(unit, "proposed_description", None),
        proposed_type=getattr(unit, "proposed_type", None),
        confidence=getattr(unit, "confidence", 0.5),
        metadata=getattr(unit, "metadata", {}) or {},
    )


@router.post("/parse", response_model=IngestResponse)
async def parse_input(
    req: IngestRequest,
    app: AppState = Depends(get_app_state),
) -> IngestResponse:
    """通过 Experience Pipeline 解析原始输入。"""
    if not app.pipeline:
        raise HTTPException(status_code=503, detail="Experience Pipeline 未初始化")

    import asyncio
    try:
        result = await asyncio.to_thread(
            app.pipeline.process, req.content, req.source_type.lower()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return IngestResponse(
        success=result.success,
        source_type=result.source_type,
        unit_count=result.unit_count,
        token_usage=result.token_usage,
        errors=result.errors,
        units=[_unit_to_out(u) for u in result.units],
    )


@router.post("/parse-and-create", response_model=IngestResponse)
async def parse_and_create_skills(
    req: IngestRequest,
    app: AppState = Depends(get_app_state),
) -> IngestResponse:
    """解析输入，并由 Self-Management Agents 创建 Skill 与图谱上下文。"""
    if not app.pipeline:
        raise HTTPException(status_code=503, detail="Experience Pipeline 未初始化")
    if not app.meta_controller:
        raise HTTPException(status_code=503, detail="Meta-Controller Agent 未初始化")

    import asyncio

    try:
        result = await asyncio.to_thread(
            app.pipeline.process, req.content, req.source_type.lower()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    created_skill_ids: List[str] = []
    graph_nodes_created = 0
    graph_edges_created = 0
    agent_trace: List[Dict[str, Any]] = []

    for unit in result.units:
        managed = await app.meta_controller.manage_ingested_unit(
            unit=unit,
            wiki=app.wiki,
            request_source_type=req.source_type,
        )
        if managed.skill:
            created_skill_ids.append(managed.skill.skill_id)
        graph_nodes_created += managed.graph_nodes_created
        graph_edges_created += managed.graph_edges_created
        result.errors.extend(managed.errors)
        agent_trace.extend([
            {
                "agent": step.agent,
                "action": step.action,
                "status": step.status,
                "details": step.details,
            }
            for step in managed.trace
        ])

    return IngestResponse(
        success=result.success and not result.errors,
        source_type=result.source_type,
        unit_count=result.unit_count,
        token_usage=result.token_usage,
        errors=result.errors,
        units=[_unit_to_out(u) for u in result.units],
        created_skill_ids=created_skill_ids,
        graph_nodes_created=graph_nodes_created,
        graph_edges_created=graph_edges_created,
        agent_trace=agent_trace,
    )


@router.post("/anthropic-skills", response_model=AnthropicSkillImportResponse)
async def import_anthropic_skills(
    req: AnthropicSkillImportRequest,
    app: AppState = Depends(get_app_state),
) -> AnthropicSkillImportResponse:
    """Import Anthropic Agent Skills as final immutable SkillOS skills."""
    if not app.wiki:
        raise HTTPException(status_code=503, detail="Skill Wiki 未初始化")

    from ...layers.input_knowledge.anthropic_skills import load_anthropic_skills

    result = load_anthropic_skills(req.path, namespace=req.namespace)
    created_skill_ids: List[str] = []
    imported: List[Dict[str, Any]] = []

    for skill in result.skills:
        existing = await app.wiki.get_by_name(skill.name, skill.version)
        if existing:
            result.skipped.append(skill.name)
            continue
        try:
            created = await app.wiki.create(skill)
            created_skill_ids.append(created.skill_id)
            imported.append({
                "skill_id": created.skill_id,
                "name": created.name,
                "original_name": created.provenance.creation_context.get("original_name") if created.provenance else "",
                "version": created.version,
                "source_format": created.source_format,
                "is_final": created.is_final,
                "immutable": created.immutable,
            })
            graph = getattr(app, "graph", None)
            if graph is not None:
                await graph.sync_skill(created)
        except Exception as exc:
            result.errors.append(f"{skill.name}: {exc}")

    if app.search and hasattr(app.search, "warmup"):
        try:
            await app.search.warmup()
        except Exception:
            pass

    return AnthropicSkillImportResponse(
        success=not result.errors,
        imported_count=len(created_skill_ids),
        skipped_count=len(result.skipped),
        errors=result.errors,
        created_skill_ids=created_skill_ids,
        imported_skills=imported,
    )
