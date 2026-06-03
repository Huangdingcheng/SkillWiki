"""Skill 图管理器 — 封装 Neo4j 图操作，提供高层图语义接口。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ...models.graph_model import (
    EdgeType,
    GraphStats,
    SkillEdge,
    SkillGraphNode,
    SkillSubgraph,
)
from ...models.skill_model import Skill, SkillState
from ...storage.neo4j_db import Neo4jConnection, SkillGraphRepository
from ...utils.logger import get_logger

logger = get_logger(__name__)


class SkillGraphManager:
    """Skill 同质图的高层管理器。

    职责：
    - 节点同步（Skill 创建/更新时同步到图）
    - 边的语义化管理（依赖、组合、相似、演化）
    - 子图查询和路径分析
    - 图结构分析（连通性、中心性、聚类）
    """

    def __init__(self, neo4j_conn: Neo4jConnection) -> None:
        self._graph = SkillGraphRepository(neo4j_conn)

    # ------------------------------------------------------------------
    # Node Sync
    # ------------------------------------------------------------------

    async def sync_skill(self, skill: Skill) -> None:
        """将 Skill 同步到图（upsert）。"""
        await self._graph.upsert_node(skill)

    async def remove_skill(self, skill_id: str) -> None:
        """从图中删除节点及其所有边。"""
        await self._graph.delete_node(skill_id)

    async def get_node(self, skill_id: str) -> Optional[SkillGraphNode]:
        return await self._graph.get_node(skill_id)

    async def list_nodes(
        self,
        state: Optional[SkillState] = None,
        limit: int = 500,
    ) -> List[SkillGraphNode]:
        filters: Dict[str, Any] = {}
        if state:
            filters["state"] = state.value
        return await self._graph.list_nodes(filters=filters, limit=limit)

    # ------------------------------------------------------------------
    # Edge Management
    # ------------------------------------------------------------------

    async def add_dependency(
        self,
        skill_id: str,
        depends_on_id: str,
        weight: float = 1.0,
    ) -> SkillEdge:
        """添加 depends_on 边：skill_id 依赖 depends_on_id。"""
        edge = SkillEdge(
            source_id=skill_id,
            target_id=depends_on_id,
            edge_type=EdgeType.DEPENDS_ON,
            weight=weight,
        )
        await self._graph.create_edge(edge)
        logger.debug(f"依赖边: {skill_id[:8]} → {depends_on_id[:8]}")
        return edge

    async def add_composition(
        self,
        composite_id: str,
        component_id: str,
        weight: float = 1.0,
    ) -> SkillEdge:
        """添加 composes_with 边：composite_id 由 component_id 组成。"""
        edge = SkillEdge(
            source_id=composite_id,
            target_id=component_id,
            edge_type=EdgeType.COMPOSES_WITH,
            weight=weight,
        )
        await self._graph.create_edge(edge)
        return edge

    async def add_similarity(
        self,
        skill_id_a: str,
        skill_id_b: str,
        similarity: float,
    ) -> SkillEdge:
        """添加 similar_to 边（双向，取较高相似度方向）。"""
        edge = SkillEdge(
            source_id=skill_id_a,
            target_id=skill_id_b,
            edge_type=EdgeType.SIMILAR_TO,
            weight=similarity,
            confidence=similarity,
        )
        await self._graph.create_edge(edge)
        return edge

    async def add_evolution(
        self,
        new_skill_id: str,
        parent_skill_id: str,
    ) -> SkillEdge:
        """添加 evolved_from 边：new_skill_id 从 parent_skill_id 演化而来。"""
        edge = SkillEdge(
            source_id=new_skill_id,
            target_id=parent_skill_id,
            edge_type=EdgeType.EVOLVED_FROM,
            weight=1.0,
        )
        await self._graph.create_edge(edge)
        return edge

    async def add_replacement(
        self,
        replacement_id: str,
        replaced_id: str,
        reason: str = "",
    ) -> SkillEdge:
        """Add replaces edge: replacement_id supersedes replaced_id."""
        edge = SkillEdge(
            edge_id=f"maintenance:deprecate:replaces:{replacement_id}:{replaced_id}",
            source_id=replacement_id,
            target_id=replaced_id,
            edge_type=EdgeType.REPLACES,
            weight=1.0,
            description=reason,
            created_by="lifecycle",
            metadata={
                "maintenance_action": "deprecate",
                "source": "skill_graph_manager",
            },
        )
        await self._graph.create_edge(edge)
        return edge

    async def remove_edge(self, edge_id: str) -> None:
        await self._graph.delete_edge(edge_id)

    async def sync_auto_edges(self, skill: Skill, valid_skill_ids: Iterable[str]) -> None:
        valid_ids = set(valid_skill_ids)
        for edge in await self.get_edges(skill.skill_id, direction="out"):
            if (
                edge.edge_type in {EdgeType.COMPOSES_WITH, EdgeType.EVOLVED_FROM, EdgeType.DEPENDS_ON}
                and edge.metadata.get("auto_generated") is True
                and edge.metadata.get("source") == "skill_repository"
            ):
                await self.remove_edge(edge.edge_id)

        sub_skill_ids = skill.implementation.sub_skill_ids if skill.implementation else []
        for child_id in _unique_ids(sub_skill_ids):
            if child_id == skill.skill_id or child_id not in valid_ids:
                logger.warning("Skip auto graph edge with missing child Skill: %s -> %s", skill.skill_id, child_id)
                continue
            await self._graph.create_edge(_auto_edge(
                source_id=skill.skill_id,
                target_id=child_id,
                edge_type=EdgeType.COMPOSES_WITH,
            ))

        parent_ids = skill.provenance.parent_skill_ids if skill.provenance else []
        for parent_id in _unique_ids(parent_ids):
            if parent_id == skill.skill_id or parent_id not in valid_ids:
                logger.warning("Skip auto graph edge with missing parent Skill: %s -> %s", skill.skill_id, parent_id)
                continue
            await self._graph.create_edge(_auto_edge(
                source_id=skill.skill_id,
                target_id=parent_id,
                edge_type=EdgeType.EVOLVED_FROM,
            ))

        for dependency_id in _unique_ids(skill.dependency_ids):
            if dependency_id == skill.skill_id or dependency_id not in valid_ids:
                logger.warning("Skip auto graph edge with missing dependency Skill: %s -> %s", skill.skill_id, dependency_id)
                continue
            await self._graph.create_edge(_auto_edge(
                source_id=skill.skill_id,
                target_id=dependency_id,
                edge_type=EdgeType.DEPENDS_ON,
            ))

    async def get_edges(
        self,
        skill_id: str,
        direction: str = "both",
        edge_type: Optional[EdgeType] = None,
    ) -> List[SkillEdge]:
        return await self._graph.get_edges(skill_id, direction=direction, edge_type=edge_type)

    # ------------------------------------------------------------------
    # Subgraph & Path Analysis
    # ------------------------------------------------------------------

    async def get_subgraph(
        self,
        skill_id: Any = None,
        depth: int = 2,
        skill_ids: Optional[List[str]] = None,
    ) -> SkillSubgraph:
        """获取以指定节点为中心的子图。"""
        if skill_ids is not None:
            roots = list(skill_ids)
        elif isinstance(skill_id, list):
            roots = skill_id
        elif skill_id is None:
            roots = []
        else:
            roots = [skill_id]
        roots = [root for root in roots if root]
        if not roots:
            return SkillSubgraph()
        if len(roots) == 1:
            return await self._graph.get_subgraph(roots[0], depth=depth)

        merged = SkillSubgraph()
        seen_edges: Set[str] = set()
        for root in roots:
            subgraph = await self._graph.get_subgraph(root, depth=depth)
            for node in subgraph.nodes.values():
                merged.nodes[node.skill_id] = node
            for edge in subgraph.edges:
                if edge.edge_id in seen_edges:
                    continue
                seen_edges.add(edge.edge_id)
                merged.edges.append(edge)
        return merged

    async def get_dependency_chain(self, skill_id: str) -> List[str]:
        """获取完整递归依赖链（所有传递依赖）。"""
        return await self._graph.get_dependency_chain(skill_id)

    async def find_similar_skills(
        self,
        skill_id: str,
        min_similarity: float = 0.7,
    ) -> List[Tuple[str, float]]:
        """查找相似 Skill，返回 (skill_id, similarity) 列表。"""
        return await self._graph.find_similar_skills(skill_id, min_similarity)

    async def find_merge_candidates(
        self,
        min_similarity: float = 0.85,
    ) -> List[Tuple[str, str, float]]:
        """全图扫描，找出高相似度的 Skill 对（合并候选）。"""
        cypher = """
        MATCH (a:Skill)-[r:SIMILAR_TO]-(b:Skill)
        WHERE r.weight >= $min_sim AND a.skill_id < b.skill_id
        RETURN a.skill_id AS id_a, b.skill_id AS id_b, r.weight AS sim
        ORDER BY sim DESC
        LIMIT 50
        """
        results = await self._graph._conn.run(cypher, {"min_sim": min_similarity})
        return [(r["id_a"], r["id_b"], r["sim"]) for r in results]

    async def get_execution_order(self, skill_ids: List[str]) -> List[str]:
        """给定一组 Skill ID，按依赖关系返回执行顺序（拓扑排序）。"""
        if len(skill_ids) <= 1:
            return skill_ids

        # 构建局部子图
        subgraph = SkillSubgraph()
        for sid in skill_ids:
            node = await self.get_node(sid)
            if node:
                subgraph.add_node(node)

        # 只添加这些节点之间的 depends_on 边
        skill_id_set: Set[str] = set(skill_ids)
        for sid in skill_ids:
            edges = await self.get_edges(sid, direction="out", edge_type=EdgeType.DEPENDS_ON)
            for edge in edges:
                if edge.target_id in skill_id_set:
                    try:
                        subgraph.add_edge(edge)
                    except ValueError:
                        pass

        try:
            return subgraph.topological_sort()
        except ValueError:
            logger.warning("依赖图中存在环，返回原始顺序")
            return skill_ids

    # ------------------------------------------------------------------
    # Graph Analytics
    # ------------------------------------------------------------------

    async def get_stats(self) -> GraphStats:
        return await self._graph.get_stats()

    async def get_central_skills(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """按度中心性返回最重要的 Skill（入度 + 出度最高）。"""
        cypher = """
        MATCH (s:Skill)
        OPTIONAL MATCH (s)-[r_out]->()
        OPTIONAL MATCH ()-[r_in]->(s)
        WITH s, count(DISTINCT r_out) + count(DISTINCT r_in) AS degree
        ORDER BY degree DESC
        LIMIT $top_n
        RETURN s.skill_id AS skill_id, degree
        """
        results = await self._graph._conn.run(cypher, {"top_n": top_n})
        return [(r["skill_id"], r["degree"]) for r in results]

    async def get_isolated_skills(self) -> List[str]:
        """返回没有任何边的孤立节点（可能是冗余 Skill）。"""
        cypher = """
        MATCH (s:Skill)
        WHERE NOT (s)-[]-()
        RETURN s.skill_id AS skill_id
        """
        results = await self._graph._conn.run(cypher)
        return [r["skill_id"] for r in results]

    async def detect_cycles(self) -> List[List[str]]:
        """检测图中的环（仅检查 depends_on 边）。"""
        cypher = """
        MATCH path = (s:Skill)-[:DEPENDS_ON*2..]->(s)
        RETURN [n IN nodes(path) | n.skill_id] AS cycle
        LIMIT 20
        """
        results = await self._graph._conn.run(cypher)
        return [r["cycle"] for r in results]


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
