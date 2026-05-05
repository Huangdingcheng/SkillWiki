"""Skill release tags and restore-commit rollback utilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ...models.skill_model import Skill
from .git_version_store import GitVersionStore
from .skill_snapshot import skill_snapshot_path


@dataclass(frozen=True)
class SkillReleaseRecord:
    tag_name: str
    commit: str
    snapshot_path: str
    skill_id: str
    skill_name: str
    version: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag_name": self.tag_name,
            "commit": self.commit,
            "snapshot_path": self.snapshot_path,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "version": self.version,
        }


@dataclass(frozen=True)
class SkillRollbackRecord:
    source_ref: str
    restore_commit: str
    restored_snapshot_path: str
    commit_message: str
    skill_id: str
    skill_name: str
    version: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "restore_commit": self.restore_commit,
            "restored_snapshot_path": self.restored_snapshot_path,
            "commit_message": self.commit_message,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "version": self.version,
        }


def skill_release_tag_name(skill: Skill) -> str:
    safe_name = _slug(skill.name)
    skill_id_prefix = _slug(skill.skill_id[:8])
    safe_version = _slug(skill.version)
    return f"skill/{safe_name}/{skill_id_prefix}/v{safe_version}"


def release_skill_snapshot(
    repo_path: str | Path,
    skill: Skill,
    ref: str = "HEAD",
    store: Optional[GitVersionStore] = None,
) -> SkillReleaseRecord:
    version_store = store or GitVersionStore(repo_path)
    tag_name = skill_release_tag_name(skill)
    snapshot_path = skill_snapshot_path(skill)

    if version_store.tag_exists(tag_name):
        raise ValueError(f"Skill release tag already exists: {tag_name}")

    version_store.read_file_at_ref(ref, snapshot_path)
    version_store.create_tag(tag_name, ref)

    return SkillReleaseRecord(
        tag_name=tag_name,
        commit=version_store.head_commit() if ref == "HEAD" else ref,
        snapshot_path=snapshot_path,
        skill_id=skill.skill_id,
        skill_name=skill.name,
        version=skill.version,
    )


def read_skill_snapshot_at_ref(
    repo_path: str | Path,
    ref: str,
    snapshot_path: str,
    store: Optional[GitVersionStore] = None,
) -> Dict[str, Any]:
    version_store = store or GitVersionStore(repo_path)
    return json.loads(version_store.read_file_at_ref(ref, snapshot_path))


def restore_skill_snapshot(
    repo_path: str | Path,
    current_skill: Skill,
    source_ref: str,
    store: Optional[GitVersionStore] = None,
) -> SkillRollbackRecord:
    version_store = store or GitVersionStore(repo_path)
    snapshot_path = skill_snapshot_path(current_skill)
    snapshot = read_skill_snapshot_at_ref(repo_path, source_ref, snapshot_path, version_store)

    target = Path(repo_path) / snapshot_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    commit_message = f"skill({current_skill.name}): restore from {source_ref}"
    restore_commit = version_store.commit_paths([snapshot_path], commit_message)

    return SkillRollbackRecord(
        source_ref=source_ref,
        restore_commit=restore_commit,
        restored_snapshot_path=snapshot_path,
        commit_message=commit_message,
        skill_id=current_skill.skill_id,
        skill_name=current_skill.name,
        version=current_skill.version,
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = re.sub(r"-+", "-", slug).strip("-._")
    return slug or "unknown"
