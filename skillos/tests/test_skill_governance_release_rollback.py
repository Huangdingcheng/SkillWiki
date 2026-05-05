from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skillos.layers.skill_governance import (
    GitVersionStore,
    GitVersionStoreError,
    read_skill_snapshot_at_ref,
    release_skill_snapshot,
    restore_skill_snapshot,
    skill_release_tag_name,
    skill_snapshot_path,
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


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _run_git(tmp_path, ["init"])
    baseline = tmp_path / ".gitkeep"
    baseline.write_text("", encoding="utf-8")
    GitVersionStore(tmp_path).commit_paths([".gitkeep"], "initial baseline")
    return tmp_path


def _skill(version: str = "1.0.0", description: str = "Search wiki entries.") -> Skill:
    return Skill(
        skill_id="skill-1234567890",
        name="search_wiki",
        version=version,
        description=description,
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


def _commit_snapshot(repo: Path, skill: Skill, message: str) -> str:
    snapshot_path = write_skill_snapshot(repo, skill)
    return GitVersionStore(repo).commit_paths([snapshot_path], message)


def test_release_tag_name_is_deterministic_and_sanitized() -> None:
    skill = _skill()
    skill.skill_id = "Skill 1234/5678"

    assert skill_release_tag_name(skill) == "skill/search_wiki/Skill-12/v1.0.0"


def test_release_skill_snapshot_creates_tag(git_repo: Path) -> None:
    skill = _skill()
    commit_hash = _commit_snapshot(git_repo, skill, "skill(search_wiki): snapshot v1.0.0")
    store = GitVersionStore(git_repo)

    record = release_skill_snapshot(git_repo, skill, commit_hash, store)

    assert record.tag_name == "skill/search_wiki/skill-12/v1.0.0"
    assert record.commit == commit_hash
    assert record.snapshot_path == "skills/skill-1234567890/1.0.0.json"
    assert store.tag_exists(record.tag_name)


def test_duplicate_release_tag_fails_clearly(git_repo: Path) -> None:
    skill = _skill()
    commit_hash = _commit_snapshot(git_repo, skill, "skill(search_wiki): snapshot v1.0.0")

    release_skill_snapshot(git_repo, skill, commit_hash)

    with pytest.raises(ValueError, match="already exists"):
        release_skill_snapshot(git_repo, skill, commit_hash)


def test_read_file_at_ref_reads_snapshot_from_tag(git_repo: Path) -> None:
    skill = _skill(description="Released description.")
    commit_hash = _commit_snapshot(git_repo, skill, "skill(search_wiki): snapshot v1.0.0")
    tag = release_skill_snapshot(git_repo, skill, commit_hash).tag_name

    snapshot = read_skill_snapshot_at_ref(git_repo, tag, skill_snapshot_path(skill))

    assert snapshot["name"] == "search_wiki"
    assert snapshot["description"] == "Released description."


def test_restore_skill_snapshot_creates_restore_commit(git_repo: Path) -> None:
    old_skill = _skill(description="Original description.")
    old_commit = _commit_snapshot(git_repo, old_skill, "skill(search_wiki): snapshot v1.0.0")
    old_tag = release_skill_snapshot(git_repo, old_skill, old_commit).tag_name

    current_skill = _skill(description="Changed description.")
    _commit_snapshot(git_repo, current_skill, "skill(search_wiki): snapshot changed")

    record = restore_skill_snapshot(git_repo, current_skill, old_tag)
    store = GitVersionStore(git_repo)
    restored_snapshot = read_skill_snapshot_at_ref(git_repo, "HEAD", skill_snapshot_path(current_skill))
    history = store.commit_history(skill_snapshot_path(current_skill))

    assert record.source_ref == old_tag
    assert record.commit_message == f"skill(search_wiki): restore from {old_tag}"
    assert restored_snapshot["description"] == "Original description."
    assert history[0].subject == record.commit_message
    assert any(entry.commit_hash == old_commit for entry in history)


def test_restore_missing_ref_fails_clearly(git_repo: Path) -> None:
    skill = _skill()

    with pytest.raises(GitVersionStoreError, match="Git command failed"):
        restore_skill_snapshot(git_repo, skill, "missing-ref")


def test_restore_missing_snapshot_fails_clearly(git_repo: Path) -> None:
    other_file = git_repo / "notes.txt"
    other_file.write_text("no skill snapshot here\n", encoding="utf-8")
    store = GitVersionStore(git_repo)
    commit_hash = store.commit_paths(["notes.txt"], "add notes")
    store.create_tag("notes-tag", commit_hash)

    with pytest.raises(GitVersionStoreError, match="Git command failed"):
        restore_skill_snapshot(git_repo, _skill(), "notes-tag")
