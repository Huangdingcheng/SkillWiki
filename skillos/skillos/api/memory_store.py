"""内存 Wiki/Graph/Search 管理器 — 用于 demo 模式（无需真实数据库）。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..layers.skill_repository.indexing import SearchQuery, SearchResult
from ..models.graph_model import SkillEdge, SkillSubgraph
from ..models.skill_model import Skill, SkillState, SkillType
from ..utils.logger import get_logger

logger = get_logger(__name__)


class MemoryWikiManager:
    """纯内存实现的 SkillWikiManager，接口与真实版本完全兼容。"""

    def __init__(self) -> None:
        self._store: Dict[str, Skill] = {}

    # ── Read ──────────────────────────────────────────────────────────────

    async def get(self, skill_id: str) -> Optional[Skill]:
        return self._store.get(skill_id)

    async def get_by_name(self, name: str, version: Optional[str] = None) -> Optional[Skill]:
        matches = [s for s in self._store.values() if s.name == name]
        if not matches:
            return None
        if version:
            return next((s for s in matches if s.version == version), None)
        released = [s for s in matches if s.state == SkillState.RELEASED]
        pool = released or matches
        return max(pool, key=lambda s: s.updated_at)

    async def get_many(self, skill_ids: List[str]) -> Dict[str, Optional[Skill]]:
        return {sid: self._store.get(sid) for sid in skill_ids}

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
        skills = list(self._store.values())
        if skill_type:
            skills = [s for s in skills if s.skill_type == skill_type]
        if state:
            skills = [s for s in skills if s.state == state]
        if tags:
            tag_set = set(tags)
            skills = [s for s in skills if tag_set & set(s.tags)]
        if name_like:
            skills = [s for s in skills if name_like.lower() in s.name.lower()]
        return skills[offset: offset + limit]

    async def search_by_tags(self, tags: List[str], limit: int = 50) -> List[Skill]:
        tag_set = set(tags)
        return [s for s in self._store.values() if tag_set & set(s.tags)][:limit]

    async def count(self, skill_type=None, state=None) -> int:
        return len(await self.list(skill_type=skill_type, state=state, limit=10000))

    # ── Write ─────────────────────────────────────────────────────────────

    async def create(self, skill: Skill) -> Skill:
        existing = await self.get_by_name(skill.name, skill.version)
        if existing:
            raise ValueError(f"Skill '{skill.name}' v{skill.version} 已存在")
        self._store[skill.skill_id] = skill
        logger.info(f"Skill 已创建: {skill.name} v{skill.version}")
        return skill

    async def update(self, skill_id: str, **kwargs: Any) -> Optional[Skill]:
        skill = self._store.get(skill_id)
        if not skill:
            return None
        updated = skill.model_copy(deep=True)
        for k, v in kwargs.items():
            if k not in ("skill_id", "created_at"):
                object.__setattr__(updated, k, v)
        object.__setattr__(updated, "updated_at", datetime.utcnow())
        self._store[skill_id] = updated
        return updated

    async def delete(self, skill_id: str) -> bool:
        return bool(self._store.pop(skill_id, None))

    # ── Version ───────────────────────────────────────────────────────────

    async def create_new_version(self, source_skill_id: str, bump: str = "patch", **overrides) -> Skill:
        source = await self.get(source_skill_id)
        if not source:
            raise ValueError(f"源 Skill 不存在: {source_skill_id}")
        new_skill = source.model_copy(deep=True)
        object.__setattr__(new_skill, "skill_id", str(uuid.uuid4()))
        new_skill.bump_version(bump)
        object.__setattr__(new_skill, "state", SkillState.DRAFT)
        object.__setattr__(new_skill, "created_at", datetime.utcnow())
        object.__setattr__(new_skill, "updated_at", datetime.utcnow())
        object.__setattr__(new_skill, "released_at", None)
        object.__setattr__(new_skill, "deprecated_at", None)
        for k, v in overrides.items():
            object.__setattr__(new_skill, k, v)
        return await self.create(new_skill)

    async def get_version_history(self, name: str) -> List[Skill]:
        exact = [s for s in self._store.values() if s.name == name]
        exact.sort(key=lambda s: [int(x) for x in s.version.split(".")])
        return exact

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def transition_state(self, skill_id: str, new_state: SkillState, reason: Optional[str] = None) -> Skill:
        skill = await self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill 不存在: {skill_id}")
        skill.transition_to(new_state)
        self._store[skill_id] = skill
        return skill

    async def release(self, skill_id: str) -> Skill:
        skill = await self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill 不存在: {skill_id}")
        skill.transition_to(SkillState.RELEASED)
        object.__setattr__(skill, "released_at", datetime.utcnow())
        self._store[skill_id] = skill
        return skill

    async def deprecate(self, skill_id: str, reason: str = "", replacement_id: Optional[str] = None) -> Skill:
        skill = await self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill 不存在: {skill_id}")
        skill.transition_to(SkillState.DEPRECATED)
        object.__setattr__(skill, "deprecated_at", datetime.utcnow())
        self._store[skill_id] = skill
        return skill

    async def record_execution(self, skill_id: str, success: bool, latency_ms: float) -> None:
        skill = self._store.get(skill_id)
        if skill:
            skill.record_execution(success, latency_ms)

    # ── Stats ─────────────────────────────────────────────────────────────

    async def get_overview_stats(self) -> Dict[str, Any]:
        skills = list(self._store.values())
        by_state: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        total_exec = 0
        success_rates = []
        for s in skills:
            by_state[s.state.value] = by_state.get(s.state.value, 0) + 1
            by_type[s.skill_type.value] = by_type.get(s.skill_type.value, 0) + 1
            total_exec += s.metrics.total_executions
            if s.metrics.total_executions >= 5:
                success_rates.append(s.metrics.success_rate)
        return {
            "total_skills": len(skills),
            "by_state": by_state,
            "by_type": by_type,
            "total_executions": total_exec,
            "avg_success_rate": sum(success_rates) / len(success_rates) if success_rates else 1.0,
            "graph_stats": {},
        }

    # ── Cache compatibility (no-op) ───────────────────────────────────────

    async def invalidate(self, skill_id: str) -> None:
        pass  # 内存模式无需缓存失效


class MemoryGraphManager:
    """纯内存实现的 SkillGraphManager。"""

    def __init__(self) -> None:
        self._edges: List[SkillEdge] = []

    async def sync_skill(self, skill: Skill) -> None:
        pass

    async def create_edge(self, edge: SkillEdge) -> None:
        self._edges.append(edge)

    async def get_subgraph(self, skill_ids: List[str], depth: int = 2) -> SkillSubgraph:
        id_set = set(skill_ids)
        edges = [e for e in self._edges if e.source_id in id_set or e.target_id in id_set]
        return SkillSubgraph(root_ids=skill_ids, edges=edges)

    async def get_dependency_chain(self, skill_id: str) -> List[Skill]:
        return []

    async def get_execution_order(self, skill_id: str) -> List[str]:
        return [skill_id]

    async def add_evolution(self, new_id: str, old_id: str) -> None:
        pass

    async def add_dependency(self, source_id: str, target_id: str, weight: float = 1.0) -> None:
        from ..models.graph_model import EdgeType
        self._edges.append(SkillEdge(
            source_id=source_id, target_id=target_id,
            edge_type=EdgeType.DEPENDS_ON, weight=weight,
        ))

    async def add_composition(self, parent_id: str, child_id: str) -> None:
        from ..models.graph_model import EdgeType
        self._edges.append(SkillEdge(
            source_id=parent_id, target_id=child_id,
            edge_type=EdgeType.COMPOSES_WITH, weight=1.0,
        ))

    async def get_stats(self) -> Dict[str, Any]:
        return {"nodes": 0, "edges": len(self._edges)}

    async def find_merge_candidates(self, skill_id: str, threshold: float = 0.85) -> List:
        return []

    async def detect_cycles(self) -> List[List[str]]:
        return []

    async def get_central_skills(self, top_k: int = 10) -> List[str]:
        return []


class MemorySearchEngine:
    """纯内存搜索引擎，基于关键词匹配 + 多维评分。"""

    def __init__(self, wiki: MemoryWikiManager) -> None:
        self._wiki = wiki

    async def search(self, query: SearchQuery) -> List[SearchResult]:
        skills = await self._wiki.list(
            skill_type=query.skill_type,
            state=query.state,
            limit=500,
        )
        if not query.include_deprecated:
            skills = [s for s in skills if s.state not in (SkillState.DEPRECATED, SkillState.ARCHIVED)]
        results: List[SearchResult] = []
        keywords = set(re.findall(r"\w+", query.text.lower())) if query.text else set()

        for skill in skills:
            score = 0.0
            reasons: List[str] = []

            # 文本匹配 (40%)
            if keywords:
                text_blob = f"{skill.name} {skill.description}".lower()
                matched = keywords & set(re.findall(r"\w+", text_blob))
                text_score = len(matched) / len(keywords) if keywords else 0.0
                score += text_score * 0.4
                if matched:
                    reasons.append(f"关键词匹配: {', '.join(matched)}")

            # 标签匹配 (20%)
            if query.tags:
                tag_overlap = set(query.tags) & set(skill.tags)
                tag_score = len(tag_overlap) / len(query.tags)
                score += tag_score * 0.2
                if tag_overlap:
                    reasons.append(f"标签: {', '.join(tag_overlap)}")

            # 质量分 (25%)
            if skill.metrics.total_executions >= 5:
                quality = skill.metrics.success_rate
            else:
                quality = 0.5
            score += quality * 0.25

            # 状态分 (15%)
            state_scores = {
                SkillState.RELEASED: 1.0,
                SkillState.VERIFIED: 0.7,
                SkillState.SKILL_CANDIDATE: 0.5,
                SkillState.DRAFT: 0.3,
            }
            score += state_scores.get(skill.state, 0.1) * 0.15

            if score > 0.1 or not query.text:
                results.append(SearchResult(skill=skill, score=score, match_reasons=reasons))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[: query.max_results]

