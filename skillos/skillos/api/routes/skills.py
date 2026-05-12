"""Skill CRUD and search routes."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...models.skill_model import Skill, SkillState, SkillType
from ..deps import AppState, get_app_state
from ..schemas import (
    EvolutionStats,
    OKResponse,
    SkillCreateRequest,
    SkillSearchRequest,
    SkillSearchResult,
    SkillVersionFieldDiff,
    SkillVersionHistoryItem,
    SkillSummary,
    SkillUpdateRequest,
)

router = APIRouter(prefix="/skills", tags=["skills"])


def _to_summary(skill: Skill) -> SkillSummary:
    return SkillSummary(
        skill_id=skill.skill_id,
        name=skill.name,
        description=skill.description,
        skill_type=skill.skill_type,
        state=skill.state,
        tags=skill.tags,
        version=skill.version,
        granularity_level=skill.granularity_level,
        metrics=skill.metrics,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


async def _sync_graph_for_skill(app: AppState, skill: Skill) -> None:
    try:
        await app.graph.sync_skill(skill)
        if hasattr(app.graph, "sync_auto_edges"):
            skills = await app.wiki.list(limit=10000)
            await app.graph.sync_auto_edges(skill, [item.skill_id for item in skills])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph sync failed: {exc}") from exc


@router.get("", response_model=List[SkillSummary])
async def list_skills(
    state: Optional[SkillState] = Query(None),
    skill_type: Optional[SkillType] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    app: AppState = Depends(get_app_state),
) -> List[SkillSummary]:
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else None
    skills = await app.wiki.list(
        state=state,
        skill_type=skill_type,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )
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
        interface=req.interface,
        implementation=req.implementation,
        provenance=SkillProvenance(source_type="api", author=req.author),
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

    updates: dict = {}
    if req.description is not None:
        updates["description"] = req.description
    if req.tags is not None:
        updates["tags"] = req.tags
    if req.interface is not None:
        updates["interface"] = req.interface
    if req.implementation is not None:
        updates["implementation"] = req.implementation

    updated = await app.wiki.update(skill_id, **updates)
    if not updated:
        raise HTTPException(status_code=500, detail="Skill update failed")
    await _sync_graph_for_skill(app, updated)
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
    deleted = await app.wiki.delete(skill_id)
    if deleted:
        try:
            await app.graph.remove_skill(skill_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Graph remove failed: {exc}") from exc
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
            version=result.skill.version,
            score=result.score,
            match_reason=", ".join(result.match_reasons) if result.match_reasons else "",
        )
        for result in results
    ]


@router.get("/{skill_id}/versions", response_model=List[SkillVersionHistoryItem])
async def get_version_history(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> List[SkillVersionHistoryItem]:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} does not exist")
    history = await app.wiki.get_version_history(skill.name)
    return [_to_version_history_item(item, previous) for previous, item in _pair_previous(history)]


def _pair_previous(history: List[Skill]) -> List[tuple[Optional[Skill], Skill]]:
    paired: List[tuple[Optional[Skill], Skill]] = []
    previous: Optional[Skill] = None
    for item in history:
        paired.append((previous, item))
        previous = item
    return paired


def _to_version_history_item(current: Skill, previous: Optional[Skill]) -> SkillVersionHistoryItem:
    summary = _to_summary(current)
    return SkillVersionHistoryItem(
        **summary.model_dump(),
        previous_version=previous.version if previous else None,
        diff_to_previous=_compute_version_diff(previous, current),
    )


def _compute_version_diff(previous: Optional[Skill], current: Skill) -> List[SkillVersionFieldDiff]:
    if previous is None:
        return []

    diffs: List[SkillVersionFieldDiff] = []
    comparisons = [
        ("description", previous.description, current.description),
        ("tags", previous.tags, current.tags),
        ("skill_type", previous.skill_type, current.skill_type),
        ("state", previous.state, current.state),
        ("interface.input_schema", previous.interface.input_schema, current.interface.input_schema),
        ("interface.output_schema", previous.interface.output_schema, current.interface.output_schema),
        ("interface.preconditions", previous.interface.preconditions, current.interface.preconditions),
        ("interface.postconditions", previous.interface.postconditions, current.interface.postconditions),
        (
            "implementation.language",
            getattr(previous.implementation, "language", None) if previous.implementation else None,
            getattr(current.implementation, "language", None) if current.implementation else None,
        ),
        (
            "implementation.prompt_template",
            getattr(previous.implementation, "prompt_template", None) if previous.implementation else None,
            getattr(current.implementation, "prompt_template", None) if current.implementation else None,
        ),
        (
            "implementation.sub_skill_ids",
            list(previous.implementation.sub_skill_ids) if previous.implementation else None,
            list(current.implementation.sub_skill_ids) if current.implementation else None,
        ),
        (
            "implementation.tool_calls",
            list(previous.implementation.tool_calls) if previous.implementation else None,
            list(current.implementation.tool_calls) if current.implementation else None,
        ),
    ]
    for field, old_value, new_value in comparisons:
        if old_value == new_value:
            continue
        if old_value is None:
            change_type = "added"
        elif new_value is None:
            change_type = "removed"
        else:
            change_type = "modified"
        diffs.append(SkillVersionFieldDiff(
            field=field,
            change_type=change_type,
            old_value=old_value,
            new_value=new_value,
        ))
    return diffs
