"""Canonical KG schema."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from skillwiki._vendor.skillfabric.registry.models import SkillNode

EdgeType = Literal["similar_to", "compose_with", "depend_on"]
_EDGE_TYPES = {"similar_to", "compose_with", "depend_on"}
_NODE_TYPES = {"skill"}


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Line-level evidence reference for a canonical edge."""

    skill: str
    line: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceRef:
        if set(payload) != {"skill", "line", "text"}:
            raise ValueError("edge evidence must contain exactly skill, line, and text")
        return cls(
            skill=_required_string(payload["skill"], label="evidence skill"),
            line=_positive_integer(payload["line"], label="evidence line"),
            text=_required_string(payload["text"], label="evidence text", strip=False),
        )


@dataclass(slots=True)
class Edge:
    """Canonical KG edge."""

    source: str
    target: str
    type: EdgeType
    confidence: float
    evidence: list[EvidenceRef] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Edge:
        expected = {
            "source",
            "target",
            "type",
            "confidence",
            "evidence",
            "reason",
        }
        if set(payload) != expected:
            raise ValueError("edge must use the canonical semantic fields")
        edge_type = payload["type"]
        if edge_type not in _EDGE_TYPES:
            raise ValueError(f"unsupported edge type: {edge_type}")
        source = _required_string(payload["source"], label="edge source")
        target = _required_string(payload["target"], label="edge target")
        if source == target:
            raise ValueError("edge endpoints must be distinct")
        if edge_type == "similar_to" and source > target:
            raise ValueError("alternative edge endpoints must use canonical id order")
        return cls(
            source=source,
            target=target,
            type=edge_type,
            confidence=_confidence(payload["confidence"]),
            evidence=[
                EvidenceRef.from_dict(item)
                for item in _object_list(payload["evidence"], label="edge evidence")
            ],
            reason=_required_string(payload["reason"], label="edge reason"),
        )


@dataclass(slots=True)
class GraphDocument:
    """Root object serialized to graph.json."""

    build_id: str
    nodes: list[SkillNode]
    edges: list[Edge]

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_id": self.build_id,
            "nodes": [node.to_dict(include_raw_text=False) for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GraphDocument:
        expected = {"build_id", "nodes", "edges"}
        if set(payload) != expected:
            raise ValueError("graph must use the canonical fields")
        nodes: list[SkillNode] = []
        seen_node_ids: set[str] = set()
        for item in _object_list(payload["nodes"], label="graph nodes"):
            node_type = str(item.get("type", ""))
            if node_type not in _NODE_TYPES:
                raise ValueError(f"unsupported node type: {node_type}")
            node = SkillNode.from_dict(item)
            if node.id in seen_node_ids:
                raise ValueError(f"graph contains duplicate node id: {node.id}")
            seen_node_ids.add(node.id)
            nodes.append(node)
        edges = [
            Edge.from_dict(item) for item in _object_list(payload["edges"], label="graph edges")
        ]
        return cls(
            build_id=_required_string(payload["build_id"], label="graph build_id"),
            nodes=nodes,
            edges=edges,
        )


def _object_list(value: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be an object")
    return value


def _required_string(value: Any, *, label: str, strip: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value.strip() if strip else value


def _positive_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("edge confidence must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("edge confidence must be between 0 and 1")
    return result
