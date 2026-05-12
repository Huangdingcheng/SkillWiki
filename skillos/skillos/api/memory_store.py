"""In-memory Wiki, Graph, and Search managers for local demo mode."""

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from ..layers.skill_repository.indexing import SearchQuery, SearchResult, rank_search_results
from ..models.graph_model import SkillEdge, SkillGraphNode, SkillSubgraph
from ..models.skill_model import EdgeType, Skill, SkillState, SkillType
from ..utils.logger import get_logger

logger = get_logger(__name__)


class MemoryWikiManager:
    """Memory implementation of the SkillWikiManager interface."""

    def __init__(self) -> None:
        self._store: Dict[str, Skill] = {}

    async def get(self, skill_id: str) -> Optional[Skill]:
        return self._store.get(skill_id)

    async def get_by_name(self, name: str, version: Optional[str] = None) -> Optional[Skill]:
        matches = [skill for skill in self._store.values() if skill.name == name]
        if not matches:
            return None
        if version:
            return next((skill for skill in matches if skill.version == version), None)
        released = [skill for skill in matches if skill.state == SkillState.RELEASED]
        return max(released or matches, key=lambda skill: skill.updated_at)

    async def get_many(self, skill_ids: List[str]) -> Dict[str, Optional[Skill]]:
        return {skill_id: self._store.get(skill_id) for skill_id in skill_ids}

    async def list(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
        tags: Optional[List[str]] = None,
        domain: Optional[str] = None,
        name_like: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Skill]:
        skills = sorted(self._store.values(), key=lambda skill: skill.updated_at, reverse=True)
        if skill_type:
            skills = [skill for skill in skills if skill.skill_type == skill_type]
        if state:
            skills = [skill for skill in skills if skill.state == state]
        if tags:
            tag_set = {tag.strip().lower() for tag in tags if tag.strip()}
            skills = [skill for skill in skills if tag_set & set(skill.tags)]
        if domain:
            skills = [skill for skill in skills if skill.domain == domain]
        if name_like:
            needle = name_like.lower()
            skills = [skill for skill in skills if needle in skill.name.lower()]
        return skills[offset : offset + limit]

    async def search_by_tags(self, tags: List[str], limit: int = 50) -> List[Skill]:
        tag_set = {tag.strip().lower() for tag in tags if tag.strip()}
        return [
            skill for skill in self._store.values()
            if tag_set & set(skill.tags)
        ][:limit]

    async def count(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
    ) -> int:
        return len(await self.list(skill_type=skill_type, state=state, limit=10000))

    async def create(self, skill: Skill) -> Skill:
        existing = await self.get_by_name(skill.name, skill.version)
        if existing:
            raise ValueError(f"Skill '{skill.name}' v{skill.version} already exists")
        now = datetime.utcnow()
        created = skill.model_copy(deep=True)
        if not created.created_at:
            object.__setattr__(created, "created_at", now)
        object.__setattr__(created, "updated_at", created.updated_at or now)
        self._store[created.skill_id] = created
        logger.info("Skill created: %s v%s", created.name, created.version)
        return created

    async def update(self, skill_id: str, **kwargs: Any) -> Optional[Skill]:
        skill = self._store.get(skill_id)
        if not skill:
            return None
        updated = skill.model_copy(deep=True)
        for key, value in kwargs.items():
            if key in {"skill_id", "created_at"}:
                continue
            object.__setattr__(updated, key, value)
        object.__setattr__(updated, "updated_at", datetime.utcnow())
        self._store[skill_id] = updated
        return updated

    async def delete(self, skill_id: str) -> bool:
        return self._store.pop(skill_id, None) is not None

    async def create_new_version(
        self,
        source_skill_id: str,
        bump: str = "patch",
        **overrides: Any,
    ) -> Skill:
        source = await self.get(source_skill_id)
        if not source:
            raise ValueError(f"Source Skill does not exist: {source_skill_id}")
        new_skill = source.model_copy(deep=True)
        object.__setattr__(new_skill, "skill_id", str(uuid.uuid4()))
        new_skill.bump_version(bump)
        object.__setattr__(new_skill, "state", SkillState.DRAFT)
        object.__setattr__(new_skill, "created_at", datetime.utcnow())
        object.__setattr__(new_skill, "updated_at", datetime.utcnow())
        object.__setattr__(new_skill, "released_at", None)
        object.__setattr__(new_skill, "deprecated_at", None)
        for key, value in overrides.items():
            object.__setattr__(new_skill, key, value)
        return await self.create(new_skill)

    async def get_version_history(self, name: str) -> List[Skill]:
        exact = [skill for skill in self._store.values() if skill.name == name]
        return sorted(exact, key=lambda skill: _version_key(skill.version))

    async def transition_state(
        self,
        skill_id: str,
        new_state: SkillState,
        reason: Optional[str] = None,
    ) -> Skill:
        skill = await self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill does not exist: {skill_id}")
        skill.transition_to(new_state)
        if new_state == SkillState.DEPRECATED and reason:
            object.__setattr__(skill, "deprecation_reason", reason)
        self._store[skill_id] = skill
        return skill

    async def release(self, skill_id: str) -> Skill:
        return await self.transition_state(skill_id, SkillState.RELEASED)

    async def deprecate(
        self,
        skill_id: str,
        reason: str = "",
        replacement_id: Optional[str] = None,
    ) -> Skill:
        skill = await self.transition_state(skill_id, SkillState.DEPRECATED, reason=reason)
        if replacement_id:
            object.__setattr__(skill, "replacement_skill_id", replacement_id)
        return skill

    async def record_execution(self, skill_id: str, success: bool, latency_ms: float) -> None:
        skill = self._store.get(skill_id)
        if skill:
            skill.record_execution(success, latency_ms)

    async def get_overview_stats(self) -> Dict[str, Any]:
        skills = list(self._store.values())
        by_state: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        total_exec = 0
        success_rates: List[float] = []
        for skill in skills:
            by_state[skill.state.value] = by_state.get(skill.state.value, 0) + 1
            by_type[skill.skill_type.value] = by_type.get(skill.skill_type.value, 0) + 1
            total_exec += skill.metrics.total_executions
            if skill.metrics.total_executions >= 5:
                success_rates.append(skill.metrics.success_rate)
        return {
            "total_skills": len(skills),
            "by_state": by_state,
            "by_type": by_type,
            "total_executions": total_exec,
            "avg_success_rate": (
                sum(success_rates) / len(success_rates) if success_rates else 1.0
            ),
            "graph_stats": {},
        }

    async def invalidate(self, skill_id: str) -> None:
        return None


