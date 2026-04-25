"""Skill 仓库层 — 高层 CRUD、缓存协调、版本管理。

SkillWikiManager 是 Skill 数据的统一入口，协调 PostgreSQL（持久化）
和 Redis（缓存），屏蔽底层存储细节。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ...models.skill_model import Skill, SkillState, SkillType
from ...storage.postgres_db import PostgresConnection, SkillRepository
from ...storage.redis_cache import RedisConnection, SkillCache, StatsCache
from ...utils.logger import get_logger

logger = get_logger(__name__)


class SkillWikiManager:
    """Skill Wiki 的统一管理器。

    职责：
    - Skill CRUD（含版本管理）
    - 缓存读写协调（Cache-Aside 策略）
    - 状态机转换
    - 批量操作
    """

    def __init__(
        self,
        pg_conn: PostgresConnection,
        redis_conn: Optional[RedisConnection] = None,
    ) -> None:
        self._repo = SkillRepository(pg_conn)
        self._cache = SkillCache(redis_conn) if redis_conn else None
        self._stats_cache = StatsCache(redis_conn) if redis_conn else None

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, skill_id: str) -> Optional[Skill]:
        """按 ID 获取 Skill（先查缓存，再查 DB）。"""
        if self._cache:
            cached = await self._cache.get(skill_id)
            if cached:
                return cached

        skill = await self._repo.get(skill_id)
        if skill and self._cache:
            await self._cache.set(skill)
        return skill

    async def get_by_name(self, name: str, version: Optional[str] = None) -> Optional[Skill]:
        """按名称（和可选版本）获取 Skill。"""
        if version:
            return await self._repo.get_by_name_version(name, version)
        # 无版本时返回最新 released 版本，否则返回最新 draft
        skills = await self._repo.list(
            filters={"name_like": name},
            limit=20,
        )
        exact = [s for s in skills if s.name == name]
        if not exact:
            return None
        # 优先 released，其次按 updated_at 降序
        released = [s for s in exact if s.state == SkillState.RELEASED]
        if released:
            return max(released, key=lambda s: s.updated_at)
        return max(exact, key=lambda s: s.updated_at)

    async def get_many(self, skill_ids: List[str]) -> Dict[str, Optional[Skill]]:
        """批量获取（缓存 pipeline + DB 补全）。"""
        result: Dict[str, Optional[Skill]] = {}
        if self._cache:
            cached = await self._cache.get_many(skill_ids)
            miss_ids = [sid for sid, v in cached.items() if v is None]
            result.update({sid: v for sid, v in cached.items() if v is not None})
        else:
            miss_ids = skill_ids

        if miss_ids:
            for sid in miss_ids:
                skill = await self._repo.get(sid)
                result[sid] = skill
            if self._cache:
                to_cache = [s for s in result.values() if s is not None]
                await self._cache.set_many(to_cache)

        return result

    async def list(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
        domain: Optional[str] = None,
        name_like: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Skill]:
        """列表查询，支持多维过滤。"""
        filters: Dict[str, Any] = {}
        if skill_type:
            filters["skill_type"] = skill_type.value
        if state:
            filters["state"] = state.value
        if domain:
            filters["domain"] = domain
        if name_like:
            filters["name_like"] = name_like
        return await self._repo.list(filters=filters, limit=limit, offset=offset)

    async def search_by_tags(self, tags: List[str], limit: int = 50) -> List[Skill]:
        return await self._repo.search_by_tags(tags, limit=limit)

    async def count(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
    ) -> int:
        filters: Dict[str, Any] = {}
        if skill_type:
            filters["skill_type"] = skill_type.value
        if state:
            filters["state"] = state.value
        return await self._repo.count(filters=filters)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(self, skill: Skill) -> Skill:
        """创建新 Skill，检查名称+版本唯一性。"""
        existing = await self._repo.get_by_name_version(skill.name, skill.version)
        if existing:
            raise ValueError(
                f"Skill '{skill.name}' v{skill.version} 已存在 (id={existing.skill_id})"
            )
        created = await self._repo.create(skill)
        if self._cache:
            await self._cache.set(created)
        logger.info(f"Skill 已创建: {skill.name} v{skill.version}")
        return created

    async def update(self, skill_id: str, **kwargs: Any) -> Optional[Skill]:
        """部分更新 Skill 字段，自动更新 updated_at。"""
        # 不允许直接修改 skill_id、created_at
        kwargs.pop("skill_id", None)
        kwargs.pop("created_at", None)
        kwargs["updated_at"] = datetime.utcnow()

        updated = await self._repo.update(skill_id, kwargs)
        if updated and self._cache:
            await self._cache.set(updated)
        return updated

    async def delete(self, skill_id: str) -> bool:
        """删除 Skill（同时清除缓存）。"""
        ok = await self._repo.delete(skill_id)
        if ok and self._cache:
            await self._cache.delete(skill_id)
        return ok

    # ------------------------------------------------------------------
    # Version Management
    # ------------------------------------------------------------------

    async def create_new_version(
        self,
        source_skill_id: str,
        bump: str = "patch",
        **overrides: Any,
    ) -> Skill:
        """基于已有 Skill 创建新版本。"""
        source = await self.get(source_skill_id)
        if not source:
            raise ValueError(f"源 Skill 不存在: {source_skill_id}")

        new_skill = source.model_copy(deep=True)
        new_skill.skill_id = str(uuid.uuid4())
        new_skill.bump_version(bump)
        new_skill.state = SkillState.DRAFT
        new_skill.created_at = datetime.utcnow()
        new_skill.updated_at = datetime.utcnow()
        new_skill.released_at = None
        new_skill.deprecated_at = None
        new_skill.metrics = new_skill.metrics.__class__()  # 重置指标

        for k, v in overrides.items():
            setattr(new_skill, k, v)

        return await self.create(new_skill)

    async def get_version_history(self, name: str) -> List[Skill]:
        """获取同名 Skill 的所有版本，按版本号排序。"""
        skills = await self._repo.list(filters={"name_like": name}, limit=100)
        exact = [s for s in skills if s.name == name]
        exact.sort(key=lambda s: [int(x) for x in s.version.split(".")])
        return exact

    # ------------------------------------------------------------------
    # Lifecycle Transitions
    # ------------------------------------------------------------------

    async def transition_state(
        self,
        skill_id: str,
        new_state: SkillState,
        reason: Optional[str] = None,
    ) -> Skill:
        """执行状态转换，持久化并更新缓存。"""
        skill = await self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill 不存在: {skill_id}")

        skill.transition_to(new_state)
        update_data: Dict[str, Any] = {
            "state": new_state.value,
            "updated_at": skill.updated_at,
        }
        if new_state == SkillState.RELEASED:
            update_data["released_at"] = skill.released_at
        elif new_state == SkillState.DEPRECATED:
            update_data["deprecated_at"] = skill.deprecated_at
            if reason:
                update_data["deprecation_reason"] = reason

        updated = await self._repo.update(skill_id, update_data)
        if updated and self._cache:
            await self._cache.set(updated)
        logger.info(f"Skill 状态转换: {skill.name} → {new_state.value}")
        return updated or skill

    async def release(self, skill_id: str) -> Skill:
        return await self.transition_state(skill_id, SkillState.RELEASED)

    async def deprecate(self, skill_id: str, reason: str, replacement_id: Optional[str] = None) -> Skill:
        skill = await self.transition_state(skill_id, SkillState.DEPRECATED, reason=reason)
        if replacement_id:
            await self._repo.update(skill_id, {"replacement_skill_id": replacement_id})
        return skill

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    async def record_execution(
        self,
        skill_id: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """记录执行结果，更新 DB 指标和缓存计数。"""
        skill = await self.get(skill_id)
        if not skill:
            return
        skill.record_execution(success, latency_ms)
        await self._repo.update(skill_id, {
            "usage_count": skill.metrics.usage_count,
            "success_count": skill.metrics.success_count,
            "failure_count": skill.metrics.failure_count,
            "avg_latency_ms": skill.metrics.avg_latency_ms,
            "last_used_at": skill.metrics.last_used_at,
        })
        if self._cache:
            await self._cache.set(skill)
            if self._stats_cache:
                await self._stats_cache.increment_usage(skill_id)

    async def get_overview_stats(self) -> Dict[str, Any]:
        """获取总览统计（优先从缓存读取）。"""
        if self._stats_cache:
            cached = await self._stats_cache.get_graph_stats()
            if cached:
                return cached

        total = await self._repo.count()
        released = await self._repo.count(filters={"state": SkillState.RELEASED.value})
        draft = await self._repo.count(filters={"state": SkillState.DRAFT.value})
        atomic = await self._repo.count(filters={"skill_type": SkillType.ATOMIC.value})
        functional = await self._repo.count(filters={"skill_type": SkillType.FUNCTIONAL.value})
        strategic = await self._repo.count(filters={"skill_type": SkillType.STRATEGIC.value})

        stats = {
            "total_skills": total,
            "released": released,
            "draft": draft,
            "atomic": atomic,
            "functional": functional,
            "strategic": strategic,
            "computed_at": datetime.utcnow().isoformat(),
        }
        if self._stats_cache:
            await self._stats_cache.set_graph_stats(stats)
        return stats
