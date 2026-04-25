"""Skill CRUD + 搜索路由。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...models.skill_model import Skill, SkillState, SkillType
from ..deps import AppState, get_app_state
from ..schemas import (
    OKResponse,
    SkillCreateRequest,
    SkillListRequest,
    SkillSearchRequest,
    SkillSearchResult,
    SkillSummary,
    SkillUpdateRequest,
    EvolutionStats,
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


@router.get("", response_model=List[SkillSummary])
async def list_skills(
    state: Optional[SkillState] = Query(None),
    skill_type: Optional[SkillType] = Query(None),
    tags: Optional[str] = Query(None, description="逗号分隔的标签"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    app: AppState = Depends(get_app_state),
) -> List[SkillSummary]:
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    skills = await app.wiki.list(
        state=state,
        skill_type=skill_type,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )
    return [_to_summary(s) for s in skills]


@router.get("/evolution-stats", response_model=EvolutionStats)
async def get_evolution_stats(
    app: AppState = Depends(get_app_state),
) -> EvolutionStats:
    skills = await app.wiki.list()
    total = len(skills)
    auto_gen = sum(1 for s in skills if s.provenance and s.provenance.source_type in ("ingest", "auto", "trajectory"))
    manual = total - auto_gen
    total_exec = sum(s.metrics.total_executions for s in skills)
    avg_reuse = total_exec / total if total else 0.0
    rated = [s for s in skills if s.metrics.total_executions >= 5]
    avg_sr = sum(s.metrics.success_rate for s in rated) / len(rated) if rated else 1.0
    multi_version = sum(1 for s in skills if s.version != "1.0.0")
    by_cat: dict = {}
    for s in skills:
        cat = s.meta_category.value if s.meta_category else s.skill_type.value
        by_cat[cat] = by_cat.get(cat, 0) + 1
    recent = sorted(skills, key=lambda s: s.updated_at, reverse=True)[:8]
    activity = [
        {
            "skill_id": s.skill_id,
            "name": s.name,
            "event": "updated" if s.version != "1.0.0" else "created",
            "state": s.state.value,
            "time": s.updated_at.isoformat(),
        }
        for s in recent
    ]
    return EvolutionStats(
        total_skills=total,
        auto_generated=auto_gen,
        manual=manual,
        avg_reuse_rate=round(avg_reuse, 2),
        avg_success_rate=round(avg_sr, 3),
        version_improved_count=multi_version,
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
    created = await app.wiki.create(skill)
    return _to_summary(created)


@router.get("/{skill_id}", response_model=SkillSummary)
async def get_skill(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    return _to_summary(skill)


@router.get("/{skill_id}/full", response_model=Skill)
async def get_skill_full(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> Skill:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    return skill


@router.patch("/{skill_id}", response_model=SkillSummary)
async def update_skill(
    skill_id: str,
    req: SkillUpdateRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    updates: dict = {}
    if req.description is not None:
        updates["description"] = req.description
    if req.tags is not None:
        updates["tags"] = req.tags
    if req.interface is not None:
        updates["interface"] = req.interface.model_dump()
    if req.implementation is not None:
        updates["implementation"] = req.implementation.model_dump()

    updated = await app.wiki.db.update(skill_id, updates)
    if not updated:
        raise HTTPException(status_code=500, detail="更新失败")
    await app.wiki.cache.invalidate(skill_id)
    return _to_summary(updated)


@router.delete("/{skill_id}", response_model=OKResponse)
async def delete_skill(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> OKResponse:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    deleted = await app.wiki.db.delete(skill_id)
    if deleted:
        await app.wiki.cache.invalidate(skill_id)
    return OKResponse(message=f"Skill {skill_id} 已删除")


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
        max_results=req.limit,
    )
    results = await app.search.search(query)
    return [
        SkillSearchResult(
            skill_id=r.skill.skill_id,
            name=r.skill.name,
            description=r.skill.description,
            skill_type=r.skill.skill_type,
            state=r.skill.state,
            tags=r.skill.tags,
            version=r.skill.version,
            score=r.score,
            match_reason=", ".join(r.match_reasons) if r.match_reasons else "",
        )
        for r in results
    ]


@router.get("/{skill_id}/versions", response_model=List[SkillSummary])
async def get_version_history(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> List[SkillSummary]:
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    history = await app.wiki.get_version_history(skill.name)
    return [_to_summary(s) for s in history]
