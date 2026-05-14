"""Knowledge ingest routes for trajectory, document, API doc, and script inputs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from ...layers.skill_management.auditor import SkillAuditorAgent
from ...models.skill_model import (
    MetaSkillCategory,
    Skill,
    SkillEvaluation,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillType,
)
from ..deps import AppState, get_app_state

router = APIRouter(prefix="/ingest", tags=["ingest"])

PARSE_SOURCE_TYPES = {"trajectory", "document", "api_doc", "script"}
CANDIDATE_SOURCE_TYPES = PARSE_SOURCE_TYPES | {"agent_execution"}


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


class CandidateSkillReviewRequest(BaseModel):
    source_type: str
    unit_id: str
    raw_content: str = ""
    name: str
    description: str
    skill_type: SkillType = SkillType.ATOMIC
    tags: List[str] = Field(default_factory=list)
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    preconditions: List[str] = Field(default_factory=list)
    postconditions: List[str] = Field(default_factory=list)
    prompt_template: str = ""
    provenance: Optional[SkillProvenance] = None
    evaluation: SkillEvaluation = Field(default_factory=SkillEvaluation)
    author: str = "human_reviewer"


class CandidateAuditOut(BaseModel):
    skill_id: str
    skill_name: str
    passed: bool
    schema_ok: bool
    safety_ok: bool
    postcondition_ok: bool
    issues: List[str]
    warnings: List[str]
    recommendations: List[str]
    audit_score: float


class CandidateCreateResponse(BaseModel):
    success: bool
    created_skill: CreatedSkillOut
    audit: CandidateAuditOut


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
    if source_type not in PARSE_SOURCE_TYPES:
        allowed = ", ".join(sorted(PARSE_SOURCE_TYPES))
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


def _build_candidate_skill(req: CandidateSkillReviewRequest) -> Skill:
    source_type = req.source_type.strip().lower()
    if source_type not in CANDIDATE_SOURCE_TYPES:
        allowed = ", ".join(sorted(CANDIDATE_SOURCE_TYPES))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported source_type '{req.source_type}'. Expected one of: {allowed}.",
        )

    prompt_template = req.prompt_template.strip() or req.description
    provenance = req.provenance or SkillProvenance(
        source_type=source_type,
        source_ids=[req.unit_id],
        created_by_agent=req.author,
        creation_context={},
    )
    provenance_context = dict(provenance.creation_context)
    paper_backlog_task = "C-P1-2" if source_type == "agent_execution" else "E-P0-1"
    provenance_context.update(
        {
            "source_type": source_type,
            "unit_id": req.unit_id,
            "raw_content_preview": req.raw_content[:500],
            "paper_backlog_task": paper_backlog_task,
            "human_review_required": True,
        }
    )
    provenance = provenance.model_copy(update={"creation_context": provenance_context})

    return Skill(
        name=req.name,
        description=req.description,
        skill_type=req.skill_type,
        meta_category=MetaSkillCategory.GENERATION if req.skill_type == SkillType.STRATEGIC else None,
        state=SkillState.SKILL_CANDIDATE,
        tags=[source_type, "candidate-review"] + req.tags,
        interface=SkillInterface(
            input_schema=req.input_schema,
            output_schema=req.output_schema,
            preconditions=req.preconditions,
            postconditions=req.postconditions,
        ),
        implementation=SkillImplementation(
            language="prompt",
            prompt_template=prompt_template,
        ),
        evaluation=req.evaluation,
        provenance=provenance,
    )


def _audit_to_out(result: Any) -> CandidateAuditOut:
    return CandidateAuditOut(
        skill_id=result.skill_id,
        skill_name=result.skill_name,
        passed=result.passed,
        schema_ok=result.schema_ok,
        safety_ok=result.safety_ok,
        postcondition_ok=result.postcondition_ok,
        issues=result.issues,
        warnings=result.warnings,
        recommendations=result.recommendations,
        audit_score=result.audit_score,
    )


async def _sync_candidate_graph(app: AppState, skill: Skill) -> None:
    graph = getattr(app, "graph", None)
    if graph is None or not hasattr(graph, "sync_skill"):
        return
    try:
        await graph.sync_skill(skill)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph sync failed: {exc}") from exc


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


@router.post("/audit-candidate", response_model=CandidateAuditOut)
async def audit_candidate_skill(
    req: CandidateSkillReviewRequest,
    app: AppState = Depends(get_app_state),
) -> CandidateAuditOut:
    """Audit an edited candidate Skill draft without writing it to the Wiki."""
    try:
        skill = _build_candidate_skill(req)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    auditor = getattr(app, "auditor", None) or SkillAuditorAgent()
    return _audit_to_out(auditor.audit(skill))


@router.post("/create-candidate", response_model=CandidateCreateResponse, status_code=201)
async def create_candidate_skill(
    req: CandidateSkillReviewRequest,
    app: AppState = Depends(get_app_state),
) -> CandidateCreateResponse:
    """Create an S1 Candidate Skill after human review of parsed experience."""
    if not app.wiki:
        raise HTTPException(status_code=503, detail="Skill Wiki is not initialized.")

    try:
        skill = _build_candidate_skill(req)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    auditor = getattr(app, "auditor", None) or SkillAuditorAgent()
    audit = _audit_to_out(auditor.audit(skill))
    try:
        created = await app.wiki.create(skill)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _sync_candidate_graph(app, created)
    return CandidateCreateResponse(
        success=True,
        created_skill=CreatedSkillOut(
            skill_id=created.skill_id,
            name=created.name,
            skill_type=created.skill_type.value,
            state=created.state.value,
            version=created.version,
        ),
        audit=audit,
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
