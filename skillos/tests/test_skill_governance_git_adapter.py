from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skillos.layers.skill_governance import GitVersionStore, GitVersionStoreError


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
    return tmp_path


def test_detects_git_repo_and_head(git_repo: Path) -> None:
    skill_path = git_repo / "skills" / "test_skill.json"
    skill_path.parent.mkdir()
    skill_path.write_text('{"name": "test_skill", "version": "1.0.0"}\n', encoding="utf-8")

    store = GitVersionStore(git_repo)
    commit_hash = store.commit_paths(["skills/test_skill.json"], "add test skill")

    assert store.is_git_repo()
    assert store.current_branch()
    assert store.head_commit() == commit_hash
    assert len(commit_hash) == 40


def test_commit_history_for_skill_snapshot(git_repo: Path) -> None:
    skill_path = git_repo / "skills" / "test_skill.json"
    skill_path.parent.mkdir()
    skill_path.write_text('{"version": "1.0.0"}\n', encoding="utf-8")

    store = GitVersionStore(git_repo)
    store.commit_paths(["skills/test_skill.json"], "add test skill")
    skill_path.write_text('{"version": "1.0.1"}\n', encoding="utf-8")
    store.commit_paths(["skills/test_skill.json"], "update test skill")

    history = store.commit_history("skills/test_skill.json")

    assert [entry.subject for entry in history] == ["update test skill", "add test skill"]
    assert history[0].changed_paths == ("skills/test_skill.json",)


def test_diff_between_commits(git_repo: Path) -> None:
    skill_path = git_repo / "skills" / "test_skill.json"
    skill_path.parent.mkdir()
    skill_path.write_text('{"version": "1.0.0"}\n', encoding="utf-8")

    store = GitVersionStore(git_repo)
    first_commit = store.commit_paths(["skills/test_skill.json"], "add test skill")
    skill_path.write_text('{"version": "1.0.1"}\n', encoding="utf-8")
    second_commit = store.commit_paths(["skills/test_skill.json"], "update test skill")

    diff = store.diff_between(first_commit, second_commit, "skills/test_skill.json")

    assert '"1.0.0"' in diff
    assert '"1.0.1"' in diff


def test_non_git_repo_reports_clear_error(tmp_path: Path) -> None:
    store = GitVersionStore(tmp_path)

    assert not store.is_git_repo()
    with pytest.raises(GitVersionStoreError, match="not a Git repository"):
        store.current_branch()


def test_rejects_paths_outside_repo(git_repo: Path) -> None:
    store = GitVersionStore(git_repo)

    with pytest.raises(ValueError, match="repo-relative"):
        store.commit_paths(["../outside.json"], "invalid path")
