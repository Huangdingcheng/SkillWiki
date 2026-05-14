"""Skill change workflow backed by Git branches and snapshot commits."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill
from .git_version_store import GitVersionStore, GitVersionStoreError
from .skill_snapshot import (
    SkillSnapshotDiff,
    diff_skill_snapshots,
    has_breaking_changes,
    review_recommendation_for_diffs,
    skill_snapshot_path,
    skill_to_snapshot,
    write_skill_snapshot,
)


@dataclass(frozen=True)
class SkillChangeReviewBundle:
    """Review bundle for a proposed Skill snapshot change."""

    branch_name: str
    base_commit: str
    head_commit: str
    snapshot_path: str
    commit_message: str
    diffs: List[SkillSnapshotDiff]
    has_breaking_changes: bool
    suggested_review_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch_name": self.branch_name,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "snapshot_path": self.snapshot_path,
            "commit_message": self.commit_message,
            "diffs": [diff.to_dict() for diff in self.diffs],
            "has_breaking_changes": self.has_breaking_changes,
            "suggested_review_status": self.suggested_review_status,
        }


def skill_change_branch_name(skill: Skill) -> str:
    """Build a deterministic branch name for a proposed Skill version."""
    safe_name = _slug(skill.name)
    skill_id_prefix = _slug(skill.skill_id[:8])
    safe_version = _slug(skill.version)
    return f"skill/{safe_name}/{skill_id_prefix}-v{safe_version}"


def skill_change_commit_message(skill: Skill) -> str:
    return f"skill({skill.name}): propose v{skill.version}"


def propose_skill_change(
    repo_path: str | Path,
    old_skill: Skill,
    new_skill: Skill,
    store: Optional[GitVersionStore] = None,
    author_name: str = "SkillOS",
) -> SkillChangeReviewBundle:
    """Create a Git branch and snapshot commit for a proposed Skill change."""
    version_store = store or GitVersionStore(repo_path)
    base_branch = version_store.current_branch()
    base_commit = version_store.head_commit()
    branch_name = skill_change_branch_name(new_skill)
    snapshot_path = skill_snapshot_path(new_skill)
    commit_message = skill_change_commit_message(new_skill)
    diffs = diff_skill_snapshots(skill_to_snapshot(old_skill), skill_to_snapshot(new_skill))
    breaking = has_breaking_changes(diffs)

    if not diffs:
        return SkillChangeReviewBundle(
            branch_name=branch_name,
            base_commit=base_commit,
            head_commit=base_commit,
            snapshot_path=snapshot_path,
            commit_message="",
            diffs=[],
            has_breaking_changes=False,
            suggested_review_status="no_changes",
        )

    if version_store.branch_exists(branch_name):
        raise GitVersionStoreError(f"Git branch already exists: {branch_name}")

    with version_store.lock():
        try:
            version_store.create_branch(branch_name, base_commit)
            version_store.checkout(branch_name)
            written_path = write_skill_snapshot(repo_path, new_skill)
            head_commit = version_store.commit_paths(
                [written_path],
                commit_message,
                author_name=author_name,
            )
        finally:
            version_store.checkout(base_branch)

    return SkillChangeReviewBundle(
        branch_name=branch_name,
        base_commit=base_commit,
        head_commit=head_commit,
        snapshot_path=snapshot_path,
        commit_message=commit_message,
        diffs=diffs,
        has_breaking_changes=breaking,
        suggested_review_status=review_recommendation_for_diffs(diffs),
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = re.sub(r"-+", "-", slug).strip("-._")
    return slug or "unknown"