class MemoryGraphManager:
    """Memory implementation of the SkillGraphManager interface."""

    def __init__(self) -> None:
        self._nodes: Dict[str, SkillGraphNode] = {}
        self._edges: List[SkillEdge] = []

    async def sync_skill(self, skill: Skill) -> None:
        self._nodes[skill.skill_id] = _skill_to_graph_node(skill)

    async def sync_auto_edges(self, skill: Skill, valid_skill_ids: Iterable[str]) -> None:
        valid_ids = set(valid_skill_ids)
        self._remove_auto_edges_from(skill.skill_id, {
            EdgeType.COMPOSES_WITH,
            EdgeType.EVOLVED_FROM,
        })

        if not skill.implementation:
            sub_skill_ids: List[str] = []
        else:
            sub_skill_ids = skill.implementation.sub_skill_ids
        for child_id in _unique_ids(sub_skill_ids):
            if child_id == skill.skill_id or child_id not in valid_ids:
                logger.warning("Skip auto graph edge with missing child Skill: %s -> %s", skill.skill_id, child_id)
                continue
            await self.create_edge(_auto_edge(
                source_id=skill.skill_id,
                target_id=child_id,
                edge_type=EdgeType.COMPOSES_WITH,
            ))

        parent_ids = skill.provenance.parent_skill_ids if skill.provenance else []
        for parent_id in _unique_ids(parent_ids):
            if parent_id == skill.skill_id or parent_id not in valid_ids:
                logger.warning("Skip auto graph edge with missing parent Skill: %s -> %s", skill.skill_id, parent_id)
                continue
            await self.create_edge(_auto_edge(
                source_id=skill.skill_id,
                target_id=parent_id,
                edge_type=EdgeType.EVOLVED_FROM,
            ))

    async def remove_skill(self, skill_id: str) -> None:
        self._nodes.pop(skill_id, None)
        self._edges = [
            edge for edge in self._edges
            if edge.source_id != skill_id and edge.target_id != skill_id
        ]

    def _remove_auto_edges_from(self, source_id: str, edge_types: Set[EdgeType]) -> None:
        self._edges = [
            edge for edge in self._edges
            if not (
                edge.source_id == source_id
                and edge.edge_type in edge_types
                and edge.metadata.get("auto_generated") is True
                and edge.metadata.get("source") == "skill_repository"
            )
        ]

    async def create_edge(self, edge: SkillEdge) -> None:
        self._edges = [existing for existing in self._edges if existing.edge_id != edge.edge_id]
        self._edges.append(edge)

    async def get_subgraph(
        self,
        skill_ids: Optional[List[str]] = None,
        depth: int = 2,
    ) -> SkillSubgraph:
        roots = list(skill_ids or [])
        if not roots:
            roots = list(self._nodes)
        visited = set(roots)
        frontier = set(roots)
        selected_edges: List[SkillEdge] = []

        for _ in range(max(depth, 1)):
            next_frontier: Set[str] = set()
            for edge in self._edges:
                touches_frontier = edge.source_id in frontier or edge.target_id in frontier
                if not touches_frontier:
                    continue
                selected_edges.append(edge)
                for node_id in (edge.source_id, edge.target_id):
                    if node_id not in visited:
                        visited.add(node_id)
                        next_frontier.add(node_id)
            frontier = next_frontier
            if not frontier:
                break

        subgraph = SkillSubgraph()
        for node_id in visited:
            node = self._nodes.get(node_id)
            if node:
                subgraph.add_node(node)
        # The API can enrich nodes from Wiki, so memory mode must not drop edges
        # just because a caller has not explicitly synced graph nodes yet.
        subgraph.edges = [
            edge for edge in _dedupe_edges(selected_edges)
            if edge.source_id in visited and edge.target_id in visited
        ]
        return subgraph

    async def get_dependency_chain(self, skill_id: str) -> List[str]:
        result: List[str] = []
        seen: Set[str] = set()

        def visit(current_id: str) -> None:
            for edge in self._edges:
                if edge.edge_type != EdgeType.DEPENDS_ON or edge.source_id != current_id:
                    continue
                if edge.target_id in seen:
                    continue
                seen.add(edge.target_id)
                visit(edge.target_id)
                result.append(edge.target_id)

        visit(skill_id)
        return result

    async def get_execution_order(self, skill_ids: Any) -> List[str]:
        ids = [skill_ids] if isinstance(skill_ids, str) else list(skill_ids)
        if len(ids) <= 1:
            return ids

        id_set = set(ids)
        in_degree = {skill_id: 0 for skill_id in ids}
        dependents = {skill_id: [] for skill_id in ids}
        for edge in self._edges:
            if edge.edge_type != EdgeType.DEPENDS_ON:
                continue
            if edge.source_id in id_set and edge.target_id in id_set:
                in_degree[edge.source_id] += 1
                dependents[edge.target_id].append(edge.source_id)

        queue = deque([skill_id for skill_id in ids if in_degree[skill_id] == 0])
        ordered: List[str] = []
        while queue:
            current = queue.popleft()
            ordered.append(current)
            for dependent in dependents[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return ordered if len(ordered) == len(ids) else ids

    async def add_evolution(self, new_id: str, old_id: str) -> None:
        await self.create_edge(SkillEdge(
            source_id=new_id,
            target_id=old_id,
            edge_type=EdgeType.EVOLVED_FROM,
            weight=1.0,
        ))

    async def add_dependency(
        self,
        source_id: str,
        target_id: str,
        weight: float = 1.0,
    ) -> None:
        await self.create_edge(SkillEdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=EdgeType.DEPENDS_ON,
            weight=weight,
        ))

    async def add_composition(
        self,
        parent_id: str,
        child_id: str,
        weight: float = 1.0,
    ) -> None:
        await self.create_edge(SkillEdge(
            source_id=parent_id,
            target_id=child_id,
            edge_type=EdgeType.COMPOSES_WITH,
            weight=weight,
        ))

    async def get_stats(self) -> Dict[str, Any]:
        edge_types: Dict[str, int] = {}
        node_ids = set(self._nodes)
        for edge in self._edges:
            node_ids.add(edge.source_id)
            node_ids.add(edge.target_id)
            edge_types[edge.edge_type.value] = edge_types.get(edge.edge_type.value, 0) + 1
        return {
            "nodes": len(node_ids),
            "edges": len(self._edges),
            "edge_type_distribution": edge_types,
        }

    async def find_merge_candidates(self, threshold: float = 0.85) -> List:
        return [
            (edge.source_id, edge.target_id, edge.weight)
            for edge in self._edges
            if edge.edge_type == EdgeType.SIMILAR_TO and edge.weight >= threshold
        ]

    async def detect_cycles(self) -> List[List[str]]:
        return []

    async def get_central_skills(self, top_k: int = 10) -> List[str]:
        degrees: Dict[str, int] = {node_id: 0 for node_id in self._nodes}
        for edge in self._edges:
            degrees[edge.source_id] = degrees.get(edge.source_id, 0) + 1
            degrees[edge.target_id] = degrees.get(edge.target_id, 0) + 1
        return [
            skill_id for skill_id, _ in sorted(
                degrees.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:top_k]
        ]


