"""Git-backed SkillStorage utilities.

This module is the production-oriented version of the repo-version idea:
Skill data is persisted as versioned JSON files in a local Git repository,
with manifest, index, and event-log files kept alongside the skill payloads.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from ...models.skill_model import (
    Skill,
    SkillMetrics,
    SkillProvenance,
    SkillState,
    SkillType,
)

VersionBump = Literal["major", "minor", "patch"]
MergeStrategy = Literal["prefer_other", "prefer_base", "append_lists"]

VERSION_MANIFEST = "versions.json"
DEFAULT_REPO_NAME = "SkillStorage"


def default_storage_dir() -> Path:
    env_path = os.getenv("SKILLOS_SKILL_STORAGE_DIR")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (Path(__file__).resolve().parent / DEFAULT_REPO_NAME).resolve()


class GitSkillStore:
    """Local Git-backed repository for versioned Skill objects."""

    def __init__(
        self,
        base_dir: Optional[str | Path] = None,
        *,
        auto_commit: bool = True,
    ) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve() if base_dir else default_storage_dir()
        self.skills_dir = self.base_dir / "skills"
        self.metadata_dir = self.base_dir / "metadata"
        self.index_file = self.metadata_dir / "skills_index.json"
        self.events_file = self.metadata_dir / "events.jsonl"
        self.config_file = self.base_dir / "skill_repo_config.json"
        self.readme_file = self.base_dir / "README.md"
        self.auto_commit = auto_commit
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Init / status
    # ------------------------------------------------------------------

    def init_repo(
        self,
        *,
        remote_url: Optional[str] = None,
        default_branch: str = "main",
        initial_commit: bool = True,
        reset: bool = False,
    ) -> None:
        """Initialize the local SkillStorage repo."""
        with self._lock:
            if reset and self.base_dir.exists():
                shutil.rmtree(self.base_dir)

            self.skills_dir.mkdir(parents=True, exist_ok=True)
            self.metadata_dir.mkdir(parents=True, exist_ok=True)
            self._write_json_if_missing(self.index_file, {})
            if not self.events_file.exists():
                self.events_file.touch()
            self._write_json_if_missing(
                self.config_file,
                {
                    "repo_name": DEFAULT_REPO_NAME,
                    "local_repo_path": str(self.base_dir),
                    "default_branch": default_branch,
                    "remote_name": "origin",
                    "remote_url": remote_url or "",
                    "auto_commit": self.auto_commit,
                    "storage_layout_version": 1,
                },
            )
            if not self.readme_file.exists():
                self.readme_file.write_text(
                    "# SkillStorage\n\n"
                    "Git-backed Skill storage repository for SkillOS.\n\n"
                    "- `skills/{skill_name}/{version}.json`: immutable skill version payload\n"
                    "- `skills/{skill_name}/versions.json`: per-skill version manifest\n"
                    "- `metadata/skills_index.json`: query index\n"
                    "- `metadata/events.jsonl`: append-only lifecycle event log\n",
                    encoding="utf-8",
                )

            if not self._is_git_repo():
                self._run_git(["init"], check=True)
                self._run_git(["checkout", "-B", default_branch], check=False)

            self._run_git(["config", "user.name", "SkillOS Bot"], check=False)
            self._run_git(["config", "user.email", "skillos-bot@example.com"], check=False)
            self._run_git(["config", "commit.gpgsign", "false"], check=False)
            if remote_url:
                remotes = self._run_git(["remote"], check=False, capture=True).stdout.splitlines()
                if "origin" in remotes:
                    self._run_git(["remote", "set-url", "origin", remote_url], check=False)
                else:
                    self._run_git(["remote", "add", "origin", remote_url], check=False)

            if initial_commit:
                self._append_event({"action": "init_repo", "reset": reset})
                self._git_commit("Initialize SkillStorage repository")

    def repo_status(self) -> Dict[str, Any]:
        self.init_repo(initial_commit=False)
        git_status = self._run_git(["status", "--short"], check=False, capture=True).stdout
        branch = self._run_git(["branch", "--show-current"], check=False, capture=True).stdout.strip()
        config = self._read_json(self.config_file, {})
        return {
            "backend": "git",
            "base_dir": str(self.base_dir),
            "is_git_repo": self._is_git_repo(),
            "branch": branch,
            "remote_name": config.get("remote_name", "origin"),
            "remote_url": config.get("remote_url", ""),
            "dirty": bool(git_status.strip()),
            "status": git_status,
        }

    # ------------------------------------------------------------------
    # CRUD / versions
    # ------------------------------------------------------------------

    def add_skill(
        self,
        skill: Skill,
        *,
        author: str = "system",
        commit: Optional[bool] = None,
        overwrite: bool = False,
        event_action: str = "create",
        event_extra: Optional[Dict[str, Any]] = None,
    ) -> Skill:
        """Create a skill version file, or overwrite the same version when allowed."""
        with self._lock:
            self.init_repo(initial_commit=False)
            skill_file = self._skill_file(skill.name, skill.version)
            if skill_file.exists() and not overwrite:
                raise ValueError(f"Skill already exists: {skill.name} v{skill.version}")

            skill.updated_at = datetime.utcnow()
            self._write_json(skill_file, self._skill_to_dict(skill))

            manifest = self._load_manifest(skill.name)
            versions = manifest.setdefault("versions", {})
            versions[skill.version] = self._version_meta_from_skill(
                skill,
                source=event_action,
                old_meta=versions.get(skill.version),
            )
            manifest["deleted"] = False
            manifest["latest_version"] = self._latest_version_from_versions(versions)
            self._save_manifest(skill.name, manifest)
            self._update_index_for_skill(skill.name)
            event = {
                "action": event_action,
                "skill": skill.name,
                "version": skill.version,
                "skill_id": skill.skill_id,
                "state": self._state_value(skill.state),
                "author": author,
                "overwrite": overwrite,
            }
            if event_extra:
                event.update(event_extra)
            self._append_event(event)
            self._maybe_commit(commit, f"{event_action}: {skill.name} v{skill.version}")
            return skill

    def update_skill_version(
        self,
        skill: Skill,
        *,
        author: str = "system",
        commit: Optional[bool] = None,
        event_action: str = "update",
        event_extra: Optional[Dict[str, Any]] = None,
    ) -> Skill:
        return self.add_skill(
            skill,
            author=author,
            commit=commit,
            overwrite=True,
            event_action=event_action,
            event_extra=event_extra,
        )

    def get_skill(
        self,
        skill_name: str,
        version: Optional[str] = None,
        *,
        include_deleted: bool = False,
    ) -> Optional[Skill]:
        self.init_repo(initial_commit=False)
        manifest = self._load_manifest(skill_name)
        versions = manifest.get("versions", {})
        target_version = version or manifest.get("latest_version")
        if not target_version and include_deleted and versions:
            target_version = sorted(versions, key=self._semver_key)[-1]
        if not target_version:
            return None
        meta = versions.get(target_version, {})
        if meta.get("deleted") and not include_deleted:
            return None
        path = self._skill_file(skill_name, target_version)
        if not path.exists():
            return None
        data = self._read_json(path, None)
        return self._dict_to_skill(data) if isinstance(data, dict) else None

    def get_skill_by_id(self, skill_id: str, *, include_deleted: bool = False) -> Optional[Skill]:
        for row in self.list_skills(
            latest_only=False,
            include_deleted=include_deleted,
            limit=100000,
        ):
            if row.get("skill_id") == skill_id:
                return self.get_skill(row["name"], row["version"], include_deleted=include_deleted)
        return None

    def get_skill_versions(self, skill_name: str, *, include_deleted: bool = False) -> List[str]:
        self.init_repo(initial_commit=False)
        manifest = self._load_manifest(skill_name)
        versions = manifest.get("versions", {})
        visible = [
            version
            for version, meta in versions.items()
            if include_deleted or not meta.get("deleted", False)
        ]
        return sorted(visible, key=self._semver_key)

    def get_version_history(self, skill_name: str, *, include_deleted: bool = False) -> List[Skill]:
        history: List[Skill] = []
        for version in self.get_skill_versions(skill_name, include_deleted=include_deleted):
            skill = self.get_skill(skill_name, version, include_deleted=include_deleted)
            if skill:
                history.append(skill)
        return history

    def list_skills(
        self,
        *,
        state: Optional[SkillState | str] = None,
        skill_type: Optional[SkillType | str] = None,
        domain: Optional[str] = None,
        name_like: Optional[str] = None,
        tags: Optional[List[str]] = None,
        latest_only: bool = True,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        self.init_repo(initial_commit=False)
        if not self.index_file.exists():
            self.rebuild_index(commit=False)

        state_value = self._state_value(state) if state else None
        type_value = self._skill_type_value(skill_type) if skill_type else None
        tag_set = {tag.strip().lower() for tag in tags or [] if tag.strip()}
        name_query = name_like.lower() if name_like else None
        index = self._read_json(self.index_file, {})

        rows: List[Dict[str, Any]] = []
        for skill_name, meta in index.items():
            if meta.get("deleted") and not include_deleted:
                continue

            versions = meta.get("versions", {})
            if latest_only:
                latest = meta.get("latest_version")
                if not latest and include_deleted and versions:
                    latest = sorted(versions, key=self._semver_key)[-1]
                versions = {latest: versions.get(latest, {})} if latest else {}

            for version, version_meta in versions.items():
                if not version:
                    continue
                if version_meta.get("deleted") and not include_deleted:
                    continue
                row = {
                    "name": skill_name,
                    "version": version,
                    **meta,
                    **version_meta,
                }
                if state_value and row.get("state") != state_value:
                    continue
                if type_value and row.get("skill_type") != type_value:
                    continue
                if domain and row.get("domain") != domain:
                    continue
                if name_query and name_query not in skill_name.lower():
                    continue
                if tag_set and not (tag_set & set(row.get("tags", []))):
                    continue
                rows.append(row)

        rows.sort(key=lambda row: str(row.get("updated_at", "")), reverse=True)
        return rows[offset : offset + limit]

    def delete_skill(
        self,
        skill_name: str,
        version: Optional[str] = None,
        *,
        hard: bool = False,
        author: str = "system",
        reason: Optional[str] = None,
        commit: Optional[bool] = None,
    ) -> bool:
        with self._lock:
            self.init_repo(initial_commit=False)
            skill_dir = self._skill_dir(skill_name)
            if not skill_dir.exists():
                return False

            manifest = self._load_manifest(skill_name)
            versions = manifest.setdefault("versions", {})
            target_versions = [version] if version else list(versions)
            changed = False

            for target in target_versions:
                if not target:
                    continue
                file_path = self._skill_file(skill_name, target)
                if hard:
                    if file_path.exists():
                        file_path.unlink()
                    versions.pop(target, None)
                    changed = True
                elif target in versions:
                    versions[target]["deleted"] = True
                    versions[target]["deleted_at"] = self._utc_now()
                    versions[target]["delete_reason"] = reason or ""
                    changed = True

            if not changed:
                return False

            if hard and not versions and skill_dir.exists():
                shutil.rmtree(skill_dir)
            else:
                manifest["versions"] = versions
                manifest["latest_version"] = self._latest_version_from_versions(versions)
                manifest["deleted"] = manifest["latest_version"] is None
                self._save_manifest(skill_name, manifest)

            self._update_index_for_skill(skill_name)
            self._append_event(
                {
                    "action": "delete",
                    "skill": skill_name,
                    "version": version,
                    "hard": hard,
                    "author": author,
                    "reason": reason,
                }
            )
            self._maybe_commit(commit, f"delete: {skill_name} {version or 'all'}")
            return True

    def create_new_version(
        self,
        skill_name: str,
        source_version: Optional[str] = None,
        *,
        bump: VersionBump = "patch",
        overrides: Optional[Dict[str, Any]] = None,
        author: str = "system",
        commit: Optional[bool] = None,
    ) -> Skill:
        source = self.get_skill(skill_name, source_version)
        if not source:
            raise ValueError(f"Source Skill does not exist: {skill_name} {source_version or '<latest>'}")

        new_skill = source.model_copy(deep=True)
        new_skill.skill_id = str(uuid.uuid4())
        new_skill.version = self._next_version(source.version, bump)
        new_skill.state = SkillState.DRAFT
        new_skill.created_at = datetime.utcnow()
        new_skill.updated_at = datetime.utcnow()
        new_skill.released_at = None
        new_skill.deprecated_at = None
        new_skill.metrics = SkillMetrics()

        if new_skill.provenance:
            parent_ids = set(new_skill.provenance.parent_skill_ids)
            parent_ids.add(source.skill_id)
            new_skill.provenance.parent_skill_ids = list(parent_ids)
            new_skill.provenance.source_type = "adapt"
            new_skill.provenance.creation_context.update({"source_version": source.version})
        else:
            new_skill.provenance = SkillProvenance(
                source_type="adapt",
                parent_skill_ids=[source.skill_id],
                creation_context={"source_version": source.version},
            )

        for key, value in (overrides or {}).items():
            setattr(new_skill, key, value)

        return self.add_skill(new_skill, author=author, commit=commit, event_action="new_version")

    def transition_skill_state(
        self,
        skill_name: str,
        version: str,
        new_state: SkillState,
        *,
        author: str = "system",
        reason: Optional[str] = None,
        commit: Optional[bool] = None,
    ) -> Skill:
        skill = self.get_skill(skill_name, version)
        if not skill:
            raise ValueError(f"Skill does not exist: {skill_name} v{version}")
        old_state = skill.state
        skill.transition_to(new_state)
        if new_state == SkillState.DEPRECATED and reason:
            skill.deprecation_reason = reason
        updated = self.update_skill_version(
            skill,
            author=author,
            commit=commit,
            event_action="transition",
            event_extra={
                "action": "transition",
                "skill": skill_name,
                "version": version,
                "from_state": self._state_value(old_state),
                "to_state": self._state_value(new_state),
                "author": author,
                "reason": reason,
            },
        )
        return updated

    # ------------------------------------------------------------------
    # Diff / merge / history / sync
    # ------------------------------------------------------------------

    def diff_versions(self, skill_name: str, v1: str, v2: str, *, context_lines: int = 3) -> str:
        self.init_repo(initial_commit=False)
        file1 = self._skill_file(skill_name, v1)
        file2 = self._skill_file(skill_name, v2)
        if not file1.exists() or not file2.exists():
            raise FileNotFoundError(f"Skill version not found: {skill_name} {v1} or {v2}")
        a = file1.read_text(encoding="utf-8").splitlines(keepends=True)
        b = file2.read_text(encoding="utf-8").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                a,
                b,
                fromfile=f"{skill_name}/{v1}.json",
                tofile=f"{skill_name}/{v2}.json",
                n=context_lines,
            )
        )

    def git_file_history(self, skill_name: str, version: Optional[str] = None, *, max_count: int = 20) -> str:
        self.init_repo(initial_commit=False)
        path = self._skill_file(skill_name, version) if version else self._skill_dir(skill_name)
        result = self._run_git(
            ["log", f"--max-count={max_count}", "--oneline", "--", str(path.relative_to(self.base_dir))],
            check=False,
            capture=True,
        )
        return (result.stdout or "") + (result.stderr or "")

    def merge_skills(
        self,
        skill_name: str,
        base_version: str,
        other_version: str,
        new_version: Optional[str] = None,
        *,
        strategy: MergeStrategy = "prefer_other",
        manual_overrides: Optional[Dict[str, Any]] = None,
        author: str = "system",
        commit: Optional[bool] = None,
    ) -> Skill:
        base = self.get_skill(skill_name, base_version)
        other = self.get_skill(skill_name, other_version)
        if not base or not other:
            raise ValueError(f"Cannot merge missing Skill versions: {skill_name} {base_version}, {other_version}")

        merged_data = self._skill_to_dict(base)
        other_data = self._skill_to_dict(other)
        for key, value in other_data.items():
            if key in {"skill_id", "version", "created_at", "updated_at", "released_at", "deprecated_at"}:
                continue
            merged_data[key] = self._merge_value(merged_data.get(key), value, strategy)
        if manual_overrides:
            merged_data.update(manual_overrides)

        source_version = max(base.version, other.version, key=self._semver_key)
        merged_data["skill_id"] = str(uuid.uuid4())
        merged_data["version"] = new_version or self._next_version(source_version, "patch")
        merged_data["state"] = SkillState.DRAFT.value
        merged_data["created_at"] = self._utc_now()
        merged_data["updated_at"] = self._utc_now()
        merged_data["released_at"] = None
        merged_data["deprecated_at"] = None
        provenance = merged_data.get("provenance") or {}
        provenance.update(
            {
                "source_type": "merge",
                "parent_skill_ids": [base.skill_id, other.skill_id],
                "creation_context": {
                    "base_version": base.version,
                    "other_version": other.version,
                    "strategy": strategy,
                },
            }
        )
        merged_data["provenance"] = provenance
        merged = self._dict_to_skill(merged_data)
        return self.add_skill(merged, author=author, commit=commit, event_action="merge")

    def rebuild_index(self, *, commit: Optional[bool] = None) -> Dict[str, Any]:
        with self._lock:
            self.init_repo(initial_commit=False)
            index: Dict[str, Any] = {}
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_dir in sorted(path for path in self.skills_dir.iterdir() if path.is_dir()):
                self._rebuild_manifest_for_skill(skill_dir.name)
                self._update_index_for_skill(skill_dir.name, index=index)
            self._write_json(self.index_file, index)
            self._append_event({"action": "rebuild_index", "skill_count": len(index)})
            self._maybe_commit(commit, "Rebuild SkillStorage index")
            return index

    def read_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.events_file.exists():
            return []
        lines = self.events_file.read_text(encoding="utf-8").splitlines()[-limit:]
        events: List[Dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def push_to_remote(self, remote_name: Optional[str] = None, branch: Optional[str] = None) -> str:
        self.init_repo(initial_commit=False)
        config = self._read_json(self.config_file, {})
        remote = remote_name or config.get("remote_name", "origin")
        target_branch = branch or config.get("default_branch", "main")
        result = self._run_git(["push", "-u", remote, target_branch], check=False, capture=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        self._append_event({"action": "push", "remote": remote, "branch": target_branch})
        return result.stdout + result.stderr

    def pull_from_remote(self, remote_name: Optional[str] = None, branch: Optional[str] = None) -> str:
        self.init_repo(initial_commit=False)
        config = self._read_json(self.config_file, {})
        remote = remote_name or config.get("remote_name", "origin")
        target_branch = branch or config.get("default_branch", "main")
        result = self._run_git(["pull", "--rebase", remote, target_branch], check=False, capture=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        self._append_event({"action": "pull", "remote": remote, "branch": target_branch})
        self.rebuild_index(commit=False)
        return result.stdout + result.stderr

    def sync_to_remote(self) -> str:
        self._git_commit("Sync pending SkillStorage changes")
        return self.push_to_remote()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _skill_dir(self, skill_name: str) -> Path:
        return self.skills_dir / skill_name

    def _skill_file(self, skill_name: str, version: str) -> Path:
        return self._skill_dir(skill_name) / f"{version}.json"

    def _manifest_file(self, skill_name: str) -> Path:
        return self._skill_dir(skill_name) / VERSION_MANIFEST

    def _load_manifest(self, skill_name: str) -> Dict[str, Any]:
        return self._read_json(
            self._manifest_file(skill_name),
            {
                "name": skill_name,
                "latest_version": None,
                "versions": {},
                "deleted": False,
                "created_at": self._utc_now(),
                "updated_at": self._utc_now(),
            },
        )

    def _save_manifest(self, skill_name: str, manifest: Dict[str, Any]) -> None:
        manifest["name"] = skill_name
        manifest["updated_at"] = self._utc_now()
        self._write_json(self._manifest_file(skill_name), manifest)

    def _rebuild_manifest_for_skill(self, skill_name: str) -> Dict[str, Any]:
        manifest = self._load_manifest(skill_name)
        versions = manifest.setdefault("versions", {})
        for file_path in sorted(self._skill_dir(skill_name).glob("*.json")):
            if file_path.name == VERSION_MANIFEST:
                continue
            data = self._read_json(file_path, {})
            if not isinstance(data, dict):
                continue
            version = file_path.stem
            old_meta = versions.get(version, {})
            versions[version] = {
                "version": version,
                "skill_id": data.get("skill_id"),
                "state": data.get("state"),
                "skill_type": data.get("skill_type"),
                "domain": data.get("domain"),
                "display_name": data.get("display_name"),
                "description": data.get("description"),
                "tags": data.get("tags", []),
                "file": file_path.name,
                "deleted": old_meta.get("deleted", False),
                "created_at": old_meta.get("created_at", data.get("created_at", self._utc_now())),
                "updated_at": data.get("updated_at", old_meta.get("updated_at", self._utc_now())),
                "source": old_meta.get("source", "file_scan"),
            }
        manifest["versions"] = versions
        manifest["latest_version"] = self._latest_version_from_versions(versions)
        manifest["deleted"] = manifest["latest_version"] is None
        self._save_manifest(skill_name, manifest)
        return manifest

    def _update_index_for_skill(self, skill_name: str, *, index: Optional[Dict[str, Any]] = None) -> None:
        owns_index = index is None
        index = self._read_json(self.index_file, {}) if index is None else index
        manifest = self._load_manifest(skill_name)
        versions = manifest.get("versions", {})
        latest = manifest.get("latest_version")
        if not versions:
            index.pop(skill_name, None)
            if owns_index:
                self._write_json(self.index_file, index)
            return

        latest_meta = versions.get(latest, {}) if latest else {}
        index[skill_name] = {
            "name": skill_name,
            "latest_version": latest,
            "deleted": manifest.get("deleted", latest is None),
            "skill_id": latest_meta.get("skill_id"),
            "state": latest_meta.get("state"),
            "skill_type": latest_meta.get("skill_type"),
            "domain": latest_meta.get("domain"),
            "display_name": latest_meta.get("display_name"),
            "description": latest_meta.get("description"),
            "tags": latest_meta.get("tags", []),
            "version_count": len([v for v in versions.values() if not v.get("deleted", False)]),
            "versions": versions,
            "updated_at": manifest.get("updated_at", self._utc_now()),
        }
        if owns_index:
            self._write_json(self.index_file, index)

    def _version_meta_from_skill(
        self,
        skill: Skill,
        *,
        source: str,
        old_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        old_meta = old_meta or {}
        return {
            "version": skill.version,
            "skill_id": skill.skill_id,
            "state": self._state_value(skill.state),
            "skill_type": self._skill_type_value(skill.skill_type),
            "domain": skill.domain,
            "display_name": skill.display_name,
            "description": skill.description,
            "tags": skill.tags,
            "file": f"{skill.version}.json",
            "deleted": False,
            "created_at": old_meta.get("created_at", self._utc_now()),
            "updated_at": self._utc_now(),
            "source": source,
        }

    def _append_event(self, event: Dict[str, Any]) -> None:
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        event.setdefault("event_id", str(uuid.uuid4()))
        event.setdefault("time", self._utc_now())
        with self.events_file.open("a", encoding="utf-8") as handle:
            json.dump(event, handle, ensure_ascii=False, default=str)
            handle.write("\n")

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError:
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
        tmp_path.replace(path)

    def _write_json_if_missing(self, path: Path, data: Any) -> None:
        if not path.exists():
            self._write_json(path, data)

    def _run_git(
        self,
        args: List[str],
        *,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            ["git", *args],
            cwd=self.base_dir,
            check=check,
            capture_output=capture,
            text=True,
        )

    def _is_git_repo(self) -> bool:
        return (self.base_dir / ".git").exists()

    def _git_commit(self, message: str) -> bool:
        if not self._is_git_repo():
            return False
        self._run_git(["add", "."], check=True)
        status = self._run_git(["status", "--porcelain"], check=False, capture=True).stdout
        if not status.strip():
            return False
        result = self._run_git(["commit", "-m", message], check=False, capture=True)
        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            if "nothing to commit" in output.lower():
                return False
            raise RuntimeError(output)
        return True

    def _maybe_commit(self, commit: Optional[bool], message: str) -> None:
        should_commit = self.auto_commit if commit is None else commit
        if should_commit:
            self._git_commit(message)

    def _skill_to_dict(self, skill: Skill) -> Dict[str, Any]:
        return skill.model_dump(mode="json") if hasattr(skill, "model_dump") else skill.dict()

    def _dict_to_skill(self, data: Dict[str, Any]) -> Skill:
        return Skill.model_validate(data) if hasattr(Skill, "model_validate") else Skill.parse_obj(data)

    def _state_value(self, state: SkillState | str | None) -> str:
        return state.value if hasattr(state, "value") else str(state)

    def _skill_type_value(self, skill_type: SkillType | str | None) -> str:
        return skill_type.value if hasattr(skill_type, "value") else str(skill_type)

    def _parse_semver(self, version: str) -> Tuple[int, int, int]:
        parts = version.split(".")
        if len(parts) != 3:
            raise ValueError(f"Invalid semantic version: {version!r}")
        return int(parts[0]), int(parts[1]), int(parts[2])

    def _semver_key(self, version: str) -> Tuple[int, int, int]:
        return self._parse_semver(version)

    def _next_version(self, version: str, bump: VersionBump = "patch") -> str:
        major, minor, patch = self._parse_semver(version)
        if bump == "major":
            return f"{major + 1}.0.0"
        if bump == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"

    def _latest_version_from_versions(self, versions: Dict[str, Any]) -> Optional[str]:
        visible = [version for version, meta in versions.items() if not meta.get("deleted", False)]
        return sorted(visible, key=self._semver_key)[-1] if visible else None

    def _merge_value(self, base_value: Any, other_value: Any, strategy: MergeStrategy) -> Any:
        if strategy == "prefer_base":
            return base_value if base_value not in (None, [], {}) else other_value
        if strategy == "append_lists" and isinstance(base_value, list) and isinstance(other_value, list):
            merged = list(base_value)
            for item in other_value:
                if item not in merged:
                    merged.append(item)
            return merged
        return other_value if other_value not in (None, [], {}) else base_value

    def _utc_now(self) -> str:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def init_repo(**kwargs: Any) -> None:
    GitSkillStore(kwargs.pop("base_dir", None)).init_repo(**kwargs)


def repo_status(base_dir: Optional[str | Path] = None) -> Dict[str, Any]:
    return GitSkillStore(base_dir).repo_status()


def read_events(limit: int = 100, base_dir: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    return GitSkillStore(base_dir).read_events(limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Git-backed SkillStorage utility")
    parser.add_argument("--base-dir", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--reset", action="store_true")
    p_init.add_argument("--remote-url", default=None)
    p_init.add_argument("--branch", default="main")

    sub.add_parser("status")
    sub.add_parser("rebuild-index")
    p_events = sub.add_parser("events")
    p_events.add_argument("--limit", type=int, default=100)

    p_list = sub.add_parser("list")
    p_list.add_argument("--all-versions", action="store_true")
    p_list.add_argument("--include-deleted", action="store_true")
    p_list.add_argument("--state")
    p_list.add_argument("--type")
    p_list.add_argument("--domain")
    p_list.add_argument("--name-like")
    p_list.add_argument("--limit", type=int, default=100)
    p_list.add_argument("--offset", type=int, default=0)

    p_get = sub.add_parser("get")
    p_get.add_argument("name")
    p_get.add_argument("--version")
    p_get.add_argument("--include-deleted", action="store_true")

    p_versions = sub.add_parser("versions")
    p_versions.add_argument("name")
    p_versions.add_argument("--include-deleted", action="store_true")

    p_diff = sub.add_parser("diff")
    p_diff.add_argument("name")
    p_diff.add_argument("v1")
    p_diff.add_argument("v2")

    p_history = sub.add_parser("history")
    p_history.add_argument("name")
    p_history.add_argument("--version")
    p_history.add_argument("--max-count", type=int, default=20)

    args = parser.parse_args()
    store = GitSkillStore(args.base_dir)

    if args.cmd == "init":
        store.init_repo(reset=args.reset, remote_url=args.remote_url, default_branch=args.branch)
        print(json.dumps(store.repo_status(), ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "status":
        print(json.dumps(store.repo_status(), ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "rebuild-index":
        print(json.dumps(store.rebuild_index(), ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "events":
        print(json.dumps(store.read_events(limit=args.limit), ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "list":
        rows = store.list_skills(
            state=args.state,
            skill_type=args.type,
            domain=args.domain,
            name_like=args.name_like,
            latest_only=not args.all_versions,
            include_deleted=args.include_deleted,
            limit=args.limit,
            offset=args.offset,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "get":
        skill = store.get_skill(args.name, args.version, include_deleted=args.include_deleted)
        data = store._skill_to_dict(skill) if skill else None
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "versions":
        print(json.dumps(store.get_skill_versions(args.name, include_deleted=args.include_deleted), indent=2))
    elif args.cmd == "diff":
        print(store.diff_versions(args.name, args.v1, args.v2))
    elif args.cmd == "history":
        print(store.git_file_history(args.name, args.version, max_count=args.max_count))


if __name__ == "__main__":
    main()
