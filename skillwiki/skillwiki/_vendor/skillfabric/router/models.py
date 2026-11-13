"""Canonical query-bundle and route-result models."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skillwiki._vendor.skillfabric.graph.models import Edge


@dataclass(frozen=True, slots=True)
class RouterBundleConfig:
    """Bounds for retrieval and semantic graph expansion."""

    workspace: str | Path = ".skillfabric"
    query: str = ""
    env_file: str | Path | None = ".env"
    seed_limit: int = 24
    expanded_limit: int = 100
    max_depth: int = 2

    def __post_init__(self) -> None:
        _nonnegative_integer(self.seed_limit, label="seed_limit")
        _nonnegative_integer(self.max_depth, label="max_depth")
        if (
            isinstance(self.expanded_limit, bool)
            or not isinstance(self.expanded_limit, int)
            or self.expanded_limit < self.seed_limit
        ):
            raise ValueError("expanded_limit must be an integer at least seed_limit")


@dataclass(frozen=True, slots=True)
class ExpansionStep:
    """One traversed semantic edge in an expansion path."""

    source: str
    target: str
    edge_type: str
    semantic_source: str
    semantic_target: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "semantic_source": self.semantic_source,
            "semantic_target": self.semantic_target,
            "confidence": round(float(self.confidence), 6),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExpansionStep:
        _require_exact_keys(
            payload,
            {
                "source",
                "target",
                "edge_type",
                "semantic_source",
                "semantic_target",
                "confidence",
                "reason",
            },
            label="expansion step",
        )
        return cls(
            source=_required_string(payload["source"], label="expansion source"),
            target=_required_string(payload["target"], label="expansion target"),
            edge_type=_required_string(payload["edge_type"], label="expansion edge_type"),
            semantic_source=_required_string(
                payload["semantic_source"],
                label="expansion semantic_source",
            ),
            semantic_target=_required_string(
                payload["semantic_target"],
                label="expansion semantic_target",
            ),
            confidence=_number(payload["confidence"], label="expansion confidence"),
            reason=_required_string(payload["reason"], label="expansion reason"),
        )


@dataclass(frozen=True, slots=True)
class ExpansionPath:
    seed_skill: str
    steps: tuple[ExpansionStep, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_skill": self.seed_skill,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExpansionPath:
        _require_exact_keys(payload, {"seed_skill", "steps"}, label="expansion path")
        return cls(
            seed_skill=_required_string(payload["seed_skill"], label="expansion seed_skill"),
            steps=tuple(
                ExpansionStep.from_dict(item)
                for item in _object_list(payload["steps"], label="expansion steps")
            ),
        )


@dataclass(frozen=True, slots=True)
class RouterSkillCandidate:
    """One seed or semantically expanded skill shown to the explorer."""

    skill_id: str
    name: str
    score: float
    is_seed: bool = False
    retrieval_ranks: dict[str, int] = field(default_factory=dict)
    graph_depth: int = 0
    introduced_by: tuple[ExpansionPath, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "score": round(float(self.score), 8),
            "is_seed": self.is_seed,
            "retrieval_ranks": dict(sorted(self.retrieval_ranks.items())),
            "graph_depth": self.graph_depth,
            "introduced_by": [path.to_dict() for path in self.introduced_by],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouterSkillCandidate:
        _require_exact_keys(
            payload,
            {
                "skill_id",
                "name",
                "score",
                "is_seed",
                "retrieval_ranks",
                "graph_depth",
                "introduced_by",
            },
            label="router skill candidate",
        )
        retrieval_ranks = payload["retrieval_ranks"]
        if not isinstance(retrieval_ranks, dict):
            raise ValueError("retrieval_ranks must be an object")
        if isinstance(payload["is_seed"], bool):
            is_seed = payload["is_seed"]
        else:
            raise ValueError("is_seed must be a boolean")
        graph_depth = payload["graph_depth"]
        if isinstance(graph_depth, bool) or not isinstance(graph_depth, int) or graph_depth < 0:
            raise ValueError("graph_depth must be a non-negative integer")
        return cls(
            skill_id=_required_string(payload["skill_id"], label="candidate skill_id"),
            name=_required_string(payload["name"], label="candidate name"),
            score=_number(payload["score"], label="candidate score"),
            is_seed=is_seed,
            retrieval_ranks={
                _required_string(key, label="retrieval channel"): _positive_integer(
                    value,
                    label=f"retrieval rank {key}",
                )
                for key, value in retrieval_ranks.items()
            },
            graph_depth=graph_depth,
            introduced_by=tuple(
                ExpansionPath.from_dict(item)
                for item in _object_list(payload["introduced_by"], label="introduced_by")
            ),
        )


@dataclass(frozen=True, slots=True)
class RouterAlternative:
    skill_id: str
    name: str
    alternative_to: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "alternative_to": self.alternative_to,
            "confidence": round(float(self.confidence), 6),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouterAlternative:
        _require_exact_keys(
            payload,
            {"skill_id", "name", "alternative_to", "confidence", "reason"},
            label="router alternative",
        )
        return cls(
            skill_id=_required_string(payload["skill_id"], label="alternative skill_id"),
            name=_required_string(payload["name"], label="alternative name"),
            alternative_to=_required_string(
                payload["alternative_to"],
                label="alternative_to",
            ),
            confidence=_number(payload["confidence"], label="alternative confidence"),
            reason=_required_string(payload["reason"], label="alternative reason"),
        )


@dataclass(frozen=True, slots=True)
class RouterBundle:
    """Evidence bundle consumed by Task Wiki exploration."""

    query: str
    selected_skills: tuple[RouterSkillCandidate, ...]
    graph_edges: tuple[Edge, ...]
    alternatives: tuple[RouterAlternative, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "selected_skills": [item.to_dict() for item in self.selected_skills],
            "graph_edges": [edge.to_dict() for edge in self.graph_edges],
            "alternatives": [item.to_dict() for item in self.alternatives],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouterBundle:
        _require_exact_keys(
            payload,
            {"query", "selected_skills", "graph_edges", "alternatives"},
            label="router bundle",
        )
        return cls(
            query=_required_string(payload["query"], label="router bundle query"),
            selected_skills=tuple(
                RouterSkillCandidate.from_dict(item)
                for item in _object_list(payload["selected_skills"], label="selected_skills")
            ),
            graph_edges=tuple(
                Edge.from_dict(item)
                for item in _object_list(payload["graph_edges"], label="graph_edges")
            ),
            alternatives=tuple(
                RouterAlternative.from_dict(item)
                for item in _object_list(payload["alternatives"], label="alternatives")
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteSelectedSkill:
    skill_id: str
    name: str
    reason: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouteSelectedSkill:
        _require_exact_keys(
            payload,
            {"skill_id", "name", "reason", "evidence"},
            label="selected route skill",
        )
        return cls(
            skill_id=_required_string(payload["skill_id"], label="selected skill_id"),
            name=_required_string(payload["name"], label="selected skill name"),
            reason=_required_string(payload["reason"], label="selected skill reason"),
            evidence=tuple(_string_list(payload["evidence"], label="selected evidence")),
        )


@dataclass(frozen=True, slots=True)
class RouteRelationEvidence:
    relation_type: str
    source_skill: str
    target_skill: str
    confidence: float
    reason: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_type": self.relation_type,
            "source_skill": self.source_skill,
            "target_skill": self.target_skill,
            "confidence": round(float(self.confidence), 6),
            "reason": self.reason,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouteRelationEvidence:
        _require_exact_keys(
            payload,
            {
                "relation_type",
                "source_skill",
                "target_skill",
                "confidence",
                "reason",
                "evidence",
            },
            label="route relation evidence",
        )
        return cls(
            relation_type=_required_string(
                payload["relation_type"],
                label="relation_type",
            ),
            source_skill=_required_string(
                payload["source_skill"],
                label="source_skill",
            ),
            target_skill=_required_string(
                payload["target_skill"],
                label="target_skill",
            ),
            confidence=_number(payload["confidence"], label="relation confidence"),
            reason=_required_string(payload["reason"], label="relation reason"),
            evidence=tuple(_string_list(payload["evidence"], label="relation evidence")),
        )


@dataclass(frozen=True, slots=True)
class RouteNearMiss:
    skill_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"skill_id": self.skill_id, "reason": self.reason}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouteNearMiss:
        _require_exact_keys(payload, {"skill_id", "reason"}, label="route near miss")
        return cls(
            skill_id=_required_string(payload["skill_id"], label="near miss skill_id"),
            reason=_required_string(payload["reason"], label="near miss reason"),
        )


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Minimal route contract consumed by planner packaging."""

    selected_skills: tuple[RouteSelectedSkill, ...]
    relation_evidence: tuple[RouteRelationEvidence, ...]
    near_misses: tuple[RouteNearMiss, ...]
    coverage_gaps: tuple[str, ...]
    wiki_pages_read: tuple[str, ...]
    rationale: str

    @property
    def selected_skill_ids(self) -> list[str]:
        return [item.skill_id for item in self.selected_skills]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_skills": [item.to_dict() for item in self.selected_skills],
            "relation_evidence": [item.to_dict() for item in self.relation_evidence],
            "near_misses": [item.to_dict() for item in self.near_misses],
            "coverage_gaps": list(self.coverage_gaps),
            "wiki_pages_read": list(self.wiki_pages_read),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RouteResult:
        expected = {
            "selected_skills",
            "relation_evidence",
            "near_misses",
            "coverage_gaps",
            "wiki_pages_read",
            "rationale",
        }
        if set(payload) != expected:
            raise ValueError("route must use the canonical route fields")
        return cls(
            selected_skills=tuple(
                RouteSelectedSkill.from_dict(item)
                for item in _object_list(payload["selected_skills"], label="selected_skills")
            ),
            relation_evidence=tuple(
                RouteRelationEvidence.from_dict(item)
                for item in _object_list(
                    payload["relation_evidence"],
                    label="relation_evidence",
                )
            ),
            near_misses=tuple(
                RouteNearMiss.from_dict(item)
                for item in _object_list(payload["near_misses"], label="near_misses")
            ),
            coverage_gaps=tuple(_string_list(payload["coverage_gaps"], label="coverage_gaps")),
            wiki_pages_read=tuple(
                _string_list(payload["wiki_pages_read"], label="wiki_pages_read")
            ),
            rationale=_required_string(payload["rationale"], label="rationale"),
        )


def _require_exact_keys(payload: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(payload) != expected:
        raise ValueError(f"{label} must use exactly: {', '.join(sorted(expected))}")


def _object_list(value: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be an object")
    return value


def _string_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return [_required_string(item, label=f"{label}[{index}]") for index, item in enumerate(value)]


def _required_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _positive_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value
