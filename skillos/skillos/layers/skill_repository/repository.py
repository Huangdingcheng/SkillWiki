"""Skill repository layer backed by GitSkillStore."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState, SkillType
from ...storage.skill_repo.common import GitSkillStore
from ...utils.logger import get_logger

logger = get_logger(__name__)


class SkillWikiManager:
    """Canonical Skill Wiki manager.

    The public async API intentionally matches the previous repository manager
    so API, runtime, and management layers can keep using `app.wiki`.
    """

    def __init__(
        self,
        pg_conn: Any = None,
        redis_conn: Optional[Any] = None,
        *,
        storage_dir: Optional[str | Path] = None,
        auto_commit: bool = True,
        init_storage: bool = True,
    ) -> None:
        self._pg_conn = pg_conn
        self._redis_conn = redis_conn
        self._store = GitSkillStore(storage_dir, auto_commit=auto_commit)
        if init_storage:
            self._store.init_repo(initial_commit=False)

    @property
    def store(self) -> GitSkillStore:
        return self._store

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, skill_id: str) -> Optional[Skill]:
        return self._store.get_skill_by_id(skill_id)

    async def get_by_name(self, name: str, version: Optional[str] = None) -> Optional[Skill]:
        return self._store.get_skill(name, version)

    async def get_many(self, skill_ids: List[str]) -> Dict[str, Optional[Skill]]:
        return {skill_id: self._store.get_skill_by_id(skill_id) for skill_id in skill_ids}

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
        rows = self._store.list_skills(
            skill_type=skill_type,
            state=state,
            tags=tags,
            domain=domain,
            name_like=name_like,
            latest_only=True,
            include_deleted=False,
            limit=limit,
            offset=offset,
        )
        return [
            skill
            for row in rows
            if (skill := self._store.get_skill(row["name"], row["version"])) is not None
        ]

    async def list_all_versions(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
        tags: Optional[List[str]] = None,
        domain: Optional[str] = None,
        name_like: Optional[str] = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Skill]:
        rows = self._store.list_skills(
            skill_type=skill_type,
            state=state,
            tags=tags,
            domain=domain,
            name_like=name_like,
            latest_only=False,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
        )
        return [
            skill
            for row in rows
            if (
                skill := self._store.get_skill(
                    row["name"],
                    row["version"],
                    include_deleted=include_deleted,
                )
            )
            is not None
        ]

    async def list_versions(self, name: str, *, include_deleted: bool = False) -> List[str]:
        return self._store.get_skill_versions(name, include_deleted=include_deleted)

    async def search_by_tags(self, tags: List[str], limit: int = 50) -> List[Skill]:
        return await self.list(tags=tags, limit=limit)

    async def count(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
    ) -> int:
        return len(
            self._store.list_skills(
                skill_type=skill_type,
                state=state,
                latest_only=True,
                include_deleted=False,
                limit=100000,
            )
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(self, skill: Skill) -> Skill:
        existing = self._store.get_skill(skill.name, skill.version, include_deleted=True)
        if existing:
            raise ValueError(f"Skill '{skill.name}' v{skill.version} already exists")
        created = self._store.add_skill(skill, author="repository", event_action="create")
        logger.info("Skill created: %s v%s", created.name, created.version)
        return created

    async def update(self, skill_id: str, **kwargs: Any) -> Optional[Skill]:
        skill = self._store.get_skill_by_id(skill_id)
        if not skill:
            return None
        kwargs.pop("skill_id", None)
        kwargs.pop("created_at", None)

        for key, value in kwargs.items():
            if key == "state" and isinstance(value, str):
                value = SkillState(value)
            elif key == "skill_type" and isinstance(value, str):
                value = SkillType(value)
            elif key == "interface" and isinstance(value, dict):
                value = SkillInterface.model_validate(value)
            elif key == "implementation" and isinstance(value, dict):
                value = SkillImplementation.model_validate(value)
            setattr(skill, key, value)
        skill.updated_at = datetime.utcnow()

        return self._store.update_skill_version(skill, author="repository", event_action="update")

    async def delete(self, skill_id: str) -> bool:
        skill = self._store.get_skill_by_id(skill_id)
        if not skill:
            return False
        return self._store.delete_skill(
            skill.name,
            version=skill.version,
            hard=False,
            author="repository",
            reason="deleted by SkillWikiManager.delete",
        )

    async def hard_delete(self, skill_id: str) -> bool:
        skill = self._store.get_skill_by_id(skill_id, include_deleted=True)
        if not skill:
            return False
        return self._store.delete_skill(
            skill.name,
            version=skill.version,
            hard=True,
            author="repository",
            reason="hard deleted by SkillWikiManager.hard_delete",
        )

    # ------------------------------------------------------------------
    # Version management
    # ------------------------------------------------------------------

    async def create_new_version(
        self,
        source_skill_id: str,
        bump: str = "patch",
        **overrides: Any,
    ) -> Skill:
        source = await self.get(source_skill_id)
        if not source:
            raise ValueError(f"Source Skill does not exist: {source_skill_id}")
        return self._store.create_new_version(
            source.name,
            source_version=source.version,
            bump=bump,  # type: ignore[arg-type]
            overrides=overrides,
            author="repository",
        )

    async def get_version_history(self, name: str) -> List[Skill]:
        return self._store.get_version_history(name, include_deleted=False)

    async def get_version_history_with_deleted(self, name: str) -> List[Skill]:
        return self._store.get_version_history(name, include_deleted=True)

    async def diff_versions(self, name: str, v1: str, v2: str) -> str:
        return self._store.diff_versions(name, v1, v2)

    async def git_history(
        self,
        name: str,
        version: Optional[str] = None,
        max_count: int = 20,
    ) -> str:
        return self._store.git_file_history(name, version, max_count=max_count)

    async def merge_versions(
        self,
        name: str,
        base_version: str,
        other_version: str,
        new_version: Optional[str] = None,
        strategy: str = "prefer_other",
        manual_overrides: Optional[Dict[str, Any]] = None,
    ) -> Skill:
        return self._store.merge_skills(
            name,
            base_version,
            other_version,
            new_version=new_version,
            strategy=strategy,  # type: ignore[arg-type]
            manual_overrides=manual_overrides,
            author="repository",
        )

    # ------------------------------------------------------------------
    # Lifecycle / metrics
    # ------------------------------------------------------------------

    async def transition_state(
        self,
        skill_id: str,
        new_state: SkillState,
        reason: Optional[str] = None,
    ) -> Skill:
        skill = await self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill does not exist: {skill_id}")
        return self._store.transition_skill_state(
            skill.name,
            skill.version,
            new_state,
            author="repository",
            reason=reason,
        )

    async def release(self, skill_id: str) -> Skill:
        return await self.transition_state(skill_id, SkillState.RELEASED)

    async def deprecate(
        self,
        skill_id: str,
        reason: str,
        replacement_id: Optional[str] = None,
    ) -> Skill:
        skill = await self.transition_state(skill_id, SkillState.DEPRECATED, reason=reason)
        if replacement_id:
            skill.replacement_skill_id = replacement_id
            skill.updated_at = datetime.utcnow()
            self._store.update_skill_version(skill, author="repository", event_action="deprecate")
        return skill

    async def archive(self, skill_id: str) -> Skill:
        return await self.transition_state(skill_id, SkillState.ARCHIVED)

    async def record_execution(self, skill_id: str, success: bool, latency_ms: float) -> None:
        skill = await self.get(skill_id)
        if not skill:
            return

        skill.record_execution(success, latency_ms)
        # Execution metrics can be very noisy. Persist them but do not create a
        # Git commit for every runtime call.
        self._store.update_skill_version(
            skill,
            author="runtime",
            commit=False,
            event_action="record_execution",
        )

    async def get_overview_stats(self) -> Dict[str, Any]:
        rows = self._store.list_skills(latest_only=True, include_deleted=False, limit=100000)
        total = len(rows)
        by_state: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        skills = [
            skill
            for row in rows
            if (skill := self._store.get_skill(row["name"], row["version"])) is not None
        ]
        total_exec = sum(skill.metrics.total_executions for skill in skills)
        rated = [skill for skill in skills if skill.metrics.total_executions >= 5]
        for row in rows:
            by_state[row.get("state", "")] = by_state.get(row.get("state", ""), 0) + 1
            by_type[row.get("skill_type", "")] = by_type.get(row.get("skill_type", ""), 0) + 1
        return {
            "total_skills": total,
            "by_state": by_state,
            "by_type": by_type,
            "total_executions": total_exec,
            "avg_success_rate": (
                sum(skill.metrics.success_rate for skill in rated) / len(rated)
                if rated
                else 1.0
            ),
            "graph_stats": {},
            "backend": "git",
            "repo_status": self._store.repo_status(),
        }

    # ------------------------------------------------------------------
    # Repository maintenance
    # ------------------------------------------------------------------

    async def rebuild_index(self) -> Dict[str, Any]:
        return self._store.rebuild_index()

    async def repo_status(self) -> Dict[str, Any]:
        return self._store.repo_status()

    async def read_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._store.read_events(limit=limit)

    async def push_to_remote(
        self,
        remote_name: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> str:
        return self._store.push_to_remote(remote_name, branch)

    async def pull_from_remote(
        self,
        remote_name: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> str:
        return self._store.pull_from_remote(remote_name, branch)

    async def sync_to_remote(self) -> str:
        return self._store.sync_to_remote()
