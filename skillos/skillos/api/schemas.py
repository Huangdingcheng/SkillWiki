"""API 请求/响应 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from ..models.maintenance_model import (
    MaintenanceProposal,
    MaintenanceProposalStatus,
    ReflectionMemoryEntry,
)
from ..models.skill_model import (
    SkillEvaluation,
    SkillImplementation,
    SkillInterface,
    SkillMetrics,
    SkillProvenance,
    SkillState,
    SkillType,
)
from ..layers.skill_runtime.harness import (
    HarnessKind,
    HarnessRunResult,
    HarnessTestCase,
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
    state: SkillState = SkillState.DRAFT
    tags: List[str] = Field(default_factory=list)
    interface: SkillInterface
    implementation: Optional[SkillImplementation] = None
    evaluation: Optional[SkillEvaluation] = None
    provenance: Optional[SkillProvenance] = None
    author: str = "api"

    @field_validator("state")
    @classmethod
    def validate_create_state(cls, state: SkillState) -> SkillState:
        allowed = {
            SkillState.RAW_EXPERIENCE,
            SkillState.SKILL_CANDIDATE,
            SkillState.DRAFT,
        }
        if state not in allowed:
            raise ValueError("Skill creation API only accepts S0/S1/S2; use lifecycle review for later states.")
        return state


class SkillUpdateRequest(BaseModel):
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    interface: Optional[SkillInterface] = None
    implementation: Optional[SkillImplementation] = None
    evaluation: Optional[SkillEvaluation] = None
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
    mode: str = Field(default="lexical", pattern=r"^(lexical|rule|hybrid|semantic)$")
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
    search_mode: str = "lexical"
    score_components: Dict[str, float] = Field(default_factory=dict)
    explanation: Dict[str, Any] = Field(default_factory=dict)


class SkillSummary(BaseModel):
    skill_id: str
    name: str
    description: str
    skill_type: SkillType
    state: SkillState
    tags: List[str]
    version: str
    granularity_level: int
    evaluation: SkillEvaluation = Field(default_factory=SkillEvaluation)
    metrics: SkillMetrics
    created_at: datetime
    updated_at: datetime


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


class SnapshotCommitRequest(BaseModel):
    author: str = "api"
    message: Optional[str] = None


class SnapshotCommitResponse(BaseModel):
    skill_id: str
    skill_name: str
    version: str
    snapshot_path: str
    commit: str
    message: str


class SnapshotHistoryResponse(BaseModel):
    skill_id: str
    snapshot_path: str
    history: List[Dict[str, Any]]


class SnapshotDiffResponse(BaseModel):
    skill_id: str
    snapshot_path: str
    from_snapshot_path: Optional[str] = None
    to_snapshot_path: Optional[str] = None
    from_ref: str
    to_ref: str
    raw_diff: str
    diffs: List[Dict[str, Any]]
    has_breaking_changes: bool
    review_recommendation: str
    impacted_skills: List[Dict[str, Any]] = Field(default_factory=list)


class ReleaseTagRequest(BaseModel):
    ref: str = "HEAD"


class RollbackRequest(BaseModel):
    source_ref: str


class MaintenanceReviewRequest(BaseModel):
    proposal_id: str
    patched_skill: Dict[str, Any]
    reason: str = ""
    author: str = "SkillMaintainerAgent"


class MaintenanceReviewResponse(BaseModel):
    skill_id: str
    proposal_id: str
    branch_name: str
    base_commit: str
    head_commit: str
    snapshot_path: str
    structured_diff: List[Dict[str, Any]]
    has_breaking_changes: bool
    review_status: str
    impacted_skills: List[Dict[str, Any]] = Field(default_factory=list)
    reason: str = ""
    author: str = "SkillMaintainerAgent"


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


class SkillGraphProjectionEdgeData(BaseModel):
    id: str
    source: str
    target: str
    edge_type: str
    weight: float
    confidence: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SkillGraphProjectionData(BaseModel):
    nodes: List[GraphNodeData]
    edges: List[SkillGraphProjectionEdgeData]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    validation_evidence: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    stats: Dict[str, Any] = Field(default_factory=dict)


class HeterogeneousGraphNodeData(BaseModel):
    id: str
    kind: str
    name: str
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HeterogeneousGraphEdgeData(BaseModel):
    id: str
    source: str
    target: str
    edge_type: str
    weight: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HeterogeneousGraphData(BaseModel):
    nodes: List[HeterogeneousGraphNodeData]
    edges: List[HeterogeneousGraphEdgeData]
    stats: Dict[str, Any] = Field(default_factory=dict)


class GraphViewNodeData(BaseModel):
    id: str
    name: str
    kind: str = "skill"
    description: str = ""
    skill_type: Optional[str] = None
    state: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    version: Optional[str] = None
    granularity_level: Optional[int] = None
    success_rate: Optional[float] = None
    usage_count: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphViewEdgeData(BaseModel):
    id: str
    source: str
    target: str
    edge_type: str
    weight: float
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphViewData(BaseModel):
    view: str
    source_endpoint: str
    nodes: List[GraphViewNodeData]
    edges: List[GraphViewEdgeData]
    stats: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    validation_evidence: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)


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
    step_index: int = 0
    skill_id: str
    skill_name: str
    status: str
    input_mapping: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class RetrievedSkill(BaseModel):
    skill_id: str
    name: str
    description: str
    skill_type: str
    score: float
    match_reason: str


class ExecutionExperienceUnit(BaseModel):
    unit_id: str
    source_type: str = "agent_execution"
    source_execution_id: str
    raw_content: str
    extracted_actions: List[str] = Field(default_factory=list)
    normalized_actions: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    proposed_skill_name: Optional[str] = None
    proposed_description: Optional[str] = None
    proposed_type: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    index_keywords: List[str] = Field(default_factory=list)
    index_embedding_hint: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    plan_id: str
    goal: str
    status: str
    steps: List[ExecutionStepResult]
    total_latency_ms: float
    final_state: Dict[str, Any]
    retrieved_skills: List[RetrievedSkill] = Field(default_factory=list)
    experience_recorded: bool = False
    experience_unit: Optional[ExecutionExperienceUnit] = None
    suggested_skill: Optional[Dict[str, Any]] = None
    verifier_passed: Optional[bool] = None
    verifier_summary: Optional[Dict[str, Any]] = None


class ExecutionHistoryItem(BaseModel):
    execution_id: str
    goal: str
    status: str
    step_count: int
    success_count: int
    total_latency_ms: float
    retrieved_skill_count: int
    created_at: datetime
    experience_unit_id: Optional[str] = None
    experience_source_type: Optional[str] = None


class HarnessVerifyLoopRequest(BaseModel):
    harness: HarnessKind = HarnessKind.LOCAL_SKILLOS
    max_attempts: int = Field(default=3, ge=1, le=5)
    promote_on_pass: bool = True
    test_cases: List[HarnessTestCase] = Field(default_factory=list)
    allow_repair: bool = True
    timeout_s: int = Field(default=120, ge=1, le=600)


class HarnessVerifyLoopResponse(BaseModel):
    loop_id: str
    skill_id: str
    status: str
    promotion_allowed: bool
    attempt_count: int
    score: Dict[str, Any]
    attempts: List[HarnessRunResult] = Field(default_factory=list)
    repairs: List[Dict[str, Any]] = Field(default_factory=list)
    initial_version: str
    final_version: str
    final_state: str
    evidence_path: str


class HarnessLoopListResponse(BaseModel):
    loops: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


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
    maintenance_proposal: Optional[MaintenanceProposal] = None


class SystemHealthResponse(BaseModel):
    total_skills: int
    healthy_count: int
    degraded_count: int
    critical_count: int
    stale_count: int
    health_ratio: float
    skill_reports: List[HealthReportResponse]


# ─── 演化 ─────────────────────────────────────────────────────────────────────

class MaintenanceProposalNextAction(BaseModel):
    """Next human-governed action after accepting a D-side proposal."""

    action: str = "create_review_bundle"
    method: str = "POST"
    endpoint: str
    required_payload_fields: List[str] = Field(default_factory=list)
    reason: str = ""


class MaintenanceProposalResponse(MaintenanceProposal):
    """API representation for D-side human-review maintenance proposals."""

    next_action: Optional[MaintenanceProposalNextAction] = None


class MaintenanceProposalListResponse(BaseModel):
    proposals: List[MaintenanceProposalResponse] = Field(default_factory=list)
    total: int = 0
    pending_count: int = 0

    @classmethod
    def from_proposals(
        cls,
        proposals: List[MaintenanceProposal],
    ) -> "MaintenanceProposalListResponse":
        pending = [
            proposal
            for proposal in proposals
            if proposal.status == MaintenanceProposalStatus.PENDING
        ]
        return cls(
            proposals=[
                MaintenanceProposalResponse.model_validate(proposal.model_dump())
                for proposal in proposals
            ],
            total=len(proposals),
            pending_count=len(pending),
        )


class ReflectionMemoryRequest(BaseModel):
    """Runtime reflection memory item submitted before creating a proposal."""

    task_id: str = ""
    skill_id: str
    goal: str = ""
    success: bool = False
    failure_signature: str = ""
    reflection_text: str = ""
    evidence: List[str] = Field(default_factory=list)
    verifier_result: Dict[str, Any] = Field(default_factory=dict)
    trajectory_summary: str = ""
    human_decision: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ReflectionMemoryResponse(BaseModel):
    """Stored reflection memory and optional threshold-triggered proposal."""

    memory: ReflectionMemoryEntry
    occurrence_count: int
    threshold: int
    proposal: Optional[MaintenanceProposalResponse] = None


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
    maintenance_proposals: List[MaintenanceProposal] = Field(default_factory=list)


# ─── 统计 ─────────────────────────────────────────────────────────────────────

class OverviewStats(BaseModel):
    total_skills: int
    by_state: Dict[str, int]
    by_type: Dict[str, int]
    total_executions: int
    avg_success_rate: float
    graph_stats: Dict[str, Any]
