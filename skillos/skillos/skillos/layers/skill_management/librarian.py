"""Skill Librarian Agent — 维护 Skill Wiki、Graph、版本和元数据。"""

from __future__ import annotations

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
                self._version_ctrl.record_change(
                    skill_id=skill.skill_id,
                    version=skill.version,
                    change_type="update",
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
