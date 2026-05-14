"""经验单元和 Skill 候选模型 — Skill 生命周期的起点。"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ExperienceSourceType(str, Enum):
    """经验来源类型。"""
    BROWSER_TRAJECTORY = "browser_trajectory"   # 浏览器操作轨迹
    API_INTERACTION = "api_interaction"          # API 调用记录
    CODE_EXECUTION = "code_execution"            # 代码执行记录
    HUMAN_DEMONSTRATION = "human_demonstration" # 人工演示
    AGENT_EXECUTION = "agent_execution"          # Agent 执行记录
    DOCUMENTATION = "documentation"             # 技术文档
    MANUAL_INPUT = "manual_input"               # 手动输入


class TrajectoryStep(BaseModel):
    """轨迹中的单个操作步骤。"""

    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    step_index: int = Field(ge=0)
    action_type: str = Field(description="操作类型（click, type, navigate, call_api, ...）")
    action_target: Optional[str] = Field(default=None, description="操作目标（元素选择器、URL 等）")
    action_value: Optional[str] = Field(default=None, description="操作值（输入文本、参数等）")
    state_before: Dict[str, Any] = Field(default_factory=dict, description="操作前状态快照")
    state_after: Dict[str, Any] = Field(default_factory=dict, description="操作后状态快照")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: Optional[float] = Field(default=None, ge=0.0)
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExperienceUnit(BaseModel):
    """原始经验单元（S0 状态），是 Skill 生成的原材料。

    可以是一段操作轨迹、一份 API 文档、一次人工演示等。
    存储在 PostgreSQL 中，通过 trajectory_refs 被 Skill 引用。
    """

    experience_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_type: ExperienceSourceType
    title: str = Field(default="", max_length=256)
    description: str = Field(default="")

    # 轨迹步骤（仅 trajectory 类型）
    steps: List[TrajectoryStep] = Field(default_factory=list)

    # 原始内容（文档、代码等）
    raw_content: Optional[str] = Field(default=None, description="原始文本内容")
    raw_content_format: str = Field(default="text", description="内容格式: text | json | html | markdown")

    # 上下文信息
    task_description: Optional[str] = Field(default=None, description="完成的任务描述")
    domain: str = Field(default="general")
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 处理状态
    is_processed: bool = False
    processed_at: Optional[datetime] = None
    extracted_skill_ids: List[str] = Field(
        default_factory=list,
        description="从该经验中提取的 Skill ID 列表",
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, v: List[str]) -> List[str]:
        return [t.strip().lower() for t in v if t.strip()]

    def mark_processed(self, skill_ids: List[str]) -> None:
        self.is_processed = True
        self.processed_at = datetime.utcnow()
        self.extracted_skill_ids.extend(skill_ids)
        self.updated_at = datetime.utcnow()

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def duration_ms(self) -> float:
        if not self.steps:
            return 0.0
        durations = [s.duration_ms for s in self.steps if s.duration_ms is not None]
        return sum(durations)


# ---------------------------------------------------------------------------
# Skill Proposal (S1 → S2 过渡)
# ---------------------------------------------------------------------------

class SkillProposalStatus(str, Enum):
    PENDING = "pending"         # 等待处理
    ACCEPTED = "accepted"       # 已接受，生成 Draft Skill
    REJECTED = "rejected"       # 已拒绝
    MERGED = "merged"           # 与已有 Skill 合并


class SkillProposal(BaseModel):
    """Skill 候选提案（S1 状态），由解析器从经验中提取。

    在正式生成 Draft Skill 之前的中间表示，包含 LLM 对 Skill 的初步理解。
    """

    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_experience_id: str = Field(description="来源经验单元 ID")

    # 提案内容（LLM 初步提取）
    proposed_name: str = Field(description="建议的 Skill 名称")
    proposed_description: str = Field(description="建议的功能描述")
    proposed_type: str = Field(default="atomic", description="建议的 Skill 类型")
    proposed_domain: str = Field(default="general")
    proposed_tags: List[str] = Field(default_factory=list)

    # 接口草案
    input_schema_draft: Dict[str, Any] = Field(default_factory=dict)
    output_schema_draft: Dict[str, Any] = Field(default_factory=dict)
    preconditions_draft: List[str] = Field(default_factory=list)
    postconditions_draft: List[str] = Field(default_factory=list)

    # 相似 Skill 检测
    similar_skill_ids: List[str] = Field(
        default_factory=list,
        description="与已有 Skill 相似的 ID 列表（用于决定是否合并）",
    )
    similarity_scores: Dict[str, float] = Field(
        default_factory=dict,
        description="与相似 Skill 的相似度分数",
    )

    # 处理结果
    status: SkillProposalStatus = Field(default=SkillProposalStatus.PENDING)
    generated_skill_id: Optional[str] = Field(
        default=None,
        description="接受后生成的 Draft Skill ID",
    )
    rejection_reason: Optional[str] = None
    merged_into_skill_id: Optional[str] = None

    # 置信度
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="提案置信度")
    extraction_model: Optional[str] = Field(default=None, description="提取使用的 LLM 模型")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def accept(self, skill_id: str) -> None:
        self.status = SkillProposalStatus.ACCEPTED
        self.generated_skill_id = skill_id
        self.updated_at = datetime.utcnow()

    def reject(self, reason: str) -> None:
        self.status = SkillProposalStatus.REJECTED
        self.rejection_reason = reason
        self.updated_at = datetime.utcnow()

    def merge_into(self, target_skill_id: str) -> None:
        self.status = SkillProposalStatus.MERGED
        self.merged_into_skill_id = target_skill_id
        self.updated_at = datetime.utcnow()


# ---------------------------------------------------------------------------
# Skill Execution Record
# ---------------------------------------------------------------------------

class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SkillExecutionRecord(BaseModel):
    """单次 Skill 执行记录，用于反馈和演化。"""

    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    skill_id: str
    skill_version: str

    # 执行上下文
    task_id: Optional[str] = Field(default=None, description="所属任务 ID")
    agent_id: Optional[str] = Field(default=None, description="执行的 Agent ID")
    parent_skill_id: Optional[str] = Field(default=None, description="调用该 Skill 的父 Skill ID")

    # 输入输出
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output_data: Optional[Dict[str, Any]] = None
    state_before: Dict[str, Any] = Field(default_factory=dict)
    state_after: Dict[str, Any] = Field(default_factory=dict)

    # 执行结果
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    error_message: Optional[str] = None
    error_type: Optional[str] = None

    # 时间指标
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    latency_ms: Optional[float] = None

    # 子 Skill 执行（组合 Skill）
    sub_executions: List[str] = Field(
        default_factory=list,
        description="子 Skill 执行记录 ID 列表",
    )

    # 反馈
    human_feedback: Optional[str] = None
    feedback_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    def start(self) -> None:
        self.status = ExecutionStatus.RUNNING
        self.started_at = datetime.utcnow()

    def complete(self, output: Dict[str, Any], state_after: Dict[str, Any]) -> None:
        self.status = ExecutionStatus.SUCCESS
        self.output_data = output
        self.state_after = state_after
        self.completed_at = datetime.utcnow()
        if self.started_at:
            self.latency_ms = (self.completed_at - self.started_at).total_seconds() * 1000

    def fail(self, error_message: str, error_type: str = "RuntimeError") -> None:
        self.status = ExecutionStatus.FAILED
        self.error_message = error_message
        self.error_type = error_type
        self.completed_at = datetime.utcnow()
        if self.started_at:
            self.latency_ms = (self.completed_at - self.started_at).total_seconds() * 1000
