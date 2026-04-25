"""PostgreSQL 存储层 — SQLAlchemy 2.0 异步 ORM。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models.experience_model import (
    ExecutionStatus,
    ExperienceSourceType,
    ExperienceUnit,
    SkillExecutionRecord,
    SkillProposal,
    SkillProposalStatus,
)
from ..models.skill_model import Skill, SkillState, SkillType
from ..utils.logger import get_logger
from .base import BaseConnection, BaseRepository

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class SkillORM(Base):
    """Skill 主表。"""

    __tablename__ = "skills"

    skill_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    skill_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    meta_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    domain: Mapped[str] = mapped_column(String(64), default="general", index=True)
    granularity_level: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    tags: Mapped[Optional[str]] = mapped_column(Text, default="[]")  # JSON array

    # Interface (JSON)
    interface_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Implementation (JSON)
    implementation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Test cases (JSON array)
    test_cases_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")

    # External references (JSON arrays)
    tool_refs_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    trajectory_refs_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    doc_refs_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")

    # Metrics
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    p95_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Provenance (JSON)
    provenance_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Graph relations (JSON arrays of IDs)
    dependency_ids_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    component_ids_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")

    # Deprecation
    deprecation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    replacement_skill_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deprecated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_skill_name_version"),
    )


class ExperienceUnitORM(Base):
    """经验单元表。"""

    __tablename__ = "experience_units"

    experience_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    steps_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_content_format: Mapped[str] = mapped_column(String(32), default="text")
    task_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(String(64), default="general", index=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extracted_skill_ids_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillProposalORM(Base):
    """Skill 候选提案表。"""

    __tablename__ = "skill_proposals"

    proposal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_experience_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    proposed_name: Mapped[str] = mapped_column(String(128), nullable=False)
    proposed_description: Mapped[str] = mapped_column(Text, default="")
    proposed_type: Mapped[str] = mapped_column(String(32), default="atomic")
    proposed_domain: Mapped[str] = mapped_column(String(64), default="general")
    proposed_tags_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    input_schema_draft_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    output_schema_draft_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    preconditions_draft_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    postconditions_draft_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    similar_skill_ids_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    similarity_scores_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    generated_skill_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    merged_into_skill_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    extraction_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillExecutionRecordORM(Base):
    """Skill 执行记录表。"""

    __tablename__ = "skill_execution_records"

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    skill_version: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    parent_skill_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    input_data_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    output_data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    state_before_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    state_after_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sub_executions_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    human_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    feedback_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Mappers: ORM ↔ Pydantic
# ---------------------------------------------------------------------------

def _j(v: Optional[str], default: Any = None) -> Any:
    """安全 JSON 解析。"""
    if v is None:
        return default
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return default


def orm_to_skill(row: SkillORM) -> Skill:
    from ..models.skill_model import (
        SkillImplementation,
        SkillInterface,
        SkillMetrics,
        SkillProvenance,
        SkillTestCase,
    )

    interface_data = _j(row.interface_json, {})
    impl_data = _j(row.implementation_json)
    provenance_data = _j(row.provenance_json)

    return Skill(
        skill_id=row.skill_id,
        name=row.name,
        version=row.version,
        display_name=row.display_name or "",
        description=row.description or "",
        skill_type=SkillType(row.skill_type),
        meta_category=row.meta_category,
        domain=row.domain,
        granularity_level=row.granularity_level,
        state=SkillState(row.state),
        tags=_j(row.tags, []),
        interface=SkillInterface(**interface_data) if interface_data else SkillInterface(),
        implementation=SkillImplementation(**impl_data) if impl_data else None,
        test_cases=[SkillTestCase(**tc) for tc in _j(row.test_cases_json, [])],
        tool_refs=_j(row.tool_refs_json, []),
        trajectory_refs=_j(row.trajectory_refs_json, []),
        doc_refs=_j(row.doc_refs_json, []),
        metrics=SkillMetrics(
            usage_count=row.usage_count,
            success_count=row.success_count,
            failure_count=row.failure_count,
            avg_latency_ms=row.avg_latency_ms,
            p95_latency_ms=row.p95_latency_ms,
            last_used_at=row.last_used_at,
        ),
        provenance=SkillProvenance(**provenance_data) if provenance_data else None,
        dependency_ids=_j(row.dependency_ids_json, []),
        component_ids=_j(row.component_ids_json, []),
        deprecation_reason=row.deprecation_reason,
        replacement_skill_id=row.replacement_skill_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        released_at=row.released_at,
        deprecated_at=row.deprecated_at,
    )


def skill_to_orm(skill: Skill) -> SkillORM:
    return SkillORM(
        skill_id=skill.skill_id,
        name=skill.name,
        version=skill.version,
        display_name=skill.display_name,
        description=skill.description,
        skill_type=skill.skill_type.value,
        meta_category=skill.meta_category.value if skill.meta_category else None,
        domain=skill.domain,
        granularity_level=skill.granularity_level,
        state=skill.state.value,
        tags=json.dumps(skill.tags),
        interface_json=skill.interface.model_dump_json(),
        implementation_json=skill.implementation.model_dump_json() if skill.implementation else None,
        test_cases_json=json.dumps([tc.model_dump() for tc in skill.test_cases]),
        tool_refs_json=json.dumps(skill.tool_refs),
        trajectory_refs_json=json.dumps(skill.trajectory_refs),
        doc_refs_json=json.dumps(skill.doc_refs),
        usage_count=skill.metrics.usage_count,
        success_count=skill.metrics.success_count,
        failure_count=skill.metrics.failure_count,
        avg_latency_ms=skill.metrics.avg_latency_ms,
        p95_latency_ms=skill.metrics.p95_latency_ms,
        last_used_at=skill.metrics.last_used_at,
        provenance_json=skill.provenance.model_dump_json() if skill.provenance else None,
        dependency_ids_json=json.dumps(skill.dependency_ids),
        component_ids_json=json.dumps(skill.component_ids),
        deprecation_reason=skill.deprecation_reason,
        replacement_skill_id=skill.replacement_skill_id,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        released_at=skill.released_at,
        deprecated_at=skill.deprecated_at,
    )


# ---------------------------------------------------------------------------
# PostgreSQL Connection
# ---------------------------------------------------------------------------

class PostgresConnection(BaseConnection):
    """PostgreSQL 异步连接管理器。"""

    def __init__(self, dsn: str, pool_size: int = 10, max_overflow: int = 20) -> None:
        self._dsn = dsn
        self._engine = create_async_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=max_overflow,
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    async def connect(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("PostgreSQL 连接成功，表结构已初始化")

    async def disconnect(self) -> None:
        await self._engine.dispose()
        logger.info("PostgreSQL 连接已关闭")

    async def health_check(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"PostgreSQL 健康检查失败: {e}")
            return False

    async def ping(self) -> float:
        import time
        start = time.monotonic()
        await self.health_check()
        return (time.monotonic() - start) * 1000

    def session(self) -> AsyncSession:
        return self._session_factory()


# ---------------------------------------------------------------------------
# Skill Repository (PostgreSQL)
# ---------------------------------------------------------------------------

class SkillRepository(BaseRepository[Skill]):
    """Skill 的 PostgreSQL 仓储实现。"""

    def __init__(self, conn: PostgresConnection) -> None:
        self._conn = conn

    async def get(self, id: str) -> Optional[Skill]:
        from sqlalchemy import select
        async with self._conn.session() as session:
            result = await session.execute(
                select(SkillORM).where(SkillORM.skill_id == id)
            )
            row = result.scalar_one_or_none()
            return orm_to_skill(row) if row else None

    async def get_by_name_version(self, name: str, version: str) -> Optional[Skill]:
        from sqlalchemy import select
        async with self._conn.session() as session:
            result = await session.execute(
                select(SkillORM).where(
                    SkillORM.name == name, SkillORM.version == version
                )
            )
            row = result.scalar_one_or_none()
            return orm_to_skill(row) if row else None

    async def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Skill]:
        from sqlalchemy import select
        stmt = select(SkillORM)
        if filters:
            if "state" in filters:
                stmt = stmt.where(SkillORM.state == filters["state"])
            if "skill_type" in filters:
                stmt = stmt.where(SkillORM.skill_type == filters["skill_type"])
            if "domain" in filters:
                stmt = stmt.where(SkillORM.domain == filters["domain"])
            if "name_like" in filters:
                stmt = stmt.where(SkillORM.name.like(f"%{filters['name_like']}%"))
        stmt = stmt.limit(limit).offset(offset).order_by(SkillORM.created_at.desc())
        async with self._conn.session() as session:
            result = await session.execute(stmt)
            return [orm_to_skill(row) for row in result.scalars().all()]

    async def create(self, entity: Skill) -> Skill:
        orm = skill_to_orm(entity)
        async with self._conn.session() as session:
            session.add(orm)
            await session.commit()
            await session.refresh(orm)
        logger.debug(f"Skill 已创建: {entity.name} v{entity.version}")
        return entity

    async def update(self, id: str, data: Dict[str, Any]) -> Optional[Skill]:
        from sqlalchemy import select, update as sa_update
        async with self._conn.session() as session:
            data["updated_at"] = datetime.utcnow()
            await session.execute(
                sa_update(SkillORM).where(SkillORM.skill_id == id).values(**data)
            )
            await session.commit()
        return await self.get(id)

    async def delete(self, id: str) -> bool:
        from sqlalchemy import delete as sa_delete
        async with self._conn.session() as session:
            result = await session.execute(
                sa_delete(SkillORM).where(SkillORM.skill_id == id)
            )
            await session.commit()
            return result.rowcount > 0

    async def exists(self, id: str) -> bool:
        from sqlalchemy import select, func
        async with self._conn.session() as session:
            result = await session.execute(
                select(func.count()).where(SkillORM.skill_id == id)
            )
            return result.scalar() > 0

    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        from sqlalchemy import select, func
        stmt = select(func.count(SkillORM.skill_id))
        if filters:
            if "state" in filters:
                stmt = stmt.where(SkillORM.state == filters["state"])
            if "skill_type" in filters:
                stmt = stmt.where(SkillORM.skill_type == filters["skill_type"])
        async with self._conn.session() as session:
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def search_by_tags(self, tags: List[str], limit: int = 50) -> List[Skill]:
        """按标签搜索（简单 LIKE 匹配）。"""
        from sqlalchemy import select, or_
        conditions = [SkillORM.tags.like(f'%"{tag}"%') for tag in tags]
        async with self._conn.session() as session:
            result = await session.execute(
                select(SkillORM).where(or_(*conditions)).limit(limit)
            )
            return [orm_to_skill(row) for row in result.scalars().all()]
