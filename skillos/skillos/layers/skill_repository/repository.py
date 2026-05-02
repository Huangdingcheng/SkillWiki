"""Skill 仓库层 — Git-backed 高层 CRUD、版本管理、生命周期管理。

这一版将原来的 PostgreSQL + Redis 后端替换为本地 Git 仓库后端。

底层存储位置：
    skillos/storage/skill_repo/SkillStorage

底层操作模块：
    skillos/storage/skill_repo/common.py

设计目标：
- 尽量保留原 SkillWikiManager 的 async 方法签名，减少 API 层改动。
- 本地 Git 仓库作为服务器端事实源。
- Skill 的新增、查询、删除、版本历史、diff、merge、状态迁移都委托给 common.py。
- 构造函数仍兼容旧代码传入 pg_conn / redis_conn，但不会再使用它们。

注意：
- create(skill) 是普通新增，若 name + version 已存在会报错。
- update(skill_id, **kwargs) 是覆盖当前版本文件，不创建新版本。
- create_new_version(source_skill_id, bump, **overrides) 才会创建新版本。
- delete(skill_id) 默认软删除。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill, SkillState, SkillType
from ...storage.skill_repo import common as git_store
from ...utils.logger import get_logger

logger = get_logger(__name__)


class SkillWikiManager:
    """Git-backed Skill Wiki 统一管理器。

    职责：
    - Skill CRUD
    - 版本管理
    - 生命周期状态迁移
    - 批量查询
    - 运行指标更新
    - Git diff / history / merge 辅助操作

    构造函数保留 pg_conn / redis_conn 参数，仅用于兼容旧调用点。
    """

    def __init__(
        self,
        pg_conn: Any = None,
        redis_conn: Optional[Any] = None,
        *,
        init_storage: bool = True,
    ) -> None:
        self._pg_conn = pg_conn
        self._redis_conn = redis_conn

        if init_storage:
            git_store.init_repo(initial_commit=False)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, skill_id: str) -> Optional[Skill]:
        """按 skill_id 获取 Skill。"""
        return git_store.get_skill_by_id(skill_id)

    async def get_by_name(
        self,
        name: str,
        version: Optional[str] = None,
    ) -> Optional[Skill]:
        """按名称和可选版本获取 Skill。

        如果不传 version，返回该 skill 的最新可见版本。
        """
        return git_store.get_skill(name, version)

    async def get_many(self, skill_ids: List[str]) -> Dict[str, Optional[Skill]]:
        """批量按 skill_id 获取 Skill。"""
        return {skill_id: git_store.get_skill_by_id(skill_id) for skill_id in skill_ids}

    async def list(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
        domain: Optional[str] = None,
        name_like: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Skill]:
        """列表查询，支持多维过滤。

        默认只返回每个 Skill 的最新版本。
        """
        rows = git_store.list_skills(
            skill_type=skill_type,
            state=state,
            domain=domain,
            name_like=name_like,
            latest_only=True,
            include_deleted=False,
            limit=limit,
            offset=offset,
        )

        result: List[Skill] = []
        for row in rows:
            skill = git_store.get_skill(row["name"], row["version"])
            if skill:
                result.append(skill)

        return result

    async def list_versions(
        self,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> List[str]:
        """获取某个 Skill 的版本号列表。"""
        return git_store.get_skill_versions(name, include_deleted=include_deleted)

    async def list_all_versions(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
        domain: Optional[str] = None,
        name_like: Optional[str] = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Skill]:
        """列表查询，返回所有版本，而不是仅最新版本。"""
        rows = git_store.list_skills(
            skill_type=skill_type,
            state=state,
            domain=domain,
            name_like=name_like,
            latest_only=False,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
        )

        result: List[Skill] = []
        for row in rows:
            skill = git_store.get_skill(
                row["name"],
                row["version"],
                include_deleted=include_deleted,
            )
            if skill:
                result.append(skill)

        return result

    async def search_by_tags(self, tags: List[str], limit: int = 50) -> List[Skill]:
        """按 tag 简单搜索。

        当前 Git 版没有专门索引倒排表，先从 list 中过滤。
        """
        normalized = {tag.strip().lower() for tag in tags if tag.strip()}
        if not normalized:
            return []

        skills = await self.list(limit=100000)
        matched: List[Skill] = []

        for skill in skills:
            skill_tags = {tag.strip().lower() for tag in skill.tags}
            if normalized.intersection(skill_tags):
                matched.append(skill)
            if len(matched) >= limit:
                break

        return matched

    async def search(self, query: str, limit: int = 20) -> List[Skill]:
        """简单关键词搜索。

        匹配 name / display_name / description / tags。
        后续如果 indexing.py 接入向量检索，可以替换这里。
        """
        q = query.strip().lower()
        if not q:
            return []

        skills = await self.list(limit=100000)
        scored: List[tuple[int, Skill]] = []

        for skill in skills:
            score = 0
            if q in skill.name.lower():
                score += 5
            if q in skill.display_name.lower():
                score += 4
            if q in skill.description.lower():
                score += 3
            if any(q in tag.lower() for tag in skill.tags):
                score += 2
            if q in skill.domain.lower():
                score += 1

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [skill for _, skill in scored[:limit]]

    async def count(
        self,
        skill_type: Optional[SkillType] = None,
        state: Optional[SkillState] = None,
    ) -> int:
        """统计 Skill 数量。

        默认统计最新版本。
        """
        rows = git_store.list_skills(
            skill_type=skill_type,
            state=state,
            latest_only=True,
            include_deleted=False,
            limit=100000,
        )
        return len(rows)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(self, skill: Skill) -> Skill:
        """创建新 Skill 版本，检查 name + version 唯一性。"""
        existing = git_store.get_skill(skill.name, skill.version, include_deleted=True)
        if existing:
            raise ValueError(
                f"Skill '{skill.name}' v{skill.version} 已存在 "
                f"(id={existing.skill_id})"
            )

        created = git_store.add_skill(
            skill,
            author="repository",
            commit=True,
            overwrite=False,
            event_action="create",
        )

        logger.info(f"Skill 已创建: {created.name} v{created.version}")
        return created

    async def update(self, skill_id: str, **kwargs: Any) -> Optional[Skill]:
        """部分更新 Skill 字段，覆盖当前版本文件。

        该操作不创建新版本。
        如需创建新版本，请使用 create_new_version。
        """
        skill = git_store.get_skill_by_id(skill_id)
        if not skill:
            return None

        kwargs.pop("skill_id", None)
        kwargs.pop("created_at", None)

        for key, value in kwargs.items():
            if key == "state" and isinstance(value, str):
                value = SkillState(value)
            elif key == "skill_type" and isinstance(value, str):
                value = SkillType(value)

            setattr(skill, key, value)

        skill.updated_at = datetime.utcnow()

        updated = git_store.update_skill_version(
            skill,
            author="repository",
            commit=True,
        )

        logger.info(f"Skill 已更新: {updated.name} v{updated.version}")
        return updated

    async def delete(self, skill_id: str) -> bool:
        """按 skill_id 软删除 Skill 当前版本。"""
        skill = git_store.get_skill_by_id(skill_id)
        if not skill:
            return False

        ok = git_store.delete_skill(
            skill.name,
            version=skill.version,
            hard=False,
            author="repository",
            reason="deleted by SkillWikiManager.delete",
            commit=True,
        )

        if ok:
            logger.info(f"Skill 已删除: {skill.name} v{skill.version}")

        return ok

    async def hard_delete(self, skill_id: str) -> bool:
        """按 skill_id 物理删除 Skill 当前版本文件。谨慎使用。"""
        skill = git_store.get_skill_by_id(skill_id, include_deleted=True)
        if not skill:
            return False

        ok = git_store.delete_skill(
            skill.name,
            version=skill.version,
            hard=True,
            author="repository",
            reason="hard deleted by SkillWikiManager.hard_delete",
            commit=True,
        )

        if ok:
            logger.info(f"Skill 已物理删除: {skill.name} v{skill.version}")

        return ok

    async def delete_by_name(
        self,
        name: str,
        version: Optional[str] = None,
        *,
        hard: bool = False,
        reason: Optional[str] = None,
    ) -> bool:
        """按 name 和可选 version 删除 Skill。"""
        ok = git_store.delete_skill(
            name,
            version=version,
            hard=hard,
            author="repository",
            reason=reason,
            commit=True,
        )

        if ok:
            logger.info(f"Skill 已删除: {name} {version or 'all'}")

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

        created = git_store.create_new_version(
            source.name,
            source_version=source.version,
            bump=bump,  # type: ignore[arg-type]
            overrides=overrides,
            author="repository",
            commit=True,
        )

        logger.info(
            f"Skill 新版本已创建: {created.name} "
            f"{source.version} -> {created.version}"
        )

        return created

    async def get_version_history(self, name: str) -> List[Skill]:
        """获取同名 Skill 的所有可见版本，按版本号排序。"""
        return git_store.get_version_history(name, include_deleted=False)

    async def get_version_history_with_deleted(self, name: str) -> List[Skill]:
        """获取同名 Skill 的所有版本，包括软删除版本。"""
        return git_store.get_version_history(name, include_deleted=True)

    async def diff_versions(self, name: str, v1: str, v2: str) -> str:
        """获取同一个 Skill 两个版本的 diff。"""
        return git_store.diff_versions(name, v1, v2)

    async def git_history(
        self,
        name: str,
        version: Optional[str] = None,
        max_count: int = 20,
    ) -> str:
        """获取某个 Skill 或某个版本文件的 Git 提交历史。"""
        return git_store.git_file_history(name, version, max_count=max_count)

    async def merge_versions(
        self,
        name: str,
        base_version: str,
        other_version: str,
        new_version: Optional[str] = None,
        strategy: str = "prefer_other",
        manual_overrides: Optional[Dict[str, Any]] = None,
    ) -> Skill:
        """合并同名 Skill 的两个版本，生成新版本。"""
        merged = git_store.merge_skills(
            name,
            base_version,
            other_version,
            new_version=new_version,
            strategy=strategy,  # type: ignore[arg-type]
            manual_overrides=manual_overrides,
            author="repository",
            commit=True,
        )

        logger.info(
            f"Skill 版本已合并: {name} "
            f"{base_version}+{other_version} -> {merged.version}"
        )

        return merged

    # ------------------------------------------------------------------
    # Lifecycle Transitions
    # ------------------------------------------------------------------

    async def transition_state(
        self,
        skill_id: str,
        new_state: SkillState,
        reason: Optional[str] = None,
    ) -> Skill:
        """执行状态转换，持久化到 Git 仓库。"""
        skill = await self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill 不存在: {skill_id}")

        updated = git_store.transition_skill_state(
            skill.name,
            skill.version,
            new_state,
            author="repository",
            reason=reason,
            commit=True,
        )

        logger.info(f"Skill 状态转换: {updated.name} → {new_state.value}")
        return updated

    async def release(self, skill_id: str) -> Skill:
        return await self.transition_state(skill_id, SkillState.RELEASED)

    async def deprecate(
        self,
        skill_id: str,
        reason: str,
        replacement_id: Optional[str] = None,
    ) -> Skill:
        skill = await self.transition_state(
            skill_id,
            SkillState.DEPRECATED,
            reason=reason,
        )

        if replacement_id:
            skill.replacement_skill_id = replacement_id
            skill.updated_at = datetime.utcnow()
            git_store.update_skill_version(
                skill,
                author="repository",
                commit=True,
            )

        return skill

    async def archive(self, skill_id: str) -> Skill:
        return await self.transition_state(skill_id, SkillState.ARCHIVED)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    async def record_execution(
        self,
        skill_id: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """记录执行结果，更新当前版本 JSON 文件。"""
        skill = await self.get(skill_id)
        if not skill:
            return

        skill.record_execution(success, latency_ms)

        git_store.update_skill_version(
            skill,
            author="repository",
            commit=True,
        )

        logger.info(
            f"Skill 执行记录已更新: {skill.name} v{skill.version}, "
            f"success={success}, latency_ms={latency_ms}"
        )

    async def get_overview_stats(self) -> Dict[str, Any]:
        """获取 Skill 仓库总览统计。"""
        rows = git_store.list_skills(
            latest_only=True,
            include_deleted=False,
            limit=100000,
        )

        total = len(rows)
        released = len([r for r in rows if r.get("state") == SkillState.RELEASED.value])
        draft = len([r for r in rows if r.get("state") == SkillState.DRAFT.value])
        atomic = len([r for r in rows if r.get("skill_type") == SkillType.ATOMIC.value])
        functional = len([r for r in rows if r.get("skill_type") == SkillType.FUNCTIONAL.value])
        strategic = len([r for r in rows if r.get("skill_type") == SkillType.STRATEGIC.value])

        return {
            "total_skills": total,
            "released": released,
            "draft": draft,
            "atomic": atomic,
            "functional": functional,
            "strategic": strategic,
            "computed_at": datetime.utcnow().isoformat(),
            "backend": "git",
            "repo_status": git_store.repo_status(),
        }

    # ------------------------------------------------------------------
    # Repository maintenance
    # ------------------------------------------------------------------

    async def rebuild_index(self) -> Dict[str, Any]:
        """重建 SkillStorage 全局索引。"""
        return git_store.rebuild_index(commit=True)

    async def repo_status(self) -> Dict[str, Any]:
        """获取本地 Git 仓库状态。"""
        return git_store.repo_status()

    async def read_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取生命周期事件日志。"""
        return git_store.read_events(limit=limit)

    async def push_to_remote(
        self,
        remote_name: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> str:
        """推送本地 Git 仓库到远程备份。"""
        return git_store.push_to_remote(remote_name, branch)

    async def pull_from_remote(
        self,
        remote_name: Optional[str] = None,
        branch: Optional[str] = None,
        rebase: bool = True,
    ) -> str:
        """从远程拉取。

        当前设计中本地服务器仓库是事实源，因此建议只在初始化或灾备时调用。
        """
        return git_store.pull_from_remote(
            remote_name=remote_name,
            branch=branch,
            rebase=rebase,
        )

    async def sync_to_remote(self) -> str:
        """提交本地未提交变更并推送到远程备份。"""
        return git_store.sync_to_remote()
