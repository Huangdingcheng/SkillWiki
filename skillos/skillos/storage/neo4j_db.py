"""Neo4j 图存储层 — 同质 Skill 图的节点和边管理。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..models.graph_model import (
    EdgeType,
    GraphStats,
    SkillEdge,
    SkillGraphNode,
    SkillSubgraph,
)
from ..models.skill_model import Skill, SkillState, SkillType
from ..utils.logger import get_logger
from .base import BaseConnection

logger = get_logger(__name__)

# Neo4j 节点标签
SKILL_LABEL = "Skill"


class Neo4jConnection(BaseConnection):
    """Neo4j 异步连接管理器（使用官方 neo4j Python 驱动）。"""

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver: Optional[Any] = None

    async def connect(self) -> None:
        try:
            from neo4j import AsyncGraphDatabase
            self._driver = AsyncGraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            await self._driver.verify_connectivity()
            await self._ensure_constraints()
            logger.info(f"Neo4j 连接成功: {self._uri}")
        except ImportError:
            raise RuntimeError("请安装 neo4j 驱动: pip install neo4j")

    async def disconnect(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None
            logger.info("Neo4j 连接已关闭")

    async def health_check(self) -> bool:
        if not self._driver:
            return False
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception as e:
            logger.error(f"Neo4j 健康检查失败: {e}")
            return False

    async def ping(self) -> float:
        start = time.monotonic()
        await self.health_check()
        return (time.monotonic() - start) * 1000

    async def run(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """执行 Cypher 查询，返回结果列表。"""
        if not self._driver:
            raise RuntimeError("Neo4j 未连接，请先调用 connect()")
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, params or {})
            return [dict(record) async for record in result]

    async def run_write(self, query: str, params: Optional[Dict[str, Any]] = None) -> None:
        """执行写操作 Cypher 查询。"""
        if not self._driver:
            raise RuntimeError("Neo4j 未连接，请先调用 connect()")
        async with self._driver.session(database=self._database) as session:
            await session.execute_write(
                lambda tx: tx.run(query, params or {})
            )

    async def _ensure_constraints(self) -> None:
        """创建唯一约束和索引。"""
        constraints = [
            "CREATE CONSTRAINT skill_id_unique IF NOT EXISTS FOR (s:Skill) REQUIRE s.skill_id IS UNIQUE",
            "CREATE INDEX skill_name_idx IF NOT EXISTS FOR (s:Skill) ON (s.name)",
            "CREATE INDEX skill_state_idx IF NOT EXISTS FOR (s:Skill) ON (s.state)",
            "CREATE INDEX skill_type_idx IF NOT EXISTS FOR (s:Skill) ON (s.skill_type)",
            "CREATE INDEX skill_domain_idx IF NOT EXISTS FOR (s:Skill) ON (s.domain)",
        ]
        for cypher in constraints:
            try:
                await self.run(cypher)
            except Exception as e:
                logger.warning(f"约束创建跳过（可能已存在）: {e}")


# ---------------------------------------------------------------------------
# Graph Repository
# ---------------------------------------------------------------------------

class SkillGraphRepository:
    """Skill 同质图的 Neo4j 仓储。

    只存储图结构（节点属性 + 边），完整 Skill 数据在 PostgreSQL。
    """

    def __init__(self, conn: Neo4jConnection) -> None:
        self._conn = conn

    # --- Node Operations ---

    async def upsert_node(self, skill: Skill) -> None:
        """插入或更新 Skill 节点（MERGE 语义）。"""
        props = skill.to_graph_node()
        cypher = """
        MERGE (s:Skill {skill_id: $skill_id})
        SET s += $props
        """
        await self._conn.run_write(cypher, {"skill_id": skill.skill_id, "props": props})
        logger.debug(f"图节点已 upsert: {skill.name}")

    async def delete_node(self, skill_id: str) -> None:
        """删除节点及其所有关联边。"""
        cypher = "MATCH (s:Skill {skill_id: $skill_id}) DETACH DELETE s"
        await self._conn.run_write(cypher, {"skill_id": skill_id})

    async def get_node(self, skill_id: str) -> Optional[SkillGraphNode]:
        """获取单个节点（含边信息）。"""
        cypher = """
        MATCH (s:Skill {skill_id: $skill_id})
        OPTIONAL MATCH (s)-[r_out]->(t:Skill)
        OPTIONAL MATCH (src:Skill)-[r_in]->(s)
        RETURN s,
               collect(DISTINCT {
                   edge_id: r_out.edge_id,
                   source_id: s.skill_id,
                   target_id: t.skill_id,
                   edge_type: type(r_out),
                   weight: r_out.weight,
                   confidence: r_out.confidence
               }) AS out_edges,
               collect(DISTINCT {
                   edge_id: r_in.edge_id,
                   source_id: src.skill_id,
                   target_id: s.skill_id,
                   edge_type: type(r_in),
                   weight: r_in.weight,
                   confidence: r_in.confidence
               }) AS in_edges
        """
        results = await self._conn.run(cypher, {"skill_id": skill_id})
        if not results:
            return None
        return self._parse_node_result(results[0])

    async def list_nodes(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 200,
    ) -> List[SkillGraphNode]:
        """列出节点（不含边详情，用于图全览）。"""
        where_clauses = []
        params: Dict[str, Any] = {"limit": limit}

        if filters:
            if "state" in filters:
                where_clauses.append("s.state = $state")
                params["state"] = filters["state"]
            if "skill_type" in filters:
                where_clauses.append("s.skill_type = $skill_type")
                params["skill_type"] = filters["skill_type"]
            if "domain" in filters:
                where_clauses.append("s.domain = $domain")
                params["domain"] = filters["domain"]

        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        cypher = f"""
        MATCH (s:Skill)
        {where}
        RETURN s
        LIMIT $limit
        """
        results = await self._conn.run(cypher, params)
        return [self._parse_node_only(r["s"]) for r in results]

    # --- Edge Operations ---

    async def create_edge(self, edge: SkillEdge) -> None:
        """创建有向边（MERGE 语义，避免重复）。"""
        rel_type = edge.edge_type.value.upper()
        cypher = f"""
        MATCH (src:Skill {{skill_id: $source_id}})
        MATCH (tgt:Skill {{skill_id: $target_id}})
        MERGE (src)-[r:{rel_type} {{edge_id: $edge_id}}]->(tgt)
        SET r += $props
        """
        await self._conn.run_write(cypher, {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "edge_id": edge.edge_id,
            "props": edge.to_neo4j_props(),
        })

    async def delete_edge(self, edge_id: str) -> None:
        cypher = "MATCH ()-[r {edge_id: $edge_id}]->() DELETE r"
        await self._conn.run_write(cypher, {"edge_id": edge_id})

    async def get_edges(
        self,
        skill_id: str,
        direction: str = "both",
        edge_type: Optional[EdgeType] = None,
    ) -> List[SkillEdge]:
        """获取节点的边（direction: out | in | both）。"""
        rel_filter = f":{edge_type.value.upper()}" if edge_type else ""
        if direction == "out":
            pattern = f"(s:Skill {{skill_id: $skill_id}})-[r{rel_filter}]->(t:Skill)"
            return_clause = "s.skill_id AS source_id, t.skill_id AS target_id"
        elif direction == "in":
            pattern = f"(src:Skill)-[r{rel_filter}]->(s:Skill {{skill_id: $skill_id}})"
            return_clause = "src.skill_id AS source_id, s.skill_id AS target_id"
        else:
            pattern = f"(a:Skill)-[r{rel_filter}]-(b:Skill)"
            return_clause = "a.skill_id AS source_id, b.skill_id AS target_id"

        cypher = f"""
        MATCH {pattern}
        RETURN r, {return_clause}, type(r) AS rel_type
        """
        results = await self._conn.run(cypher, {"skill_id": skill_id})
        return [self._parse_edge_result(r) for r in results]

    # --- Subgraph Operations ---

    async def get_subgraph(self, skill_id: str, depth: int = 2) -> SkillSubgraph:
        """获取以指定节点为中心的子图（BFS，指定深度）。"""
        cypher = """
        MATCH path = (center:Skill {skill_id: $skill_id})-[*0..$depth]-(neighbor:Skill)
        WITH nodes(path) AS ns, relationships(path) AS rs
        UNWIND ns AS n
        WITH collect(DISTINCT n) AS all_nodes, rs
        UNWIND rs AS r
        RETURN all_nodes,
               collect(DISTINCT {
                   edge_id: r.edge_id,
                   source_id: startNode(r).skill_id,
                   target_id: endNode(r).skill_id,
                   edge_type: type(r),
                   weight: r.weight,
                   confidence: r.confidence
               }) AS all_edges
        """
        results = await self._conn.run(cypher, {"skill_id": skill_id, "depth": depth})
        subgraph = SkillSubgraph(name=f"subgraph_{skill_id[:8]}")

        if results:
            row = results[0]
            for node_data in row.get("all_nodes", []):
                node = self._parse_node_only(node_data)
                subgraph.nodes[node.skill_id] = node
            for edge_data in row.get("all_edges", []):
                if edge_data.get("source_id") and edge_data.get("target_id"):
                    try:
                        edge = self._parse_edge_dict(edge_data)
                        subgraph.edges.append(edge)
                    except Exception:
                        pass
        return subgraph

    async def find_similar_skills(
        self, skill_id: str, min_similarity: float = 0.7
    ) -> List[Tuple[str, float]]:
        """查找相似 Skill（通过 similar_to 边）。"""
        cypher = """
        MATCH (s:Skill {skill_id: $skill_id})-[r:SIMILAR_TO]-(t:Skill)
        WHERE r.weight >= $min_similarity
        RETURN t.skill_id AS target_id, r.weight AS similarity
        ORDER BY similarity DESC
        """
        results = await self._conn.run(cypher, {
            "skill_id": skill_id,
            "min_similarity": min_similarity,
        })
        return [(r["target_id"], r["similarity"]) for r in results]

    async def get_dependency_chain(self, skill_id: str) -> List[str]:
        """获取完整依赖链（递归 depends_on）。"""
        cypher = """
        MATCH (s:Skill {skill_id: $skill_id})-[:DEPENDS_ON*]->(dep:Skill)
        RETURN DISTINCT dep.skill_id AS dep_id
        """
        results = await self._conn.run(cypher, {"skill_id": skill_id})
        return [r["dep_id"] for r in results]

    async def get_stats(self) -> GraphStats:
        """计算全图统计信息。"""
        node_cypher = """
        MATCH (s:Skill)
        RETURN
            count(s) AS total,
            sum(CASE WHEN s.skill_type = 'atomic' THEN 1 ELSE 0 END) AS atomic,
            sum(CASE WHEN s.skill_type = 'composite' THEN 1 ELSE 0 END) AS composite,
            sum(CASE WHEN s.skill_type = 'meta' THEN 1 ELSE 0 END) AS meta,
            avg(s.success_rate) AS avg_success_rate,
            sum(s.usage_count) AS total_usage
        """
        edge_cypher = "MATCH ()-[r]->() RETURN count(r) AS total_edges"
        state_cypher = """
        MATCH (s:Skill)
        RETURN s.state AS state, count(s) AS cnt
        """

        node_results = await self._conn.run(node_cypher)
        edge_results = await self._conn.run(edge_cypher)
        state_results = await self._conn.run(state_cypher)

        nr = node_results[0] if node_results else {}
        er = edge_results[0] if edge_results else {}
        total_nodes = nr.get("total", 0)
        total_edges = er.get("total_edges", 0)

        state_dist = {r["state"]: r["cnt"] for r in state_results}
        density = (
            total_edges / (total_nodes * (total_nodes - 1))
            if total_nodes > 1 else 0.0
        )

        return GraphStats(
            total_nodes=total_nodes,
            total_edges=total_edges,
            atomic_count=nr.get("atomic", 0),
            composite_count=nr.get("composite", 0),
            meta_count=nr.get("meta", 0),
            state_distribution=state_dist,
            avg_success_rate=nr.get("avg_success_rate") or 0.0,
            total_usage_count=nr.get("total_usage") or 0,
            density=density,
        )

    # --- Helpers ---

    def _parse_node_only(self, node_data: Any) -> SkillGraphNode:
        if hasattr(node_data, "items"):
            d = dict(node_data)
        else:
            d = node_data
        return SkillGraphNode(
            skill_id=d["skill_id"],
            name=d["name"],
            version=d.get("version", "1.0.0"),
            skill_type=SkillType(d.get("skill_type", "atomic")),
            state=SkillState(d.get("state", "S2")),
            domain=d.get("domain", "general"),
            granularity_level=d.get("granularity_level", 1),
            success_rate=d.get("success_rate", 0.0),
            usage_count=d.get("usage_count", 0),
            tags=d.get("tags", []),
        )

    def _parse_node_result(self, row: Dict[str, Any]) -> SkillGraphNode:
        node = self._parse_node_only(row["s"])
        for e_data in row.get("out_edges", []):
            if e_data.get("target_id"):
                try:
                    node.out_edges.append(self._parse_edge_dict(e_data))
                except Exception:
                    pass
        for e_data in row.get("in_edges", []):
            if e_data.get("source_id"):
                try:
                    node.in_edges.append(self._parse_edge_dict(e_data))
                except Exception:
                    pass
        return node

    def _parse_edge_dict(self, d: Dict[str, Any]) -> SkillEdge:
        et_raw = d.get("edge_type", "DEPENDS_ON").lower()
        return SkillEdge(
            edge_id=d.get("edge_id", ""),
            source_id=d["source_id"],
            target_id=d["target_id"],
            edge_type=EdgeType(et_raw),
            weight=d.get("weight") or 1.0,
            confidence=d.get("confidence") or 1.0,
        )

    def _parse_edge_result(self, row: Dict[str, Any]) -> SkillEdge:
        et_raw = row.get("rel_type", "DEPENDS_ON").lower()
        r = row.get("r", {})
        return SkillEdge(
            edge_id=r.get("edge_id", ""),
            source_id=row["source_id"],
            target_id=row["target_id"],
            edge_type=EdgeType(et_raw),
            weight=r.get("weight") or 1.0,
            confidence=r.get("confidence") or 1.0,
        )
