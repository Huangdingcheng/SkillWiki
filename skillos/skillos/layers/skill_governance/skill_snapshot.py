"""Skill snapshot serialization and domain-level diff utilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ...models.skill_model import Skill
from .git_version_store import GitVersionStore


SNAPSHOT_FIELDS = (
    "skill_id",
    "name",
    "version",
    "description",
    "skill_type",
    "domain",
    "granularity_level",
    "state",
    "tags",
    "interface",
    "implementation",
    "test_cases",
    "provenance",
)

DIFF_FIELDS = (
    "name",
    "version",
    "description",
    "skill_type",
    "domain",
    "state",
    "tags",
    "interface.input_schema",
    "interface.output_schema",
    "implementation.prompt_template",
    "implementation.code",
    "implementation.tool_calls",
    "implementation.sub_skill_ids",
)

SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class SkillSnapshotDiff:
    """A single field-level snapshot diff."""

    field: str
    change_type: str
    old_value: Any
    new_value: Any
    is_breaking: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "change_type": self.change_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "is_breaking": self.is_breaking,
        }


def skill_snapshot_path(skill: Skill) -> str:
    """Return the repo-relative snapshot path for a Skill version."""
    skill_id = _safe_snapshot_component(skill.skill_id, "skill_id")
    version = _safe_snapshot_component(skill.version, "version")
    return f"skills/{skill_id}/{version}.json"


def skill_to_snapshot(skill: Skill) -> Dict[str, Any]:
    """Convert a Skill to a stable governance snapshot dictionary."""
    raw = skill.model_dump(mode="json")
    return {field: raw.get(field) for field in SNAPSHOT_FIELDS}


def snapshot_to_json(snapshot: Mapping[str, Any]) -> str:
    """Serialize a snapshot to stable JSON."""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def skill_to_snapshot_json(skill: Skill) -> str:
    """Serialize a Skill to stable governance snapshot JSON."""
    return snapshot_to_json(skill_to_snapshot(skill))


def write_skill_snapshot(repo_path: str | Path, skill: Skill) -> str:
    """Write a Skill snapshot under the repo path and return its relative path."""
    relative_path = skill_snapshot_path(skill)
    repo_root = Path(repo_path).resolve()
    target = (repo_root / relative_path).resolve()
    if not target.is_relative_to(repo_root):
        raise ValueError("Skill snapshot path must stay inside the repository.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skill_to_snapshot_json(skill), encoding="utf-8")
    return relative_path


def commit_skill_snapshot(
    repo_path: str | Path,
    skill: Skill,
    store: Optional[GitVersionStore] = None,
) -> str:
    """Write and commit a Skill snapshot, returning the new commit hash."""
    relative_path = write_skill_snapshot(repo_path, skill)
    version_store = store or GitVersionStore(repo_path)
    message = f"skill({skill.name}): snapshot v{skill.version}"
    return version_store.commit_paths([relative_path], message)


def diff_skill_snapshots(
    old_snapshot: Mapping[str, Any] | str,
    new_snapshot: Mapping[str, Any] | str,
) -> List[SkillSnapshotDiff]:
    """Return domain-level diffs between two Skill snapshots."""
    old_data = _coerce_snapshot(old_snapshot)
    new_data = _coerce_snapshot(new_snapshot)
    diffs: List[SkillSnapshotDiff] = []

    for field in DIFF_FIELDS:
        old_value = _get_path(old_data, field)
        new_value = _get_path(new_data, field)
        if old_value == new_value:
            continue
        diffs.extend(_diff_field(field, old_value, new_value))

    return diffs


def has_breaking_changes(diffs: List[SkillSnapshotDiff]) -> bool:
    return any(diff.is_breaking for diff in diffs)


def _safe_snapshot_component(value: str, field_name: str) -> str:
    if not value or not SAFE_PATH_COMPONENT.fullmatch(value) or ".." in value:
        raise ValueError(f"Invalid Skill snapshot {field_name}: {value!r}")
    return value


def _coerce_snapshot(snapshot: Mapping[str, Any] | str) -> Dict[str, Any]:
    if isinstance(snapshot, str):
        return json.loads(snapshot)
    return dict(snapshot)


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _diff_field(field: str, old_value: Any, new_value: Any) -> List[SkillSnapshotDiff]:
    if field in ("interface.input_schema", "interface.output_schema"):
        return _diff_schema(field, old_value or {}, new_value or {})

    change_type = _change_type(old_value, new_value)
    is_breaking = field in ("implementation.prompt_template", "implementation.code") and (
        bool(old_value) and not new_value
    )
    return [SkillSnapshotDiff(field, change_type, old_value, new_value, is_breaking)]


def _diff_schema(
    field: str,
    old_schema: Mapping[str, Any],
    new_schema: Mapping[str, Any],
) -> List[SkillSnapshotDiff]:
    diffs: List[SkillSnapshotDiff] = []
    old_properties = old_schema.get("properties", {}) if isinstance(old_schema, Mapping) else {}
    new_properties = new_schema.get("properties", {}) if isinstance(new_schema, Mapping) else {}
    old_required = set(old_schema.get("required", []) if isinstance(old_schema, Mapping) else [])
    new_required = set(new_schema.get("required", []) if isinstance(new_schema, Mapping) else [])

    for name in sorted(set(old_properties) | set(new_properties)):
        old_prop = old_properties.get(name)
        new_prop = new_properties.get(name)
        if old_prop == new_prop:
            continue
        path = f"{field}.properties.{name}"
        if name not in new_properties:
            diffs.append(
                SkillSnapshotDiff(
                    path,
                    "removed",
                    old_prop,
                    None,
                    field in ("interface.input_schema", "interface.output_schema"),
                )
            )
        elif name not in old_properties:
            diffs.append(SkillSnapshotDiff(path, "added", None, new_prop, False))
        else:
            old_type = old_prop.get("type") if isinstance(old_prop, Mapping) else None
            new_type = new_prop.get("type") if isinstance(new_prop, Mapping) else None
            diffs.append(
                SkillSnapshotDiff(path, "modified", old_prop, new_prop, old_type != new_type)
            )

    for name in sorted(old_required - new_required):
        path = f"{field}.required.{name}"
        diffs.append(SkillSnapshotDiff(path, "removed", True, False, False))

    for name in sorted(new_required - old_required):
        path = f"{field}.required.{name}"
        diffs.append(SkillSnapshotDiff(path, "added", False, True, field == "interface.input_schema"))

    return diffs


def _change_type(old_value: Any, new_value: Any) -> str:
    if old_value is None and new_value is not None:
        return "added"
    if old_value is not None and new_value is None:
        return "removed"
    return "modified"