class MemorySearchEngine:
    """Keyword-based search engine for memory demo mode."""

    def __init__(self, wiki: MemoryWikiManager) -> None:
        self._wiki = wiki

    async def search(self, query: SearchQuery) -> List[SearchResult]:
        skills = await self._wiki.list(
            skill_type=query.skill_type,
            state=query.state,
            domain=query.domain,
            tags=query.tags or None,
            limit=10000,
        )
        return rank_search_results(skills, query)


def _version_key(version: str) -> List[int]:
    try:
        return [int(part) for part in version.split(".")]
    except ValueError:
        return [0, 0, 0]


def _skill_to_graph_node(skill: Skill) -> SkillGraphNode:
    return SkillGraphNode(
        skill_id=skill.skill_id,
        name=skill.name,
        version=skill.version,
        skill_type=skill.skill_type,
        state=skill.state,
        domain=skill.domain,
        granularity_level=skill.granularity_level,
        success_rate=skill.metrics.success_rate,
        usage_count=skill.metrics.usage_count,
        tags=skill.tags,
    )


def _dedupe_edges(edges: Iterable[SkillEdge]) -> List[SkillEdge]:
    seen: Set[str] = set()
    unique: List[SkillEdge] = []
    for edge in edges:
        if edge.edge_id in seen:
            continue
        seen.add(edge.edge_id)
        unique.append(edge)
    return unique


def _auto_edge(source_id: str, target_id: str, edge_type: EdgeType) -> SkillEdge:
    return SkillEdge(
        edge_id=f"auto:{edge_type.value}:{source_id}:{target_id}",
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        weight=1.0,
        metadata={"auto_generated": True, "source": "skill_repository"},
    )


def _unique_ids(skill_ids: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for skill_id in skill_ids:
        if not skill_id or skill_id in seen:
            continue
        seen.add(skill_id)
        result.append(skill_id)
    return result
