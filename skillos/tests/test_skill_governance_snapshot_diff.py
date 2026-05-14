from __future__ import annotations

import subprocess
from pathlib import Path

from skillos.layers.skill_governance import (
    GitVersionStore,
    commit_skill_snapshot,
    diff_skill_snapshots,
    has_breaking_changes,
    skill_snapshot_path,
    skill_to_snapshot,
    skill_to_snapshot_json,
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


def test_description_change_is_not_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(description="Search wiki entries with filters.")

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert [diff.field for diff in diffs] == ["description"]
    assert diffs[0].change_category == "metadata_change"
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


def test_added_output_property_is_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        output_schema={
            "type": "object",
            "properties": {
                "results": {"type": "array"},
                "score": {"type": "number"},
            },
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.output_schema.properties.score" for diff in diffs)
    assert has_breaking_changes(diffs)


def test_output_property_constraint_change_is_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        output_schema={
            "type": "object",
            "properties": {"results": {"type": "array", "minItems": 1}},
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.output_schema.properties.results" for diff in diffs)
    assert has_breaking_changes(diffs)


def test_output_root_schema_change_is_breaking() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with(
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"results": {"type": "array"}},
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "interface.output_schema.additionalProperties" for diff in diffs)
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


def test_removed_sub_skill_id_is_breaking_dependency_change() -> None:
    old_snapshot = _snapshot_with(
        implementation={
            "language": "python",
            "sub_skill_ids": ["search_index", "rank_results"],
        }
    )
    new_snapshot = _snapshot_with(
        implementation={
            "language": "python",
            "sub_skill_ids": ["search_index"],
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "implementation.sub_skill_ids.rank_results" for diff in diffs)
    assert diffs[0].change_category == "dependency_change"
    assert has_breaking_changes(diffs)


def test_reordered_sub_skill_ids_are_breaking_dependency_change() -> None:
    old_snapshot = _snapshot_with(
        implementation={
            "language": "python",
            "sub_skill_ids": ["search_index", "rank_results"],
        }
    )
    new_snapshot = _snapshot_with(
        implementation={
            "language": "python",
            "sub_skill_ids": ["rank_results", "search_index"],
        }
    )

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert [diff.field for diff in diffs] == ["implementation.sub_skill_ids"]
    assert diffs[0].change_category == "dependency_change"
    assert has_breaking_changes(diffs)


def test_postcondition_and_provenance_are_semantic_categories() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with()
    old_snapshot["interface"]["postconditions"] = ["results are ranked"]
    new_snapshot["interface"]["postconditions"] = ["results are ranked by relevance"]
    old_snapshot["provenance"] = {"source_type": "trajectory", "source_ids": ["trace-1"]}
    new_snapshot["provenance"] = {"source_type": "trajectory", "source_ids": ["trace-2"]}

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)
    categories = {diff.change_category for diff in diffs}

    assert "postcondition_change" in categories
    assert "provenance_change" in categories


def test_durable_governance_fields_are_reviewable() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with()
    old_snapshot["test_cases"] = [{"test_id": "case-1", "name": "baseline"}]
    new_snapshot["test_cases"] = [{"test_id": "case-2", "name": "regression"}]
    old_snapshot["interface"]["side_effects"] = ["reads cache"]
    new_snapshot["interface"]["side_effects"] = ["writes cache"]
    old_snapshot["implementation"]["execution_order"] = ["search_index", "rank_results"]
    new_snapshot["implementation"]["execution_order"] = ["rank_results", "search_index"]

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)
    fields = {diff.field for diff in diffs}
    categories = {diff.change_category for diff in diffs}

    assert "test_cases" in fields
    assert "interface.side_effects" in fields
    assert "implementation.execution_order" in fields
    assert "schema_change" in categories
    assert "dependency_change" in categories


def test_dependency_reference_changes_are_reviewable() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with()
    old_snapshot["dependency_ids"] = ["click_element"]
    new_snapshot["dependency_ids"] = ["click_element", "type_text"]
    old_snapshot["component_ids"] = ["open_form"]
    new_snapshot["component_ids"] = []

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert any(diff.field == "dependency_ids.type_text" for diff in diffs)
    assert any(diff.field == "component_ids.open_form" for diff in diffs)
    assert {diff.change_category for diff in diffs} == {"dependency_change"}
    assert not has_breaking_changes(diffs)


def test_evaluation_change_is_reviewable_metadata() -> None:
    old_snapshot = _snapshot_with()
    new_snapshot = _snapshot_with()
    old_snapshot["evaluation"] = {"validation_summary": "passes baseline verifier"}
    new_snapshot["evaluation"] = {"validation_summary": "passes stricter verifier"}

    diffs = diff_skill_snapshots(old_snapshot, new_snapshot)

    assert [diff.field for diff in diffs] == ["evaluation"]
    assert diffs[0].change_category == "metadata_change"
    assert not has_breaking_changes(diffs)


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
