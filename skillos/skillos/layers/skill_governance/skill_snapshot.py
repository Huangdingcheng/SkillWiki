"""Skill snapshot serialization and domain-level diff utilities."""

from __future__ import annotations

import json
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
    "evaluation",
    "provenance",
    "dependency_ids",
    "component_ids",
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
    "interface.preconditions",
    "interface.postconditions",
    "interface.side_effects",
    "implementation.prompt_template",
    "implementation.code",
    "implementation.tool_calls",
    "implementation.sub_skill_ids",
    "implementation.execution_order",
    "test_cases",
    "evaluation",
    "provenance",
    "dependency_ids",
    "component_ids",
)


@dataclass(frozen=True)
class SkillSnapshotDiff:
    """A single field-level snapshot diff."""

    field: str
    change_type: str
    old_value: Any
    new_value: Any
    is_breaking: bool = False
    change_category: str = "metadata_change"
    breaking_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "change_type": self.change_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "is_breaking": self.is_breaking,
            "change_category": self.change_category,
            "breaking_reason": self.breaking_reason,
        }


def skill_snapshot_path(skill: Skill) -> str:
    """Return the repo-relative snapshot path for a Skill version."""
    return f"skills/{skill.skill_id}/{skill.version}.json"


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
    target = Path(repo_path) / relative_path
    if target.exists():
        GitVersionStore(repo_path).ensure_paths_clean([relative_path])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skill_to_snapshot_json(skill), encoding="utf-8")
    return relative_path


def commit_skill_snapshot(
    repo_path: str | Path,
    skill: Skill,
    store: Optional[GitVersionStore] = None,
) -> str:
    """Write and commit a Skill snapshot, returning the new commit hash."""
    version_store = store or GitVersionStore(repo_path)
    message = f"skill({skill.name}): snapshot v{skill.version}"
    with version_store.lock():
        relative_path = write_skill_snapshot(repo_path, skill)
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


def review_recommendation_for_diffs(diffs: List[SkillSnapshotDiff]) -> str:
    """Return the governance review recommendation for a structured diff."""
    if not diffs:
        return "no_changes"
    if has_breaking_changes(diffs):
        return "breaking_review_required"
    return "review_required"


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
    if field == "implementation.sub_skill_ids":
        return _diff_ordered_list(
            field,
            old_value or [],
            new_value or [],
            change_category="dependency_change",
            breaking_on_removed=True,
            breaking_on_reorder=True,
        )
    if field == "implementation.execution_order":
        return _diff_ordered_list(
            field,
            old_value or [],
            new_value or [],
            change_category="dependency_change",
            breaking_on_removed=False,
            breaking_on_reorder=True,
        )
    if field in ("dependency_ids", "component_ids"):
        return _diff_ordered_list(
            field,
            old_value or [],
            new_value or [],
            change_category="dependency_change",
            breaking_on_removed=False,
        )

    change_type = _change_type(old_value, new_value)
    change_category = _change_category(field)
    breaking_reason = None
    is_breaking = False
    if field in ("implementation.prompt_template", "implementation.code") and (
        bool(old_value) and not new_value
    ):
        is_breaking = True
        breaking_reason = "implementation_removed"
    return [
        SkillSnapshotDiff(
            field,
            change_type,
            old_value,
            new_value,
            is_breaking,
            change_category,
            breaking_reason,
        )
    ]


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
    category = _change_category(field)

    old_root = {
        key: value
        for key, value in old_schema.items()
        if key not in ("properties", "required")
    } if isinstance(old_schema, Mapping) else {}
    new_root = {
        key: value
        for key, value in new_schema.items()
        if key not in ("properties", "required")
    } if isinstance(new_schema, Mapping) else {}
    for key in sorted(set(old_root) | set(new_root)):
        old_value = old_root.get(key)
        new_value = new_root.get(key)
        if old_value == new_value:
            continue
        is_breaking = field == "interface.output_schema" or key == "type"
        diffs.append(
            SkillSnapshotDiff(
                f"{field}.{key}",
                _change_type(old_value, new_value),
                old_value,
                new_value,
                is_breaking,
                category,
                "schema_root_changed" if is_breaking else None,
            )
        )

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
                    category,
                    "schema_property_removed",
                )
            )
        elif name not in old_properties:
            is_breaking = field == "interface.output_schema"
            diffs.append(
                SkillSnapshotDiff(
                    path,
                    "added",
                    None,
                    new_prop,
                    is_breaking,
                    category,
                    "output_schema_property_added" if is_breaking else None,
                )
            )
        else:
            old_type = old_prop.get("type") if isinstance(old_prop, Mapping) else None
            new_type = new_prop.get("type") if isinstance(new_prop, Mapping) else None
            is_breaking = field == "interface.output_schema" or old_type != new_type
            diffs.append(
                SkillSnapshotDiff(
                    path,
                    "modified",
                    old_prop,
                    new_prop,
                    is_breaking,
                    category,
                    "output_schema_property_changed" if field == "interface.output_schema"
                    else "schema_type_changed" if is_breaking else None,
                )
            )

    for name in sorted(old_required - new_required):
        path = f"{field}.required.{name}"
        is_breaking = field == "interface.output_schema"
        diffs.append(
            SkillSnapshotDiff(
                path,
                "removed",
                True,
                False,
                is_breaking,
                _change_category(field),
                "output_required_removed" if is_breaking else None,
            )
        )

    for name in sorted(new_required - old_required):
        path = f"{field}.required.{name}"
        is_breaking = field in ("interface.input_schema", "interface.output_schema")
        diffs.append(
            SkillSnapshotDiff(
                path,
                "added",
                False,
                True,
                is_breaking,
                _change_category(field),
                "required_field_added" if is_breaking else None,
            )
        )

    return diffs


