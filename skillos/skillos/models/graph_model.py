"""Graph data models.

The original demo only modeled a homogeneous graph of Skill nodes. SkillOS now
uses a heterogeneous graph as the system memory: raw sources, tasks, tools,
APIs, tests, versions, feedback, and extracted Skills can all be first-class
nodes connected by typed relationships.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, field_validator, model_validator

from .skill_model import EdgeType, SkillState, SkillType


class GraphNodeType(str, Enum):
    """Node types used by the SkillOS heterogeneous knowledge graph."""

    SKILL = "skill"
    TASK = "task"
    TRAJECTORY = "trajectory"
    DOCUMENT = "document"
    API_DOC = "api_doc"
    TOOL = "tool"
    SCRIPT = "script"
    TEST = "test"
    VERSION = "version"
    FEEDBACK = "feedback"
    AGENT = "agent"
    DATASET = "dataset"
    HOST_INFORMATION = "host_information"


class GraphRelationType(str, Enum):
    """Relationship types spanning source, Skill, governance, and runtime data."""

    DERIVED_FROM = "derived_from"
    BELONGS_TO = "belongs_to"
    USES = "uses"
    REQUIRES = "requires"
    COMPOSES_WITH = "composes_with"
    VERIFIED_BY = "verified_by"
    EVOLVES_FROM = "evolves_from"
    SIMILAR_TO = "similar_to"
    REPLACES = "replaces"
    PRODUCED_BY = "produced_by"
    TRIGGERED_BY = "triggered_by"
    FEEDS_BACK_TO = "feeds_back_to"
    DOCUMENTS = "documents"
    TESTS = "tests"
    VERSION_OF = "version_of"


class HeterogeneousGraphNode(BaseModel):
    """A first-class node in the SkillOS heterogeneous graph."""

    node_id: str = Field(description="Globally unique graph node ID")
    node_type: GraphNodeType
    name: str = Field(min_length=1, description="Display name")
    description: str = ""
    labels: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Skill-specific fields are optional so source/tool/test/version nodes stay
    # lightweight while the frontend can still render lifecycle and metrics.
    skill_id: Optional[str] = None
    skill_type: Optional[SkillType] = None
    state: Optional[SkillState] = None
    version: Optional[str] = None
    domain: str = "general"
    granularity_level: int = Field(default=1, ge=1, le=5)
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    usage_count: int = Field(default=0, ge=0)
    source_type: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("node_id cannot be empty")
        return cleaned


class HeterogeneousGraphEdge(BaseModel):
    """A typed relationship between any two heterogeneous graph nodes."""

    edge_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    target_id: str
    relation_type: GraphRelationType
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None

    @field_validator("source_id", "target_id")
    @classmethod
    def validate_endpoint_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("edge endpoint IDs cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_no_self_loop(self) -> "HeterogeneousGraphEdge":
        if self.source_id == self.target_id:
            raise ValueError(f"self-loop edges are not allowed: {self.source_id}")
        return self


class HeterogeneousSubgraph(BaseModel):
    """A graph slice containing heterogeneous nodes and edges."""

    subgraph_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    nodes: Dict[str, HeterogeneousGraphNode] = Field(default_factory=dict)
    edges: List[HeterogeneousGraphEdge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def add_node(self, node: HeterogeneousGraphNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: HeterogeneousGraphEdge) -> None:
        if edge.source_id not in self.nodes or edge.target_id not in self.nodes:
            raise ValueError(
                f"edge endpoints are not both present in the subgraph: "
                f"{edge.source_id} -> {edge.target_id}"
            )
        self.edges.append(edge)


# ---------------------------------------------------------------------------
# Graph Edge
# ---------------------------------------------------------------------------

class SkillEdge(BaseModel):
    """同质图中两个 Skill 节点之间的有向边。"""

    edge_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str = Field(description="源 Skill ID")
    target_id: str = Field(description="目标 Skill ID")
    edge_type: EdgeType

    # 边属性
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="边权重（相似度/依赖强度）")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="关系置信度")
    description: str = Field(default="", description="关系描述")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(default=None, description="创建该边的 Agent 类型")

    @field_validator("source_id", "target_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("节点 ID 不能为空")
        return v.strip()

    @model_validator(mode="after")
    def validate_no_self_loop(self) -> "SkillEdge":
        if self.source_id == self.target_id:
            raise ValueError(f"不允许自环边: {self.source_id}")
        return self

    def to_neo4j_props(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "weight": self.weight,
            "confidence": self.confidence,
            "description": self.description,
            "metadata": json.dumps(self.metadata),
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }


# ---------------------------------------------------------------------------
# Graph Node (lightweight wrapper for graph operations)
# ---------------------------------------------------------------------------

class SkillGraphNode(BaseModel):
    """图操作用的轻量节点视图（不含完整 Skill 数据）。"""

    skill_id: str
    name: str
    version: str
    skill_type: SkillType
    state: SkillState
    domain: str = "general"
    granularity_level: int = 1
    success_rate: float = 0.0
    usage_count: int = 0
    tags: List[str] = Field(default_factory=list)

    # 图拓扑（由图查询填充）
    out_edges: List[SkillEdge] = Field(default_factory=list)
    in_edges: List[SkillEdge] = Field(default_factory=list)

    @property
    def degree(self) -> int:
        return len(self.out_edges) + len(self.in_edges)

    @property
    def out_degree(self) -> int:
        return len(self.out_edges)

    @property
    def in_degree(self) -> int:
        return len(self.in_edges)

    def get_neighbors(self, edge_type: Optional[EdgeType] = None) -> List[str]:
        """获取邻居节点 ID 列表（可按边类型过滤）。"""
        edges = self.out_edges + self.in_edges
        if edge_type:
            edges = [e for e in edges if e.edge_type == edge_type]
        neighbor_ids: Set[str] = set()
        for e in edges:
            if e.source_id != self.skill_id:
                neighbor_ids.add(e.source_id)
            if e.target_id != self.skill_id:
                neighbor_ids.add(e.target_id)
        return list(neighbor_ids)


# ---------------------------------------------------------------------------
# Subgraph
# ---------------------------------------------------------------------------

class SkillSubgraph(BaseModel):
    """子图：一组节点和边的集合，用于局部图操作。"""

    subgraph_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(default="")
    nodes: Dict[str, SkillGraphNode] = Field(default_factory=dict)
    edges: List[SkillEdge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def add_node(self, node: SkillGraphNode) -> None:
        self.nodes[node.skill_id] = node

    def add_edge(self, edge: SkillEdge) -> None:
        if edge.source_id not in self.nodes or edge.target_id not in self.nodes:
            raise ValueError(
                f"边的端点不在子图中: {edge.source_id} → {edge.target_id}"
            )
        self.edges.append(edge)

    def get_roots(self) -> List[str]:
        """返回入度为 0 的节点 ID（无依赖的 Skill）。"""
        has_incoming = {e.target_id for e in self.edges}
        return [nid for nid in self.nodes if nid not in has_incoming]

    def get_leaves(self) -> List[str]:
        """返回出度为 0 的节点 ID（不被其他 Skill 依赖）。"""
        has_outgoing = {e.source_id for e in self.edges}
        return [nid for nid in self.nodes if nid not in has_outgoing]

    def topological_sort(self) -> List[str]:
        """拓扑排序（Kahn 算法），用于确定执行顺序。"""
        in_degree: Dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: Dict[str, List[str]] = {nid: [] for nid in self.nodes}

        for edge in self.edges:
            if edge.edge_type == EdgeType.DEPENDS_ON:
                adj[edge.target_id].append(edge.source_id)
                in_degree[edge.source_id] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self.nodes):
            raise ValueError("图中存在环，无法进行拓扑排序")
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subgraph_id": self.subgraph_id,
            "name": self.name,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": [n.model_dump() for n in self.nodes.values()],
            "edges": [e.model_dump() for e in self.edges],
        }


# ---------------------------------------------------------------------------
# Graph Statistics
# ---------------------------------------------------------------------------

class GraphStats(BaseModel):
    """全图统计信息。"""

    total_nodes: int = 0
    total_edges: int = 0

    # 节点类型分布
    atomic_count: int = 0
    composite_count: int = 0
    meta_count: int = 0

    # 状态分布
    state_distribution: Dict[str, int] = Field(default_factory=dict)

    # 边类型分布
    edge_type_distribution: Dict[str, int] = Field(default_factory=dict)

    # 图结构指标
    avg_degree: float = 0.0
    max_degree: int = 0
    density: float = 0.0         # 实际边数 / 最大可能边数
    connected_components: int = 0

    # 质量指标
    avg_success_rate: float = 0.0
    total_usage_count: int = 0

    computed_at: datetime = Field(default_factory=datetime.utcnow)
