"""知识导入路由 — 支持轨迹、文档、API文档、代码四种输入源。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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


class IngestResponse(BaseModel):
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: List[str]
    units: List[ExperienceUnitOut]


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
    """解析输入并自动创建 Skill 草稿（S1 候选状态）。"""
    if not app.pipeline:
        raise HTTPException(status_code=503, detail="Experience Pipeline 未初始化")

    import asyncio
    from ...models.skill_model import Skill, SkillState, SkillType, SkillProvenance, SkillInterface, SkillImplementation

    try:
        result = await asyncio.to_thread(
            app.pipeline.process, req.content, req.source_type.lower()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    for unit in result.units:
        name = unit.proposed_skill_name or f"skill_from_{req.source_type}"
        desc = unit.proposed_description or unit.raw_content[:100]
        try:
            skill = Skill(
                name=name,
                description=desc,
                skill_type=SkillType(unit.proposed_type or "atomic"),
                state=SkillState.SKILL_CANDIDATE,
                tags=[req.source_type, "auto-imported"] + unit.index_keywords[:3],
                interface=SkillInterface(
                    input_schema={"type": "object", "properties": {}},
                    output_schema={"type": "object", "properties": {}},
                ),
                implementation=SkillImplementation(
                    prompt_template=unit.summary or desc,
                ),
                provenance=SkillProvenance(
                    source_type=req.source_type,
                    author="ingest_pipeline",
                    source_id=unit.unit_id,
                ),
            )
            await app.wiki.create(skill)
        except (ValueError, Exception):
            pass

    return IngestResponse(
        success=result.success,
        source_type=result.source_type,
        unit_count=result.unit_count,
        token_usage=result.token_usage,
        errors=result.errors,
        units=[_unit_to_out(u) for u in result.units],
    )
