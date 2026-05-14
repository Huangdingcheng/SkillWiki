from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skillos.layers.skill_governance import (
    GitVersionStore,
    commit_skill_snapshot,
    diff_skill_snapshots,
    has_breaking_changes,
    skill_snapshot_path,
    skill_to_snapshot,
    skill_to_snapshot_json,
    write_skill_snapshot,
)
from skillos.models import Skill, SkillImplementation, SkillInterface, SkillState, SkillType


def _run_git(repo: Path, args: list[str]) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _make_skill() -> Skill:
    return Skill(
        skill_id="skill-123",
        name="search_wiki",
        version="1.0.0",
        description="Search wiki entries.",
        skill_type=SkillType.FUNCTIONAL,
        domain="wiki",
        state=SkillState.RELEASED,
        tags=["wiki", "search"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            output_schema={
                "type": "object",
                "properties": {"results": {"type": "array"}},
            },
        ),
        implementation=SkillImplementation(prompt_template="Search for {query}"),
    )


def _snapshot_with(
    *,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    description: str = "Search wiki entries.",
    tags: list[str] | None = None,
    implementation: dict | None = None,
) -> dict:
    snapshot = skill_to_snapshot(_make_skill())
    snapshot["description"] = description
    snapshot["tags"] = tags if tags is not None else ["wiki", "search"]
    if input_schema is not None:
        snapshot["interface"]["input_schema"] = input_schema
    if output_schema is not None:
        snapshot["interface"]["output_schema"] = output_schema
    if implementation is not None:
        snapshot["implementation"] = implementation
    return snapshot


def test_snapshot_json_is_stable() -> None:
    skill = _make_skill()

    assert skill_to_snapshot_json(skill) == skill_to_snapshot_json(skill)


def test_snapshot_excludes_runtime_noise_fields() -> None:
    skill = _make_skill()
    skill.record_execution(success=True, latency_ms=120)
    snapshot = skill_to_snapshot(skill)
    json_text = skill_to_snapshot_json(skill)

    assert "metrics" not in snapshot
    assert "created_at" not in snapshot
    assert "updated_at" not in snapshot
    assert "released_at" not in snapshot
    assert "deprecated_at" not in snapshot
    assert "metrics" not in json_text


def test_snapshot_path_uses_skill_id_and_version() -> None:
    skill = _make_skill()

    assert skill_snapshot_path(skill) == "skills/skill-123/1.0.0.json"


def test_snapshot_path_rejects_unsafe_skill_id() -> None:
    for unsafe_id in ("../outside", "bad/id", "bad\\id", "bad id"):
        skill = _make_skill()
        skill.skill_id = unsafe_id

        with pytest.raises(ValueError, match="Invalid Skill snapshot skill_id"):
            skill_snapshot_path(skill)


def test_write_snapshot_rejects_unsafe_path_before_writing(tmp_path: Path) -> None:
    skill = _make_skill()
    skill.skill_id = "../outside"

    with pytest.raises(ValueError, match="Invalid Skill snapshot skill_id"):
        write_skill_snapshot(tmp_path, skill)

    assert not (tmp_path.parent / "outside").exists()


def test_description_change_is_not_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(description="Search wiki entries with filters.")

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert [diff.field for diff in diffs] == ["description"]
    assert not has_breaking_changes(diffs)


def test_added_optional_input_field_is_not_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.input_schema.properties.limit" for diff in diffs)
    assert not has_breaking_changes(diffs)


def test_removed_input_property_is_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        input_schema={"type": "object", "properties": {}, "required": []}
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.input_schema.properties.query" for diff in diffs)
    assert has_breaking_changes(diffs)


def test_added_required_input_field_is_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "workspace": {"type": "string"},
            },
            "required": ["query", "workspace"],
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.input_schema.required.workspace" for diff in diffs)
    assert has_breaking_changes(diffs)


def test_removed_output_property_is_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        output_schema={"type": "object", "properties": {}}
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.output_schema.properties.results" for diff in diffs)
    assert has_breaking_changes(diffs)


def test_schema_type_change_is_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "array"}},
            "required": ["query"],
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.input_schema.properties.query" for diff in diffs)
    assert has_breaking_changes(diffs)


def test_prompt_or_code_removed_is_breaking() -> None:
    old_snapshot = _snapshot_with(
        implementation={"language": "python", "prompt_template": "Search {query}"}
    )
    new_snapshot = _snapshot_with(
        implementation={"language": "python", "prompt_template": ""}
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "implementation.prompt_template" for diff in diffs)
    assert has_breaking_changes(diffs)


def test_diff_accepts_json_strings() -> None:
    old_json = skill_to_snapshot_json(_make_skill())
    new_snapshot = _snapshot_with(description="Updated description.")

    diffs = diff_skill_snapshots(old_json, new_snapshot)

    assert [diff.to_dict()["field"] for diff in diffs] == ["description"]


def test_commit_snapshot_to_temp_git_repo(tmp_path: Path) -> None:
    _run_git(tmp_path, ["init"])
    skill = _make_skill()
    store = GitVersionStore(tmp_path)
    baseline = tmp_path / ".gitkeep"
    baseline.write_text("", encoding="utf-8")
    baseline_commit = store.commit_paths([".gitkeep"], "initial baseline")

    commit_hash = commit_skill_snapshot(tmp_path, skill, store)
    history = store.commit_history(skill_snapshot_path(skill))
    diff = store.diff_between(baseline_commit, commit_hash, skill_snapshot_path(skill))

    assert history[0].subject == "skill(search_wiki): snapshot v1.0.0"
    assert "search_wiki" in diff
