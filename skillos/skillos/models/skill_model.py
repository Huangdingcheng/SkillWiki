"""Skill 核心数据模型 — 覆盖完整生命周期和所有属性。"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator, computed_field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SkillState(str, Enum):
    """Skill 生命周期状态机（S0-S7）。"""
    RAW_EXPERIENCE = "S0"       # 原始经验，未结构化
    SKILL_CANDIDATE = "S1"      # 候选 Skill，已识别但未生成
    DRAFT = "S2"                # 草稿，已生成但未验证
    VERIFIED = "S3"             # 已验证，通过测试
    RELEASED = "S4"             # 已发布，可被 Agent 使用
    DEGRADED = "S5"             # 降级，成功率下降
    DEPRECATED = "S6"           # 废弃，不再推荐使用
    ARCHIVED = "S7"             # 归档，历史保留


class SkillType(str, Enum):
    """Skill 类型层级（三层体系）。"""
    ATOMIC = "atomic"           # L1: 原子操作，不可再分（点击、输入等）
    FUNCTIONAL = "functional"   # L2: 功能 Skill，可复用的功能单元（核心层）
    STRATEGIC = "strategic"     # L3: 策略 Skill，高层目标分解与编排


class MetaSkillCategory(str, Enum):
    """Strategic Skill 子分类（管理型元技能）。"""
    LIFECYCLE = "lifecycle"                         # 生命周期管理
    OPTIMIZATION = "optimization"                   # 优化
    QUALITY_ASSURANCE = "quality_assurance"         # 质量保证
    KNOWLEDGE_MANAGEMENT = "knowledge_management"   # 知识管理
    GENERATION = "generation"                       # Skill 生成
    MAINTENANCE = "maintenance"                     # Skill 维护
    GRAPH = "graph"                                 # 图谱管理


class EdgeType(str, Enum):
    """同质图中 Skill 节点之间的边类型。"""
    DEPENDS_ON = "depends_on"           # A 依赖 B（执行 A 前需要 B）
    COMPOSES_WITH = "composes_with"     # A 由 B 组合而成（子组件）
    SIMILAR_TO = "similar_to"           # A 与 B 语义相似
    EVOLVED_FROM = "evolved_from"       # A 从 B 演化而来（版本关系）
    CONFLICTS_WITH = "conflicts_with"   # A 与 B 存在冲突
    REPLACES = "replaces"               # A 替代了 B
    SPECIALIZES = "specializes"         # A 是 B 的特化版本
    GENERALIZES = "generalizes"         # A 是 B 的泛化版本


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class SkillInterface(BaseModel):
    """Skill 的输入/输出接口规范（JSON Schema 格式）。"""
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="输入参数的 JSON Schema",
    )
    output_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="输出结果的 JSON Schema",
    )
    preconditions: List[str] = Field(
        default_factory=list,
        description="执行前必须满足的条件（自然语言描述）",
    )
    postconditions: List[str] = Field(
        default_factory=list,
        description="执行后保证满足的条件（自然语言描述）",
    )
    side_effects: List[str] = Field(
        default_factory=list,
        description="执行的副作用（如修改文件、发送请求等）",
    )


class SkillImplementation(BaseModel):
    """Skill 的具体实现（代码或 LLM prompt）。"""
    language: str = Field(default="python", description="实现语言")
    code: Optional[str] = Field(default=None, description="可执行代码")
    prompt_template: Optional[str] = Field(default=None, description="LLM prompt 模板")
    tool_calls: List[str] = Field(
        default_factory=list,
        description="调用的外部工具列表（工具名称引用）",
    )
    sub_skill_ids: List[str] = Field(
        default_factory=list,
        description="组合 Skill 的子 Skill ID 列表（有序）",
    )
    execution_order: Optional[List[str]] = Field(
        default=None,
        description="子 Skill 执行顺序（支持并行标记）",
    )

    @model_validator(mode="after")
    def validate_implementation(self) -> "SkillImplementation":
        if not self.code and not self.prompt_template and not self.sub_skill_ids:
            raise ValueError("至少需要提供 code、prompt_template 或 sub_skill_ids 之一")
        return self


class SkillTestCase(BaseModel):
    """Skill 的测试用例。"""
    test_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    input_data: Dict[str, Any] = Field(default_factory=dict)
    expected_output: Optional[Dict[str, Any]] = None
    expected_state_changes: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    is_regression: bool = False


class SkillMetrics(BaseModel):
    """Skill 运行时统计指标。"""
    usage_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    avg_latency_ms: float = Field(default=0.0, ge=0.0)
    p95_latency_ms: float = Field(default=0.0, ge=0.0)
    last_used_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_executions(self) -> int:
        return self.success_count + self.failure_count

    @computed_field  # type: ignore[prop-decorator]
    @property
    def successful_executions(self) -> int:
        return self.success_count

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_executions(self) -> int:
        return self.failure_count


class SkillProvenance(BaseModel):
    """Skill 的来源溯源信息。"""
    source_type: str = Field(
        description="来源类型: trajectory | doc | manual | merge | split | adapt"
    )
    source_ids: List[str] = Field(
        default_factory=list,
        description="来源资源 ID（轨迹 ID、文档 ID 等）",
    )
    parent_skill_ids: List[str] = Field(
        default_factory=list,
        description="父 Skill ID（演化/合并来源）",
    )
    created_by_agent: Optional[str] = Field(
        default=None,
        description="创建该 Skill 的 Agent 类型",
    )
    creation_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="创建时的上下文信息",
    )


# ---------------------------------------------------------------------------
# Core Skill Model
# ---------------------------------------------------------------------------

class Skill(BaseModel):
    """SkillOS 核心 Skill 数据模型。

    同质图中的唯一节点类型，包含完整的生命周期状态、接口规范、
    实现细节、测试用例、运行时指标和来源溯源。
    """

    # --- Identity ---
    skill_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="全局唯一 Skill ID",
    )
    name: str = Field(
        min_length=1,
        max_length=128,
        description="Skill 名称（snake_case）",
    )
    version: str = Field(
        default="1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="语义化版本号",
    )
    display_name: str = Field(default="", description="人类可读的展示名称")
    description: str = Field(default="", description="Skill 功能描述")
    tags: List[str] = Field(default_factory=list, description="标签列表")

    # --- Classification ---
    skill_type: SkillType = Field(default=SkillType.ATOMIC)
    meta_category: Optional[MetaSkillCategory] = Field(
        default=None,
        description="仅 Meta Skill 使用",
    )
    domain: str = Field(default="general", description="领域（web, file, api, ...）")
    granularity_level: int = Field(
        default=1,
        ge=1,
        le=5,
        description="粒度级别 1-5（1=最细粒度原子操作，5=高层策略）",
    )

    # --- Lifecycle ---
    state: SkillState = Field(default=SkillState.DRAFT)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    released_at: Optional[datetime] = None
    deprecated_at: Optional[datetime] = None

    # --- Interface ---
    interface: SkillInterface = Field(default_factory=SkillInterface)

    # --- Implementation ---
    implementation: Optional[SkillImplementation] = None

    # --- Tests ---
    test_cases: List[SkillTestCase] = Field(default_factory=list)
    test_trajectory_ids: List[str] = Field(
        default_factory=list,
        description="关联的测试轨迹 ID（存储在 PostgreSQL，非图节点）",
    )

    # --- External References (stored as IDs, not graph nodes) ---
    tool_refs: List[str] = Field(
        default_factory=list,
        description="依赖的外部工具名称列表",
    )
    trajectory_refs: List[str] = Field(
        default_factory=list,
        description="来源轨迹 ID 列表（PostgreSQL 中的 ExperienceUnit）",
    )
    doc_refs: List[str] = Field(
        default_factory=list,
        description="来源文档 ID 列表",
    )

    # --- Metrics ---
    metrics: SkillMetrics = Field(default_factory=SkillMetrics)

    # --- Provenance ---
    provenance: Optional[SkillProvenance] = None

    # --- Graph Relations (stored in Neo4j, referenced here for convenience) ---
    dependency_ids: List[str] = Field(
        default_factory=list,
        description="直接依赖的 Skill ID（depends_on 边）",
    )
    component_ids: List[str] = Field(
        default_factory=list,
        description="组成该 Skill 的子 Skill ID（composes_with 边）",
    )

    # --- Deprecation ---
    deprecation_reason: Optional[str] = None
    replacement_skill_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(f"Skill 名称必须为 snake_case，以小写字母开头: {v!r}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        return [t.strip().lower() for t in v if t.strip()]

    @model_validator(mode="after")
    def validate_meta_category(self) -> "Skill":
        if self.skill_type == SkillType.STRATEGIC and self.meta_category is None:
            raise ValueError("Strategic Skill 必须指定 meta_category")
        if self.skill_type != SkillType.STRATEGIC and self.meta_category is not None:
            raise ValueError("只有 Strategic Skill 才能设置 meta_category")
        return self

    @model_validator(mode="after")
    def set_display_name(self) -> "Skill":
        if not self.display_name:
            self.display_name = self.name.replace("_", " ").title()
        return self

    def transition_to(self, new_state: SkillState) -> None:
        """执行状态转换，验证合法性。"""
        valid_transitions: Dict[SkillState, List[SkillState]] = {
            SkillState.RAW_EXPERIENCE: [SkillState.SKILL_CANDIDATE],
            SkillState.SKILL_CANDIDATE: [SkillState.DRAFT],
            SkillState.DRAFT: [SkillState.VERIFIED, SkillState.SKILL_CANDIDATE],
            SkillState.VERIFIED: [SkillState.RELEASED, SkillState.DRAFT],
            SkillState.RELEASED: [SkillState.DEGRADED, SkillState.DEPRECATED],
            SkillState.DEGRADED: [SkillState.RELEASED, SkillState.DEPRECATED],
            SkillState.DEPRECATED: [SkillState.ARCHIVED],
            SkillState.ARCHIVED: [],
        }
        allowed = valid_transitions.get(self.state, [])
        if new_state not in allowed:
            raise ValueError(
                f"非法状态转换: {self.state.value} → {new_state.value}，"
                f"允许的目标状态: {[s.value for s in allowed]}"
            )
        self.state = new_state
        self.updated_at = datetime.utcnow()
        if new_state == SkillState.RELEASED:
            self.released_at = datetime.utcnow()
        elif new_state == SkillState.DEPRECATED:
            self.deprecated_at = datetime.utcnow()

    def is_usable(self) -> bool:
        """是否可被 Agent 使用（Released 或 Degraded 状态）。"""
        return self.state in (SkillState.RELEASED, SkillState.DEGRADED)

    def bump_version(self, part: str = "patch") -> None:
        """递增版本号。part: major | minor | patch"""
        major, minor, patch = map(int, self.version.split("."))
        if part == "major":
            self.version = f"{major + 1}.0.0"
        elif part == "minor":
            self.version = f"{major}.{minor + 1}.0"
        else:
            self.version = f"{major}.{minor}.{patch + 1}"
        self.updated_at = datetime.utcnow()

    def record_execution(self, success: bool, latency_ms: float) -> None:
        """记录一次执行结果，更新指标。"""
        now = datetime.utcnow()
        self.metrics.usage_count += 1
        self.metrics.last_used_at = now
        if success:
            self.metrics.success_count += 1
            self.metrics.last_success_at = now
        else:
            self.metrics.failure_count += 1
            self.metrics.last_failure_at = now
        # 滑动平均延迟
        n = self.metrics.usage_count
        self.metrics.avg_latency_ms = (
            (self.metrics.avg_latency_ms * (n - 1) + latency_ms) / n
        )
        self.updated_at = now

    def to_graph_node(self) -> Dict[str, Any]:
        """导出为 Neo4j 节点属性字典（仅标量属性）。"""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "skill_type": self.skill_type.value,
            "meta_category": self.meta_category.value if self.meta_category else None,
            "domain": self.domain,
            "granularity_level": self.granularity_level,
            "state": self.state.value,
            "tags": self.tags,
            "success_rate": self.metrics.success_rate,
            "usage_count": self.metrics.usage_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    model_config = {"validate_assignment": True}
