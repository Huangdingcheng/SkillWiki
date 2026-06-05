"""Skill CRUD and search routes."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...models.skill_model import Skill, SkillState, SkillType, SkillVisibility
from ..deps import AppState, get_app_state
from ..schemas import (
    EvolutionStats,
    OKResponse,
    SkillCreateRequest,
    SkillSearchRequest,
    SkillSearchResult,
    SkillSummary,
    SkillUpdateRequest,
)

router = APIRouter(prefix="/skills", tags=["skills"])


def _normalize_enum(value: Optional[str], enum_cls, field_name: str):
    if value is None or value == "":
        return None
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid_values = ", ".join(item.value for item in enum_cls)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field_name}: {value!r}. Expected one of: {valid_values}",
        ) from exc


def _normalize_visibility(value: str) -> str:
    normalized = (value or "user").strip().lower()
    if normalized == "all":
        return normalized
    try:
        return SkillVisibility(normalized).value
    except ValueError as exc:
        valid_values = ", ".join([*(item.value for item in SkillVisibility), "all"])
        raise HTTPException(
            status_code=422,
            detail=f"Invalid visibility: {value!r}. Expected one of: {valid_values}",
        ) from exc


def _to_summary(skill: Skill) -> SkillSummary:
    return SkillSummary(
        skill_id=skill.skill_id,
        name=skill.name,
        description=skill.description,
        source_format=getattr(skill, "source_format", "skillos"),
        is_final=getattr(skill, "is_final", False),
        immutable=getattr(skill, "immutable", False),
        skill_type=skill.skill_type,
        state=skill.state,
        tags=skill.tags,
        visibility=skill.visibility,
        version=skill.version,
        granularity_level=skill.granularity_level,
        metrics=skill.metrics,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


async def _sync_graph_for_skill(app: AppState, skill: Skill) -> None:
    graph = getattr(app, "graph", None)
    if graph is None:
        return
    try:
        await graph.sync_skill(skill)
        if hasattr(graph, "sync_auto_edges"):
            skills = await app.wiki.list(limit=10000)
            await graph.sync_auto_edges(skill, [item.skill_id for item in skills])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph sync failed: {exc}") from exc


@router.get("", response_model=List[SkillSummary])
async def list_skills(
    state: Optional[str] = Query(None),
    skill_type: Optional[str] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    query: Optional[str] = Query(None, description="Text query across name, description, tags, and source metadata"),
    visibility: str = Query("user", description="user | kernel | all"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    app: AppState = Depends(get_app_state),
) -> List[SkillSummary]:
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else None
    normalized_state = _normalize_enum(state, SkillState, "state")
    normalized_type = _normalize_enum(skill_type, SkillType, "skill_type")
    normalized_visibility = _normalize_visibility(visibility)
    skills = await app.wiki.list(
        state=normalized_state,
        skill_type=normalized_type,
        tags=tag_list,
        limit=10000 if normalized_visibility != "all" or query else limit,
        offset=offset,
    )
    if normalized_visibility != "all":
        skills = [skill for skill in skills if skill.visibility.value == normalized_visibility]
    if query:
        q = query.strip().lower()
        if q:
            def matches(skill: Skill) -> bool:
                creation_context = skill.provenance.creation_context if skill.provenance else {}
                haystack = " ".join([
                    skill.name,
                    skill.display_name or "",
                    skill.description,
                    getattr(skill, "source_format", "skillos"),
                    str(creation_context.get("original_name") or ""),
                    "final immutable" if getattr(skill, "is_final", False) or getattr(skill, "immutable", False) else "",
                    *skill.tags,
                ]).lower()
                return q in haystack
            skills = [skill for skill in skills if matches(skill)]
    if normalized_visibility != "all" or query:
        skills = skills[:limit]
    return [_to_summary(skill) for skill in skills]


@router.get("/evolution-stats", response_model=EvolutionStats)
async def get_evolution_stats(
    app: AppState = Depends(get_app_state),
) -> EvolutionStats:
    skills = await app.wiki.list()
    total = len(skills)
    auto_gen = sum(
        1 for skill in skills
        if skill.provenance and skill.provenance.source_type in ("ingest", "auto", "trajectory")
    )
    total_exec = sum(skill.metrics.total_executions for skill in skills)
    rated = [skill for skill in skills if skill.metrics.total_executions >= 5]
    avg_sr = sum(skill.metrics.success_rate for skill in rated) / len(rated) if rated else 1.0
    by_cat: dict = {}
    for skill in skills:
        cat = skill.meta_category.value if skill.meta_category else skill.skill_type.value
        by_cat[cat] = by_cat.get(cat, 0) + 1
    recent = sorted(skills, key=lambda skill: skill.updated_at, reverse=True)[:8]
    activity = [
        {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "event": "updated" if skill.version != "1.0.0" else "created",
            "state": skill.state.value,
            "time": skill.updated_at.isoformat(),
        }
        for skill in recent
    ]
    return EvolutionStats(
        total_skills=total,
        auto_generated=auto_gen,
        manual=total - auto_gen,
        avg_reuse_rate=round(total_exec / total, 2) if total else 0.0,
        avg_success_rate=round(avg_sr, 3),
        version_improved_count=sum(1 for skill in skills if skill.version != "1.0.0"),
        skills_by_category=by_cat,
        recent_activity=activity,
    )


@router.post("", response_model=SkillSummary, status_code=201)
async def create_skill(
    req: SkillCreateRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    from ...models.skill_model import SkillProvenance

    skill = Skill(
        name=req.name,
        description=req.description,
        skill_type=req.skill_type,
        tags=req.tags,
        visibility=req.visibility,
        interface=req.interface,
        implementation=req.implementation,
        provenance=SkillProvenance(source_type="api", created_by_agent=req.author),
    )
    try:
        created = await app.wiki.create(skill)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _sync_graph_for_skill(app, created)
    return _to_summary(created)


@router.get("/{skill_id}", response_model=SkillSummary)
async def get_skill(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    return _to_summary(skill)


@router.get("/{skill_id}/full", response_model=Skill)
async def get_skill_full(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> Skill:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    return skill


@router.patch("/{skill_id}", response_model=SkillSummary)
async def update_skill(
    skill_id: str,
    req: SkillUpdateRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    if getattr(skill, "is_locked", False):
        raise HTTPException(status_code=409, detail="Final immutable Skill cannot be updated")

    updates: dict = {}
    if req.description is not None:
        updates["description"] = req.description
    if req.tags is not None:
        updates["tags"] = req.tags
    if req.visibility is not None:
        updates["visibility"] = req.visibility
    if req.interface is not None:
        updates["interface"] = req.interface
    if req.implementation is not None:
        updates["implementation"] = req.implementation

    updated = await app.wiki.update(skill_id, **updates)
    if not updated:
        raise HTTPException(status_code=500, detail="Skill update failed")
    await _sync_graph_for_skill(app, updated)
    if hasattr(app.wiki, "invalidate"):
        await app.wiki.invalidate(skill_id)
    return _to_summary(updated)


@router.delete("/{skill_id}", response_model=OKResponse)
async def delete_skill(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> OKResponse:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    if getattr(skill, "is_locked", False):
        raise HTTPException(status_code=409, detail="Final immutable Skill cannot be deleted")
    deleted = await app.wiki.delete(skill_id)
    if deleted:
        graph = getattr(app, "graph", None)
        if graph is not None:
            try:
                await graph.remove_skill(skill_id)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Graph remove failed: {exc}") from exc
        if hasattr(app.wiki, "invalidate"):
            await app.wiki.invalidate(skill_id)
    return OKResponse(message=f"Skill {skill_id} deleted")


@router.post("/search", response_model=List[SkillSearchResult])
async def search_skills(
    req: SkillSearchRequest,
    app: AppState = Depends(get_app_state),
) -> List[SkillSearchResult]:
    from ...layers.skill_repository.indexing import SearchQuery

    query = SearchQuery(
        text=req.query,
        tags=req.tags or [],
        skill_type=req.skill_type,
        state=req.state,
        domain=getattr(req, "domain", None),
        min_success_rate=getattr(req, "min_success_rate", 0.0),
        include_deprecated=getattr(req, "include_deprecated", False),
        max_results=req.limit,
    )
    results = await app.search.search(query)
    return [
        SkillSearchResult(
            skill_id=result.skill.skill_id,
            name=result.skill.name,
            description=result.skill.description,
            skill_type=result.skill.skill_type,
            state=result.skill.state,
            tags=result.skill.tags,
            visibility=result.skill.visibility,
            version=result.skill.version,
            score=result.score,
            match_reason=", ".join(result.match_reasons) if result.match_reasons else "",
        )
        for result in results
    ]


@router.get("/{skill_id}/versions", response_model=List[SkillSummary])
async def get_version_history(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> List[SkillSummary]:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    history = await app.wiki.get_version_history(skill.name)
    return [_to_summary(item) for item in history]
