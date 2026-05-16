"""Knowledge ingest routes for trajectory, document, API doc, script, and past Skill inputs."""

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

PARSE_SOURCE_TYPES = {"trajectory", "document", "api_doc", "script", "past_skills"}
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
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
    dependency_ids: List[str] = Field(default_factory=list)
    component_ids: List[str] = Field(default_factory=list)
    sub_skill_ids: List[str] = Field(default_factory=list)
    parent_skill_ids: List[str] = Field(default_factory=list)
    tool_calls: List[str] = Field(default_factory=list)
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
        metadata=getattr(unit, "metadata", {}) or {},
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
    parent_skill_ids = _unique_ids([*provenance.parent_skill_ids, *req.parent_skill_ids])
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
    provenance = provenance.model_copy(update={
        "parent_skill_ids": parent_skill_ids,
        "creation_context": provenance_context,
    })
    sub_skill_ids = _unique_ids([*req.sub_skill_ids, *req.component_ids])
    dependency_ids = _unique_ids(req.dependency_ids)
    component_ids = _unique_ids([*req.component_ids, *sub_skill_ids])

    return Skill(
        name=req.name,
        description=req.description,
        skill_type=req.skill_type,
        meta_category=MetaSkillCategory.GENERATION if req.skill_type == SkillType.STRATEGIC else None,
        state=SkillState.SKILL_CANDIDATE,
        tags=_unique_ids([source_type, "candidate-review", *req.tags]),
        interface=SkillInterface(
            input_schema=req.input_schema,
            output_schema=req.output_schema,
            preconditions=req.preconditions,
            postconditions=req.postconditions,
        ),
        implementation=SkillImplementation(
            language="prompt",
            prompt_template=prompt_template,
            tool_calls=_unique_ids(req.tool_calls),
            sub_skill_ids=sub_skill_ids,
        ),
        evaluation=req.evaluation,
        provenance=provenance,
        dependency_ids=dependency_ids,
        component_ids=component_ids,
    )


