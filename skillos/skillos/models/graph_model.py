"""同质图数据模型 — 仅 Skill 节点，类型化边。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, field_validator, model_validator

from .skill_model import EdgeType, SkillState, SkillType


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


# ---------------------------------------------------------------------------
# Heterogeneous Graph
# ---------------------------------------------------------------------------

class HeteroNodeKind(str, Enum):
    SOURCE = "source"
    SKILL = "skill"
    EXECUTION = "execution"
    VALIDATION = "validation"
    VERSION = "version"


GraphNodeKind = HeteroNodeKind


class HeteroEdgeType(str, Enum):
    DERIVED_FROM = "derived_from"
    EXECUTED_AS = "executed_as"
    VALIDATED_BY = "validated_by"
    VERSIONED_AS = "versioned_as"
    COMPOSES_WITH = "composes_with"


class HeteroGraphNode(BaseModel):
    node_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    node_kind: HeteroNodeKind
    name: str
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(default=None)

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("节点 ID 不能为空")
        return value.strip()


class SourceGraphNode(HeteroGraphNode):
    node_kind: HeteroNodeKind = Field(default=HeteroNodeKind.SOURCE)
    source_uri: str = ""
    source_type: str = "trajectory"


class HeteroSkillNode(HeteroGraphNode):
    node_kind: HeteroNodeKind = Field(default=HeteroNodeKind.SKILL)
    skill_id: str = ""
    skill_version: str = "1.0.0"
    skill_state: str = "S2"


class ExecutionGraphNode(HeteroGraphNode):
    node_kind: HeteroNodeKind = Field(default=HeteroNodeKind.EXECUTION)
    execution_id: str = ""
    status: str = "completed"
    skill_ref: Optional[str] = None


class ValidationGraphNode(HeteroGraphNode):
    node_kind: HeteroNodeKind = Field(default=HeteroNodeKind.VALIDATION)
    validation_id: str = ""
    outcome: str = "passed"
    validator: str = "system"


class VersionGraphNode(HeteroGraphNode):
    node_kind: HeteroNodeKind = Field(default=HeteroNodeKind.VERSION)
    version_id: str = ""
    version_label: str = "v1.0.0"
    release_state: str = "draft"


class HeteroGraphEdge(BaseModel):
    edge_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str = Field(description="源节点 ID")
    target_id: str = Field(description="目标节点 ID")
    edge_type: HeteroEdgeType
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(default=None)

    @field_validator("source_id", "target_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("节点 ID 不能为空")
        return value.strip()

    @model_validator(mode="after")
    def validate_no_self_loop(self) -> "HeteroGraphEdge":
        if self.source_id == self.target_id:
            raise ValueError(f"不允许自环边: {self.source_id}")
        return self


class HeteroGraph(BaseModel):
    graph_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "heterogeneous"
    nodes: Dict[str, HeteroGraphNode] = Field(default_factory=dict)
    edges: List[HeteroGraphEdge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def add_node(self, node: HeteroGraphNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: HeteroGraphEdge) -> None:
        if edge.source_id not in self.nodes or edge.target_id not in self.nodes:
            raise ValueError(
                f"边的端点不在异构图中: {edge.source_id} -> {edge.target_id}"
            )
        self.edges = [existing for existing in self.edges if existing.edge_id != edge.edge_id]
        self.edges.append(edge)

    def get_node_kinds(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.node_kind.value] = counts.get(node.node_kind.value, 0) + 1
        return counts

    def get_edge_types(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for edge in self.edges:
            counts[edge.edge_type.value] = counts.get(edge.edge_type.value, 0) + 1
        return counts

    def get_stats(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "name": self.name,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "node_kind_distribution": self.get_node_kinds(),
            "edge_type_distribution": self.get_edge_types(),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "name": self.name,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": [node.model_dump() for node in self.nodes.values()],
            "edges": [edge.model_dump() for edge in self.edges],
            "stats": self.get_stats(),
        }


def build_demo_hetero_graph(
    *,
    fill_form_skill_id: str = "fill_form",
    fill_form_skill_version: str = "1.0.0",
) -> HeteroGraph:
    graph = HeteroGraph(name="heterogeneous-demo")

    source = SourceGraphNode(
        node_id="source_demo_trajectory",
        name="source_demo_trajectory",
        description="Original browser trajectory used to derive the fill_form Skill.",
        source_uri="trajectory://source_demo_trajectory",
        source_type="browser_trajectory",
        metadata={"demo": True},
    )
    skill = HeteroSkillNode(
        node_id="fill_form",
        name="fill_form",
        description="Reusable Skill extracted from the source trajectory.",
        skill_id=fill_form_skill_id,
        skill_version=fill_form_skill_version,
        skill_state="S4",
        metadata={"demo": True},
    )
    execution = ExecutionGraphNode(
        node_id="execution_demo",
        name="execution_demo",
        description="Recorded execution instance for the fill_form Skill.",
        execution_id="execution_demo",
        status="completed",
        skill_ref=skill.node_id,
        metadata={"demo": True},
    )
    validation = ValidationGraphNode(
        node_id="validation_demo",
        name="validation_demo",
        description="Validation summary for the execution.",
        validation_id="validation_demo",
        outcome="passed",
        validator="demo-verifier",
        metadata={"demo": True},
    )
    version = VersionGraphNode(
        node_id="version_demo",
        name="version_demo",
        description="Released Skill version tied to the validation result.",
        version_id="version_demo",
        version_label=fill_form_skill_version,
        release_state="released",
        metadata={"demo": True},
    )

    for node in [source, skill, execution, validation, version]:
        graph.add_node(node)

    for edge in [
        HeteroGraphEdge(
            edge_id="demo-hetero-derived-from",
            source_id=source.node_id,
            target_id=skill.node_id,
            edge_type=HeteroEdgeType.DERIVED_FROM,
            metadata={"demo": True},
        ),
        HeteroGraphEdge(
            edge_id="demo-hetero-executed-as",
            source_id=skill.node_id,
            target_id=execution.node_id,
            edge_type=HeteroEdgeType.EXECUTED_AS,
            metadata={"demo": True},
        ),
        HeteroGraphEdge(
            edge_id="demo-hetero-validated-by",
            source_id=execution.node_id,
            target_id=validation.node_id,
            edge_type=HeteroEdgeType.VALIDATED_BY,
            metadata={"demo": True},
        ),
        HeteroGraphEdge(
            edge_id="demo-hetero-versioned-as",
            source_id=validation.node_id,
            target_id=version.node_id,
            edge_type=HeteroEdgeType.VERSIONED_AS,
            metadata={"demo": True},
        ),
        HeteroGraphEdge(
            edge_id="demo-hetero-composes-with",
            source_id=version.node_id,
            target_id=skill.node_id,
            edge_type=HeteroEdgeType.COMPOSES_WITH,
            metadata={"demo": True},
        ),
    ]:
        graph.add_edge(edge)

    return graph
