"""API 请求/响应 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from ..models.skill_model import (
    SkillImplementation,
    SkillInterface,
    SkillMetrics,
    SkillProvenance,
    SkillState,
    SkillType,
)


# ─── 通用 ────────────────────────────────────────────────────────────────────

class OKResponse(BaseModel):
    ok: bool = True
    message: str = ""


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: Optional[str] = None


# ─── Skill CRUD ──────────────────────────────────────────────────────────────

class SkillCreateRequest(BaseModel):
    name: str
    description: str
    skill_type: SkillType = SkillType.ATOMIC
    tags: List[str] = Field(default_factory=list)
    interface: SkillInterface
    implementation: Optional[SkillImplementation] = None
    author: str = "api"


class SkillUpdateRequest(BaseModel):
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    interface: Optional[SkillInterface] = None
    implementation: Optional[SkillImplementation] = None
    author: str = "api"


class SkillListRequest(BaseModel):
    state: Optional[SkillState] = None
    skill_type: Optional[SkillType] = None
    tags: Optional[List[str]] = None
    limit: int = Field(default=50, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class SkillSearchRequest(BaseModel):
    query: str
    tags: Optional[List[str]] = None
    skill_type: Optional[SkillType] = None
    domain: Optional[str] = None
    state: Optional[SkillState] = None
    min_success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    include_deprecated: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class SkillSearchResult(BaseModel):
    skill_id: str
    name: str
    description: str
    skill_type: SkillType
    state: SkillState
    tags: List[str]
    version: str
    score: float
    match_reason: str


class SkillSummary(BaseModel):
    skill_id: str
    name: str
    description: str
    skill_type: SkillType
    state: SkillState
    tags: List[str]
    version: str
    granularity_level: int
    metrics: SkillMetrics
    created_at: datetime
    updated_at: datetime


class SkillVersionFieldDiff(BaseModel):
    field: str
    change_type: Literal["added", "removed", "modified"]
    old_value: Any = None
    new_value: Any = None


class SkillVersionHistoryItem(SkillSummary):
    previous_version: Optional[str] = None
    diff_to_previous: List[SkillVersionFieldDiff] = Field(default_factory=list)


# ─── 生命周期 ─────────────────────────────────────────────────────────────────

class TransitionRequest(BaseModel):
    new_state: SkillState
    reason: str = ""
    author: str = "api"


class ReleaseRequest(BaseModel):
    author: str = "api"


class DeprecateRequest(BaseModel):
    reason: str
    replacement_id: Optional[str] = None


class NewVersionRequest(BaseModel):
    bump: str = Field(default="patch", pattern=r"^(major|minor|patch)$")
    description: Optional[str] = None
    author: str = "api"


# ─── 图谱 ─────────────────────────────────────────────────────────────────────

class AddEdgeRequest(BaseModel):
    source_id: str
    target_id: str
    edge_type: str
    weight: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphNodeData(BaseModel):
    id: str
    name: str
    skill_type: str
    state: str
    tags: List[str]
    version: str
    granularity_level: int
    success_rate: float
    usage_count: int
    label: str | None = None
    size: int = 16
    color: str = "#9CA3AF"
    tooltip: str | None = None

class GraphEdgeData(BaseModel):
    id: str
    source: str
    target: str
    edge_type: str
    weight: float


class GraphData(BaseModel):
    nodes: List[GraphNodeData]
    edges: List[GraphEdgeData]
    stats: Dict[str, Any] = Field(default_factory=dict)


class SubgraphRequest(BaseModel):
    skill_id: str
    depth: int = Field(default=2, ge=1, le=5)


# ─── 执行 ─────────────────────────────────────────────────────────────────────

class ExecuteSkillRequest(BaseModel):
    skill_id: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)


class ExecutePlanRequest(BaseModel):
    goal: str
    context: Dict[str, Any] = Field(default_factory=dict)
    max_skills: int = Field(default=10, ge=1, le=50)


class ExecutionStepResult(BaseModel):
    step_id: str
    skill_id: str
    skill_name: str
    status: str
    outputs: Dict[str, Any]
    latency_ms: float
    error: Optional[str] = None


class RetrievedSkill(BaseModel):
    skill_id: str
    name: str
    description: str
    skill_type: str
    score: float
    match_reason: str


class ExecutionResult(BaseModel):
    plan_id: str
    goal: str
    status: str
    steps: List[ExecutionStepResult]
    total_latency_ms: float
    final_state: Dict[str, Any]
    retrieved_skills: List[RetrievedSkill] = Field(default_factory=list)
    experience_recorded: bool = False
    suggested_skill: Optional[Dict[str, Any]] = None


class ExecutionHistoryItem(BaseModel):
    execution_id: str
    goal: str
    status: str
    step_count: int
    success_count: int
    total_latency_ms: float
    retrieved_skill_count: int
    created_at: datetime


class EvolutionStats(BaseModel):
    total_skills: int
    auto_generated: int
    manual: int
    avg_reuse_rate: float
    avg_success_rate: float
    version_improved_count: int
    skills_by_category: Dict[str, int]
    recent_activity: List[Dict[str, Any]]


# ─── 健康监控 ─────────────────────────────────────────────────────────────────

class HealthReportResponse(BaseModel):
    skill_id: str
    skill_name: str
    status: str
    success_rate: float
    usage_count: int
    avg_latency_ms: float
    issues: List[str]
    recommendations: List[str]


class SystemHealthResponse(BaseModel):
    total_skills: int
    healthy_count: int
    degraded_count: int
    critical_count: int
    stale_count: int
    health_ratio: float
    skill_reports: List[HealthReportResponse]


# ─── 演化 ─────────────────────────────────────────────────────────────────────

class EvolutionCycleResponse(BaseModel):
    cycle_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    tasks_total: int
    tasks_completed: int
    tasks_failed: int
    repaired: List[str]
    deprecated: List[str]
    merged: List[Tuple[List[str], str]]
    split: List[Tuple[str, List[str]]]
    errors: List[str]


# ─── 统计 ─────────────────────────────────────────────────────────────────────

class OverviewStats(BaseModel):
    total_skills: int
    by_state: Dict[str, int]
    by_type: Dict[str, int]
    total_executions: int
    avg_success_rate: float
    graph_stats: Dict[str, Any]
