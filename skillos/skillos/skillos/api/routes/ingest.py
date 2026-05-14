"""Knowledge ingest routes for trajectory, document, API doc, and script inputs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import AppState, get_app_state

router = APIRouter(prefix="/ingest", tags=["ingest"])

ALLOWED_SOURCE_TYPES = {"trajectory", "document", "api_doc", "script"}


class IngestRequest(BaseModel):
    source_type: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class ExperienceUnitOut(BaseModel):
    unit_id: str
    source_type: str
    raw_content: str
    extracted_actions: List[str]
    normalized_actions: List[Dict[str, Any]]
    summary: str
    proposed_skill_name: Optional[str]
    proposed_description: Optional[str]
    proposed_type: Optional[str]
    confidence: float
    index_keywords: List[str]
    index_embedding_hint: str


class CreatedSkillOut(BaseModel):
    skill_id: str
    name: str
    skill_type: str
    state: str
    version: str


class IngestResponse(BaseModel):
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: List[str]
    units: List[ExperienceUnitOut]
    created_skills: List[CreatedSkillOut] = Field(default_factory=list)


def _validate_request(req: IngestRequest) -> str:
    source_type = req.source_type.strip().lower()
    if source_type not in ALLOWED_SOURCE_TYPES:
        allowed = ", ".join(sorted(ALLOWED_SOURCE_TYPES))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported source_type '{req.source_type}'. Expected one of: {allowed}.",
        )
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content must not be empty.")
    return source_type


def _unit_to_out(unit: Any) -> ExperienceUnitOut:
    raw_content = getattr(unit, "raw_content", "") or ""
    return ExperienceUnitOut(
        unit_id=unit.unit_id,
        source_type=unit.source_type if isinstance(unit.source_type, str) else str(unit.source_type),
        raw_content=raw_content[:500],
        extracted_actions=getattr(unit, "extracted_actions", []) or [],
        normalized_actions=getattr(unit, "normalized_actions", []) or [],
        summary=getattr(unit, "summary", "") or "",
        proposed_skill_name=getattr(unit, "proposed_skill_name", None),
        proposed_description=getattr(unit, "proposed_description", None),
        proposed_type=getattr(unit, "proposed_type", None),
        confidence=getattr(unit, "confidence", 0.5),
        index_keywords=getattr(unit, "index_keywords", []) or [],
        index_embedding_hint=getattr(unit, "index_embedding_hint", "") or "",
    )


@router.post("/parse", response_model=IngestResponse)
async def parse_input(
    req: IngestRequest,
    app: AppState = Depends(get_app_state),
) -> IngestResponse:
    """Parse raw input through the Experience Pipeline."""
    if not app.pipeline:
        raise HTTPException(status_code=503, detail="Experience Pipeline is not initialized.")

    source_type = _validate_request(req)

    import asyncio

    try:
        result = await asyncio.to_thread(app.pipeline.process, req.content, source_type)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return IngestResponse(
        success=result.success,
        source_type=result.source_type,
        unit_count=result.unit_count,
        token_usage=result.token_usage,
        errors=result.errors,
        units=[_unit_to_out(unit) for unit in result.units],
    )


@router.post("/parse-and-create", response_model=IngestResponse)
async def parse_and_create_skills(
    req: IngestRequest,
    app: AppState = Depends(get_app_state),
) -> IngestResponse:
    """Parse raw input and create candidate Skills in the Wiki."""
    if not app.pipeline:
        raise HTTPException(status_code=503, detail="Experience Pipeline is not initialized.")

    source_type = _validate_request(req)

    import asyncio

    from ...models.skill_model import (
        MetaSkillCategory,
        Skill,
        SkillImplementation,
        SkillInterface,
        SkillProvenance,
        SkillState,
        SkillType,
    )

    try:
        result = await asyncio.to_thread(app.pipeline.process, req.content, source_type)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    created_skills: List[CreatedSkillOut] = []
    errors = list(result.errors)

    for unit in result.units:
        name = unit.proposed_skill_name or f"skill_from_{source_type}"
        description = unit.proposed_description or unit.raw_content[:100] or name
        try:
            skill_type = SkillType(unit.proposed_type or "atomic")
            skill = Skill(
                name=name,
                description=description,
                skill_type=skill_type,
                meta_category=MetaSkillCategory.GENERATION if skill_type == SkillType.STRATEGIC else None,
                state=SkillState.SKILL_CANDIDATE,
                tags=[source_type, "auto-imported"] + unit.index_keywords[:3],
                interface=SkillInterface(
                    input_schema={"type": "object", "properties": {}},
                    output_schema={"type": "object", "properties": {}},
                ),
                implementation=SkillImplementation(
                    prompt_template=unit.summary or description,
                ),
                provenance=SkillProvenance(
                    source_type=source_type,
                    source_ids=[unit.unit_id],
                    created_by_agent="ingest_pipeline",
                    creation_context={"source_type": source_type},
                ),
            )
            created = await app.wiki.create(skill)
            created_skills.append(
                CreatedSkillOut(
                    skill_id=created.skill_id,
                    name=created.name,
                    skill_type=created.skill_type.value,
                    state=created.state.value,
                    version=created.version,
                )
            )
        except Exception as exc:
            errors.append(f"Failed to create skill '{name}': {exc}")

    return IngestResponse(
        success=result.success and (len(result.units) == 0 or len(created_skills) > 0),
        source_type=result.source_type,
        unit_count=result.unit_count,
        token_usage=result.token_usage,
        errors=errors,
        units=[_unit_to_out(unit) for unit in result.units],
        created_skills=created_skills,
    )