def _diff_ordered_list(
    field: str,
    old_value: Any,
    new_value: Any,
    *,
    change_category: str,
    breaking_on_removed: bool,
    breaking_on_reorder: bool = False,
) -> List[SkillSnapshotDiff]:
    old_items = [str(item) for item in old_value] if isinstance(old_value, list) else []
    new_items = [str(item) for item in new_value] if isinstance(new_value, list) else []
    diffs: List[SkillSnapshotDiff] = []

    if old_items != new_items and sorted(old_items) == sorted(new_items):
        diffs.append(
            SkillSnapshotDiff(
                field,
                "modified",
                old_items,
                new_items,
                breaking_on_reorder,
                change_category,
                "ordered_composition_changed" if breaking_on_reorder else None,
            )
        )
        return diffs

    for item in sorted(set(old_items) - set(new_items)):
        diffs.append(
            SkillSnapshotDiff(
                f"{field}.{item}",
                "removed",
                item,
                None,
                breaking_on_removed,
                change_category,
                "sub_skill_dependency_removed" if breaking_on_removed else None,
            )
        )
    for item in sorted(set(new_items) - set(old_items)):
        diffs.append(
            SkillSnapshotDiff(
                f"{field}.{item}",
                "added",
                None,
                item,
                False,
                change_category,
            )
        )

    return diffs


def _change_type(old_value: Any, new_value: Any) -> str:
    if old_value is None and new_value is not None:
        return "added"
    if old_value is not None and new_value is None:
        return "removed"
    return "modified"


def _change_category(field: str) -> str:
    if field.startswith("interface.input_schema") or field.startswith("interface.output_schema"):
        return "schema_change"
    if field.startswith("interface.preconditions") or field.startswith("interface.side_effects"):
        return "schema_change"
    if field.startswith("interface.postconditions"):
        return "postcondition_change"
    if field.startswith("implementation.sub_skill_ids"):
        return "dependency_change"
    if field.startswith("implementation.execution_order"):
        return "dependency_change"
    if field.startswith("implementation."):
        return "implementation_change"
    if field.startswith("evaluation") or field.startswith("test_cases"):
        return "metadata_change"
    if field.startswith("provenance"):
        return "provenance_change"
    if field in ("dependency_ids", "component_ids"):
        return "dependency_change"
    return "metadata_change"
