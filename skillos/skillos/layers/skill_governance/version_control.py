"""Skill 版本控制 — 语义化版本管理、变更记录、版本比较。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ...models.skill_model import Skill, SkillState
from ...utils.logger import get_logger

logger = get_logger(__name__)


class ChangeType(str, Enum):
    CREATED = "created"
    INTERFACE_CHANGED = "interface_changed"   # 接口变更（breaking）
    IMPLEMENTATION_CHANGED = "implementation_changed"
    DESCRIPTION_UPDATED = "description_updated"
    STATE_TRANSITIONED = "state_transitioned"
    TAGS_UPDATED = "tags_updated"
    MERGED = "merged"
    SPLIT = "split"
    REPAIRED = "repaired"


@dataclass
class ChangeRecord:
    """单次变更记录（类似 git commit）。"""
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    skill_id: str = ""
    skill_name: str = ""
    from_version: str = ""
    to_version: str = ""
    change_type: ChangeType = ChangeType.CREATED
    summary: str = ""
    diff: Dict[str, Any] = field(default_factory=dict)   # {field: (old, new)}
    author: str = "system"
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_breaking(self) -> bool:
        """是否为破坏性变更（接口变更）。"""
        return self.change_type == ChangeType.INTERFACE_CHANGED


class VersionController:
    """Skill 版本控制器。

    职责：
    - 记录每次 Skill 变更
    - 计算版本差异
    - 决定版本号递增策略（major/minor/patch）
    - 维护变更历史
    """

    def __init__(self) -> None:
        # 内存中的变更历史（生产环境应持久化到 DB）
        self._history: Dict[str, List[ChangeRecord]] = {}

    def record_change(
        self,
        skill: Skill,
        change_type: ChangeType,
        summary: str,
        diff: Optional[Dict[str, Any]] = None,
        author: str = "system",
        from_version: str = "",
    ) -> ChangeRecord:
        """记录一次变更。"""
        record = ChangeRecord(
            skill_id=skill.skill_id,
            skill_name=skill.name,
            from_version=from_version or skill.version,
            to_version=skill.version,
            change_type=change_type,
            summary=summary,
            diff=diff or {},
            author=author,
        )
        self._history.setdefault(skill.skill_id, []).append(record)
        logger.debug(f"变更记录: {skill.name} [{change_type.value}] {summary}")
        return record

    def get_history(self, skill_id: str) -> List[ChangeRecord]:
        """获取 Skill 的完整变更历史（时间正序）。"""
        return list(self._history.get(skill_id, []))

    def get_latest_change(self, skill_id: str) -> Optional[ChangeRecord]:
        history = self._history.get(skill_id, [])
        return history[-1] if history else None

    def compute_diff(self, old_skill: Skill, new_skill: Skill) -> Dict[str, Any]:
        """计算两个 Skill 版本之间的差异。"""
        diff: Dict[str, Any] = {}
        fields_to_compare = [
            "name", "description", "skill_type", "domain",
            "granularity_level", "state", "tags",
        ]
        for f in fields_to_compare:
            old_val = getattr(old_skill, f, None)
            new_val = getattr(new_skill, f, None)
            if old_val != new_val:
                diff[f] = {"old": old_val, "new": new_val}

        # 接口比较
        old_iface = old_skill.interface.model_dump()
        new_iface = new_skill.interface.model_dump()
        if old_iface != new_iface:
            diff["interface"] = {"old": old_iface, "new": new_iface}

        return diff

    def suggest_version_bump(self, diff: Dict[str, Any]) -> str:
        """根据变更内容建议版本号递增策略。"""
        if "interface" in diff:
            # 接口变更 → major
            return "major"
        if any(k in diff for k in ("skill_type", "domain", "granularity_level")):
            # 分类变更 → minor
            return "minor"
        # 其他变更 → patch
        return "patch"

    def determine_change_type(self, diff: Dict[str, Any]) -> ChangeType:
        """根据 diff 判断变更类型。"""
        if "interface" in diff:
            return ChangeType.INTERFACE_CHANGED
        if "description" in diff:
            return ChangeType.DESCRIPTION_UPDATED
        if "tags" in diff:
            return ChangeType.TAGS_UPDATED
        if "state" in diff:
            return ChangeType.STATE_TRANSITIONED
        return ChangeType.IMPLEMENTATION_CHANGED

    def create_new_version(
        self,
        old_skill: Skill,
        new_skill: Skill,
        author: str = "system",
    ) -> Tuple[Skill, ChangeRecord]:
        """基于 diff 自动决定版本号并记录变更。"""
        diff = self.compute_diff(old_skill, new_skill)
        bump = self.suggest_version_bump(diff)
        change_type = self.determine_change_type(diff)

        new_skill.bump_version(bump)
        new_skill.state = SkillState.DRAFT  # 新版本回到 Draft

        summary = f"版本 {old_skill.version} → {new_skill.version}: {', '.join(diff.keys())}"
        record = self.record_change(
            new_skill,
            change_type,
            summary,
            diff=diff,
            author=author,
            from_version=old_skill.version,
        )
        return new_skill, record
