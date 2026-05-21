"""Skill Librarian Agent — 维护 Skill Wiki、Graph、版本和元数据。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LibraryUpdateResult:
    skill_id: str
    wiki_updated: bool = False
    graph_updated: bool = False
    version_recorded: bool = False
    metadata_updated: bool = False
    errors: List[str] = field(default_factory=list)


@dataclass
class GraphIndexResult:
    skill_id: str
    nodes_created: int = 0
    edges_created: int = 0
    errors: List[str] = field(default_factory=list)


class SkillLibrarianAgent:
    """维护 Skill Wiki、Graph、版本记录和元数据。"""

    def __init__(
        self,
        wiki_manager: Optional[Any] = None,
        graph_manager: Optional[Any] = None,
        version_controller: Optional[Any] = None,
    ) -> None:
        self._wiki = wiki_manager
        self._graph = graph_manager
        self._version_ctrl = version_controller

    async def update(self, skill: Skill, change_summary: str = "") -> LibraryUpdateResult:
        """将 Skill 更新同步到 Wiki、Graph 和版本记录。"""
        result = LibraryUpdateResult(skill_id=skill.skill_id)

        # 更新 Wiki
        if self._wiki:
            try:
                existing = await self._wiki.get(skill.skill_id)
                if existing:
                    updates = {
                        "description": skill.description,
                        "tags": skill.tags,
                        "implementation": skill.implementation.model_dump() if skill.implementation else None,
                    }
                    if hasattr(self._wiki, "update"):
                        await self._wiki.update(skill.skill_id, **updates)
                    elif hasattr(self._wiki, "db"):
                        await self._wiki.db.update(skill.skill_id, updates)
                    else:
                        raise RuntimeError("Wiki manager does not expose update")
                else:
                    await self._wiki.create(skill)
                result.wiki_updated = True
                logger.info(f"Librarian: Wiki 已更新 {skill.name}")
            except Exception as exc:
                result.errors.append(f"Wiki 更新失败: {exc}")

        # 更新 Graph（确保节点存在）
        if self._graph:
            try:
                if hasattr(self._graph, "sync_skill"):
                    await self._graph.sync_skill(skill)
                elif hasattr(self._graph, "add_skill"):
                    maybe_result = self._graph.add_skill(skill)
                    if hasattr(maybe_result, "__await__"):
                        await maybe_result
                else:
                    raise RuntimeError("Graph manager does not expose sync_skill")
                result.graph_updated = True
                logger.info(f"Librarian: Graph 节点已更新 {skill.name}")
            except Exception as exc:
                result.errors.append(f"Graph 更新失败: {exc}")

        # 记录版本变更
        if self._version_ctrl and change_summary:
            try:
                from ..skill_governance import ChangeType

                self._version_ctrl.record_change(
                    skill,
                    change_type=ChangeType.IMPLEMENTATION_CHANGED,
                    summary=change_summary,
                    author="skill_librarian",
                )
                result.version_recorded = True
            except Exception as exc:
                result.errors.append(f"版本记录失败: {exc}")

        result.metadata_updated = result.wiki_updated
        return result

    async def register_new(self, skill: Skill) -> LibraryUpdateResult:
        """注册全新 Skill 到所有系统。"""
        result = LibraryUpdateResult(skill_id=skill.skill_id)

        if self._wiki:
            try:
                await self._wiki.create(skill)
                result.wiki_updated = True
            except Exception as exc:
                result.errors.append(f"Wiki 注册失败: {exc}")

        if self._graph:
            try:
                if hasattr(self._graph, "sync_skill"):
                    await self._graph.sync_skill(skill)
                elif hasattr(self._graph, "add_skill"):
                    maybe_result = self._graph.add_skill(skill)
                    if hasattr(maybe_result, "__await__"):
                        await maybe_result
                else:
                    raise RuntimeError("Graph manager does not expose sync_skill")
                result.graph_updated = True
            except Exception as exc:
                result.errors.append(f"Graph 注册失败: {exc}")

        return result

    async def add_relation(
        self, source_id: str, target_id: str, edge_type: str, weight: float = 1.0
    ) -> bool:
        """在 Graph 中添加 Skill 关系边。"""
        if not self._graph:
            return False
        try:
            from ...models.graph_model import SkillEdge
            from ...models.skill_model import EdgeType

            edge = SkillEdge(
                source_id=source_id,
                target_id=target_id,
                edge_type=EdgeType(edge_type),
                weight=weight,
            )
            if hasattr(self._graph, "create_edge"):
                await self._graph.create_edge(edge)
            elif hasattr(self._graph, "add_edge"):
                maybe_result = self._graph.add_edge(edge)
                if hasattr(maybe_result, "__await__"):
                    await maybe_result
            else:
                raise RuntimeError("Graph manager does not expose create_edge")
            return True
        except Exception as exc:
            logger.warning(f"Librarian: 添加关系失败 {source_id}→{target_id}: {exc}")
            return False

    async def index_ingested_unit_graph(
        self,
        skill: Skill,
        unit: Any,
        request_source_type: str,
    ) -> GraphIndexResult:
        """Index fixed-pipeline source artifacts around a Skill in the heterogeneous graph."""
        result = GraphIndexResult(skill_id=skill.skill_id)
        if not self._graph:
            result.errors.append("Graph manager is not configured.")
            return result
        if not hasattr(self._graph, "upsert_node") or not hasattr(self._graph, "upsert_edge"):
            result.errors.append("Graph manager does not expose heterogeneous graph methods.")
            return result

        from ...models.graph_model import (
            GraphNodeType,
            GraphRelationType,
            HeterogeneousGraphEdge,
            HeterogeneousGraphNode,
        )

        metadata = getattr(unit, "metadata", {}) or {}
        source_type = str(metadata.get("source_type") or request_source_type).lower()
        source_id = str(metadata.get("source_id") or f"{source_type}:{getattr(unit, 'unit_id', skill.skill_id)}")
        source_node_type = _source_node_type(source_type, GraphNodeType)

        async def add_node(node: HeterogeneousGraphNode) -> None:
            await self._graph.upsert_node(node)
            result.nodes_created += 1

        async def add_edge(edge: HeterogeneousGraphEdge) -> None:
            await self._graph.upsert_edge(edge)
            result.edges_created += 1

        try:
            if hasattr(self._graph, "sync_skill"):
                await self._graph.sync_skill(skill)

            await add_node(HeterogeneousGraphNode(
                node_id=source_id,
                node_type=source_node_type,
                name=str(metadata.get("source_title") or source_type.replace("_", " ").title()),
                description=str(metadata.get("source_description") or getattr(unit, "raw_content", "")[:240]),
                labels=[source_type, "static-source"],
                source_type=source_type,
                metadata={
                    "unit_id": getattr(unit, "unit_id", ""),
                    "pipeline": "fixed_demo",
                    "managed_by": "SkillLibrarianAgent",
                    "capability_scope": metadata.get("capability_scope"),
                    "capability_kind": metadata.get("capability_kind"),
                    "target": metadata.get("target"),
                },
            ))
            await add_edge(_hetero_edge(
                skill.skill_id,
                source_id,
                GraphRelationType.DERIVED_FROM,
                "SkillLibrarianAgent",
            ))

            for tool in metadata.get("tools", []) or []:
                tool_id = f"tool:{_slug(tool)}"
                await add_node(HeterogeneousGraphNode(
                    node_id=tool_id,
                    node_type=GraphNodeType.TOOL,
                    name=str(tool),
                    description=f"Tool used by {skill.display_name}.",
                    labels=["tool"],
                    source_type="tool_doc",
                ))
                await add_edge(_hetero_edge(skill.skill_id, tool_id, GraphRelationType.USES, "SkillLibrarianAgent"))

            for endpoint in metadata.get("api_endpoints", []) or []:
                endpoint_id = f"api_doc:{_slug(endpoint)}"
                await add_node(HeterogeneousGraphNode(
                    node_id=endpoint_id,
                    node_type=GraphNodeType.API_DOC,
                    name=str(endpoint),
                    description=f"API endpoint referenced by {skill.display_name}.",
                    labels=["api", "endpoint"],
                    source_type="api_doc",
                ))
                await add_edge(_hetero_edge(skill.skill_id, endpoint_id, GraphRelationType.REQUIRES, "SkillLibrarianAgent"))

            for test_name in metadata.get("tests", []) or []:
                test_id = f"test:{skill.name}:{_slug(test_name)}"
                await add_node(HeterogeneousGraphNode(
                    node_id=test_id,
                    node_type=GraphNodeType.TEST,
                    name=str(test_name),
                    description=f"Validation case for {skill.display_name}.",
                    labels=["test", "validation"],
                    source_type="test",
                ))
                await add_edge(_hetero_edge(skill.skill_id, test_id, GraphRelationType.VERIFIED_BY, "SkillLibrarianAgent"))

            version = str(metadata.get("version") or skill.version)
            version_id = f"version:{skill.name}:{version}"
            await add_node(HeterogeneousGraphNode(
                node_id=version_id,
                node_type=GraphNodeType.VERSION,
                name=f"{skill.display_name} v{version}",
                description="Version node created by the Skill Librarian Agent.",
                labels=["version", skill.skill_type.value],
                skill_id=skill.skill_id,
                version=version,
                source_type="version_store",
            ))
            await add_edge(_hetero_edge(version_id, skill.skill_id, GraphRelationType.VERSION_OF, "SkillLibrarianAgent"))
        except Exception as exc:
            result.errors.append(f"Graph indexing failed: {exc}")
            logger.warning("Librarian: heterogeneous graph indexing failed for %s: %s", skill.name, exc)

        return result


def _source_node_type(source_type: str, graph_node_type: Any) -> Any:
    mapping = {
        "trajectory": graph_node_type.TRAJECTORY,
        "document": graph_node_type.DOCUMENT,
        "api_doc": graph_node_type.API_DOC,
        "script": graph_node_type.SCRIPT,
        "task": graph_node_type.TASK,
    }
    return mapping.get(source_type, graph_node_type.DOCUMENT)


def _hetero_edge(source_id: str, target_id: str, relation_type: Any, created_by: str) -> Any:
    from ...models.graph_model import HeterogeneousGraphEdge

    return HeterogeneousGraphEdge(
        edge_id=f"agent:{relation_type.value}:{source_id}:{target_id}",
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        metadata={"auto_generated": True, "source": "skill_librarian_agent"},
        created_by=created_by,
    )


def _slug(value: Any) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip()).strip("_").lower()
    return cleaned or "unknown"