def _unique_ids(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


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
        if hasattr(graph, "sync_auto_edges") and app.wiki:
            skills = await app.wiki.list(limit=10000)
            await graph.sync_auto_edges(skill, [item.skill_id for item in skills])
        await _sync_candidate_hetero_graph(app, skill)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph sync failed: {exc}") from exc


async def _sync_candidate_hetero_graph(app: AppState, skill: Skill) -> None:
    graph = getattr(app, "graph", None)
    if graph is None or not hasattr(graph, "add_hetero_node") or not hasattr(graph, "add_hetero_edge"):
        return

    from ...models.graph_model import (
        ExecutionGraphNode,
        HeteroEdgeType,
        HeteroGraphEdge,
        HeteroSkillNode,
        SourceGraphNode,
        ValidationGraphNode,
        VersionGraphNode,
    )

    source_type = skill.provenance.source_type if skill.provenance else "unknown"
    source_ids = skill.provenance.source_ids if skill.provenance else []
    source_group = _source_group_tag(skill.tags)
    source_node_id = (
        f"source:{source_group}"
        if source_group
        else f"source:{source_type}:{source_ids[0] if source_ids else skill.skill_id}"
    )
    skill_node_id = f"skill:{skill.skill_id}"
    execution_node_id = f"execution:ingest:{skill.skill_id}"
    validation_node_id = f"validation:ingest:{skill.skill_id}"
    version_node_id = f"version:{skill.skill_id}:{skill.version}"

    await graph.add_hetero_node(SourceGraphNode(
        node_id=source_node_id,
        name=source_group or f"{source_type}_source",
        description=f"Imported {source_type} source used to derive Candidate Skill {skill.name}.",
        source_uri=f"{source_type}://{source_group or (source_ids[0] if source_ids else skill.skill_id)}",
        source_type=source_type,
        metadata={
            "source_group": source_group,
            "source_ids": source_ids,
            "skillos_import": True,
        },
        created_by="ingest_pipeline",
    ))
    await graph.add_hetero_node(_hetero_skill_node(skill))
    await graph.add_hetero_node(ExecutionGraphNode(
        node_id=execution_node_id,
        name=f"ingest_review:{skill.name}",
        description="Candidate creation and audit event used as lightweight execution evidence.",
        execution_id=execution_node_id,
        status="completed",
        skill_ref=skill_node_id,
        metadata={
            "event_type": "ingest_create_candidate",
            "verifier_spec_count": len(skill.evaluation.verifier_specs if skill.evaluation else []),
            "skillos_import": True,
        },
        created_by="ingest_pipeline",
    ))
    await graph.add_hetero_node(ValidationGraphNode(
        node_id=validation_node_id,
        name=f"candidate_validation:{skill.name}",
        description="Candidate audit/verifier contract captured during ingest.",
        validation_id=validation_node_id,
        outcome="candidate",
        validator="SkillOS candidate auditor",
        metadata={
            "state": skill.state.value,
            "validation_summary": skill.evaluation.validation_summary if skill.evaluation else "",
            "verifier_specs": skill.evaluation.verifier_specs if skill.evaluation else [],
            "skillos_import": True,
        },
        created_by="ingest_pipeline",
    ))
    await graph.add_hetero_node(VersionGraphNode(
        node_id=version_node_id,
        name=f"{skill.name}@{skill.version}",
        description="Candidate version captured during ingest.",
        version_id=version_node_id,
        version_label=skill.version,
        release_state=skill.state.value,
        metadata={"skill_id": skill.skill_id, "skillos_import": True},
        created_by="ingest_pipeline",
    ))

    await _add_hetero_edge(graph, source_node_id, skill_node_id, HeteroEdgeType.DERIVED_FROM)
    await _add_hetero_edge(graph, skill_node_id, execution_node_id, HeteroEdgeType.EXECUTED_AS)
    await _add_hetero_edge(graph, execution_node_id, validation_node_id, HeteroEdgeType.VALIDATED_BY)
    await _add_hetero_edge(graph, skill_node_id, version_node_id, HeteroEdgeType.VERSIONED_AS)

    related_ids = _unique_ids([
        *(skill.implementation.sub_skill_ids if skill.implementation else []),
        *skill.component_ids,
        *(skill.provenance.parent_skill_ids if skill.provenance else []),
    ])
    related_skills = await _skills_by_id(app, related_ids)
    for related_id in related_ids:
        related = related_skills.get(related_id)
        if not related:
            continue
        await graph.add_hetero_node(_hetero_skill_node(related))

    for component_id in _unique_ids([
        *(skill.implementation.sub_skill_ids if skill.implementation else []),
        *skill.component_ids,
    ]):
        if component_id in related_skills and component_id != skill.skill_id:
            await _add_hetero_edge(
                graph,
                skill_node_id,
                f"skill:{component_id}",
                HeteroEdgeType.COMPOSES_WITH,
            )

    parent_ids = skill.provenance.parent_skill_ids if skill.provenance else []
    for parent_id in _unique_ids(parent_ids):
        if parent_id in related_skills and parent_id != skill.skill_id:
            await _add_hetero_edge(
                graph,
                version_node_id,
                f"skill:{parent_id}",
                HeteroEdgeType.COMPOSES_WITH,
            )


def _source_group_tag(tags: List[str]) -> str:
    for tag in tags:
        text = str(tag or "").strip()
        if text.startswith("source_group:"):
            return text.split(":", 1)[1].strip()
    return ""


def _hetero_skill_node(skill: Skill) -> Any:
    from ...models.graph_model import HeteroSkillNode

    return HeteroSkillNode(
        node_id=f"skill:{skill.skill_id}",
        name=skill.name,
        description=skill.description,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        skill_state=skill.state.value,
        metadata={
            "skill_type": skill.skill_type.value,
            "tags": skill.tags,
            "domain": skill.meta_category.value if skill.meta_category else "skillos",
            "skillos_import": True,
        },
        created_by="ingest_pipeline",
    )


async def _add_hetero_edge(graph: Any, source_id: str, target_id: str, edge_type: Any) -> None:
    from ...models.graph_model import HeteroGraphEdge

    await graph.add_hetero_edge(HeteroGraphEdge(
        edge_id=f"ingest:{edge_type.value}:{source_id}:{target_id}",
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        metadata={"source": "ingest_pipeline", "skillos_import": True},
        created_by="ingest_pipeline",
    ))


async def _skills_by_id(app: AppState, skill_ids: List[str]) -> Dict[str, Skill]:
    if not app.wiki or not skill_ids:
        return {}
    if hasattr(app.wiki, "get_many"):
        return await app.wiki.get_many(skill_ids)
    result: Dict[str, Skill] = {}
    getter = getattr(app.wiki, "get", None)
    if not callable(getter):
        return result
    for skill_id in skill_ids:
        skill = await getter(skill_id)
        if skill:
            result[skill_id] = skill
    return result


async def _known_skill_refs(app: AppState) -> List[Dict[str, str]]:
    if not app.wiki:
        return []
    try:
        skills = await app.wiki.list(limit=10000)
    except Exception:
        return []
    return [
        {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "display_name": skill.display_name,
        }
        for skill in skills
    ]


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
        known_skills = await _known_skill_refs(app)
        result = await asyncio.to_thread(
            app.pipeline.process,
            req.content,
            source_type,
            known_skills,
            req.metadata,
        )
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
        known_skills = await _known_skill_refs(app)
        result = await asyncio.to_thread(
            app.pipeline.process,
            req.content,
            source_type,
            known_skills,
            req.metadata,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    created_skills: List[CreatedSkillOut] = []
    errors = list(result.errors)

    for unit in result.units:
        name = unit.proposed_skill_name or f"skill_from_{source_type}"
        description = unit.proposed_description or unit.raw_content[:100] or name
        metadata = getattr(unit, "metadata", {}) or {}
        candidate_interface = metadata.get("candidate_interface", {}) if isinstance(metadata, dict) else {}
        candidate_impl = metadata.get("candidate_implementation", {}) if isinstance(metadata, dict) else {}
        candidate_relations = metadata.get("candidate_relations", {}) if isinstance(metadata, dict) else {}
        candidate_evaluation = metadata.get("candidate_evaluation", {}) if isinstance(metadata, dict) else {}
        try:
            skill_type = SkillType(unit.proposed_type or "atomic")
            parent_ids = _unique_ids(candidate_relations.get("parent_skill_ids", []))
            skill = Skill(
                name=name,
                description=description,
                skill_type=skill_type,
                meta_category=MetaSkillCategory.GENERATION if skill_type == SkillType.STRATEGIC else None,
                state=SkillState.SKILL_CANDIDATE,
                tags=_unique_ids([source_type, "auto-imported", *unit.index_keywords[:3]]),
                interface=SkillInterface(
                    input_schema=candidate_interface.get("input_schema") or {"type": "object", "properties": {}},
                    output_schema=candidate_interface.get("output_schema") or {"type": "object", "properties": {}},
                    preconditions=candidate_interface.get("preconditions") or [],
                    postconditions=candidate_interface.get("postconditions") or [],
                ),
                implementation=SkillImplementation(
                    prompt_template=candidate_impl.get("prompt_template") or unit.summary or description,
                    tool_calls=_unique_ids(candidate_impl.get("tool_calls", [])),
                    sub_skill_ids=_unique_ids(candidate_impl.get("sub_skill_ids", [])),
                ),
                evaluation=SkillEvaluation(**candidate_evaluation) if isinstance(candidate_evaluation, dict) else SkillEvaluation(),
                provenance=SkillProvenance(
                    source_type=source_type,
                    source_ids=[unit.unit_id],
                    parent_skill_ids=parent_ids,
                    created_by_agent="ingest_pipeline",
                    creation_context={
                        "source_type": source_type,
                        "ctx2skill_evidence": metadata.get("ctx2skill_evidence") if isinstance(metadata, dict) else None,
                    },
                ),
                dependency_ids=_unique_ids(candidate_relations.get("dependency_ids", [])),
                component_ids=_unique_ids(candidate_relations.get("component_ids", [])),
            )
            created = await app.wiki.create(skill)
            await _sync_candidate_graph(app, created)
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
