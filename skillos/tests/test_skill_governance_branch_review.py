from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skillos.layers.skill_governance import (
    GitVersionStore,
    GitVersionStoreError,
    propose_skill_change,
    skill_change_branch_name,
    skill_snapshot_path,
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
    store = GitVersionStore(tmp_path)
    store.commit_paths([".gitkeep"], "initial baseline")
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


def test_branch_name_is_deterministic_and_sanitized() -> None:
    skill = _skill(version="1.0.1")
    skill.skill_id = "Skill 1234/5678"
    skill.name = "search_wiki"

    assert skill_change_branch_name(skill) == "skill/search_wiki/Skill-12-v1.0.1"


def test_workflow_creates_review_branch_and_snapshot_commit(git_repo: Path) -> None:
    old_skill = _skill()
    new_skill = _skill(version="1.0.1", description="Search wiki entries with filters.")
    store = GitVersionStore(git_repo)
    base_branch = store.current_branch()

    bundle = propose_skill_change(git_repo, old_skill, new_skill, store)

    assert bundle.branch_name == "skill/search_wiki/skill-12-v1.0.1"
    assert bundle.snapshot_path == "skills/skill-1234567890/1.0.1.json"
    assert bundle.commit_message == "skill(search_wiki): propose v1.0.1"
    assert bundle.suggested_review_status == "review_required"
    assert not bundle.has_breaking_changes
    assert store.current_branch() == base_branch

    review_snapshot = store.read_file_at_ref(bundle.head_commit, skill_snapshot_path(new_skill))
    assert "Search wiki entries with filters." in review_snapshot


def test_breaking_change_requires_breaking_review(git_repo: Path) -> None:
    old_skill = _skill()
    new_skill = _skill(version="2.0.0")
    new_skill.interface.input_schema = {
        "type": "object",
        "properties": {"query": {"type": "array"}},
        "required": ["query"],
    }

    bundle = propose_skill_change(git_repo, old_skill, new_skill)

    assert bundle.suggested_review_status == "breaking_review_required"
    assert bundle.has_breaking_changes
    assert any(diff.is_breaking for diff in bundle.diffs)


def test_no_changes_does_not_create_branch_or_commit(git_repo: Path) -> None:
    old_skill = _skill()
    new_skill = _skill()
    store = GitVersionStore(git_repo)
    base_branch = store.current_branch()
    base_commit = store.head_commit()

    bundle = propose_skill_change(git_repo, old_skill, new_skill, store)

    assert bundle.suggested_review_status == "no_changes"
    assert bundle.head_commit == base_commit
    assert bundle.commit_message == ""
    assert store.current_branch() == base_branch
    assert not store.branch_exists(bundle.branch_name)


def test_existing_branch_fails_clearly(git_repo: Path) -> None:
    old_skill = _skill()
    new_skill = _skill(version="1.0.1", description="Updated description.")
    store = GitVersionStore(git_repo)
    store.create_branch(skill_change_branch_name(new_skill))

    with pytest.raises(GitVersionStoreError, match="already exists"):
        propose_skill_change(git_repo, old_skill, new_skill, store)


def test_workflow_failure_restores_base_branch(git_repo: Path) -> None:
    old_skill = _skill()
    new_skill = _skill(version="1.0.1", description="Updated description.")
    store = GitVersionStore(git_repo)
    base_branch = store.current_branch()

    class FailingCommitStore(GitVersionStore):
        def commit_paths(self, paths, message, author_name="SkillOS", author_email="skillos@example.local"):
            raise GitVersionStoreError("simulated commit failure")

    failing_store = FailingCommitStore(git_repo)

    with pytest.raises(GitVersionStoreError, match="simulated commit failure"):
        propose_skill_change(git_repo, old_skill, new_skill, failing_store)

    assert store.current_branch() == base_branch
