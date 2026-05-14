from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.deps import get_app_state
from skillos.api.routes.lifecycle import router
from skillos.layers.skill_governance import GitVersionStore, skill_snapshot_path, write_skill_snapshot
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


class FakeWiki:
    def __init__(self, skill: Skill | None) -> None:
        self.skill = skill

    async def get(self, skill_id: str) -> Skill | None:
        if self.skill and self.skill.skill_id == skill_id:
            return self.skill
        return None


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _run_git(tmp_path, ["init"])
    baseline = tmp_path / ".gitkeep"
    baseline.write_text("", encoding="utf-8")
    GitVersionStore(tmp_path).commit_paths([".gitkeep"], "initial baseline")
    return tmp_path


@pytest.fixture
def skill() -> Skill:
    return Skill(
        skill_id="skill-1234567890",
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


@pytest.fixture
def client(git_repo: Path, skill: Skill, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SKILLOS_GOVERNANCE_REPO", str(git_repo))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(wiki=FakeWiki(skill))
    return TestClient(app)


def _commit_snapshot(repo: Path, skill: Skill, message: str) -> str:
    snapshot_path = write_skill_snapshot(repo, skill)
    return GitVersionStore(repo).commit_paths([snapshot_path], message)


def test_snapshot_endpoint_creates_commit(client: TestClient, git_repo: Path, skill: Skill) -> None:
    response = client.post(f"/api/v1/lifecycle/{skill.skill_id}/snapshot", json={})

    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_path"] == skill_snapshot_path(skill)
    assert data["message"] == "skill(search_wiki): snapshot v1.0.0"
    assert len(data["commit"]) == 40
    assert (git_repo / skill_snapshot_path(skill)).exists()


def test_snapshot_history_endpoint_returns_git_history(
    client: TestClient,
    git_repo: Path,
    skill: Skill,
) -> None:
    client.post(f"/api/v1/lifecycle/{skill.skill_id}/snapshot", json={"message": "snapshot one"})

    response = client.get(f"/api/v1/lifecycle/{skill.skill_id}/snapshot/history")

    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_path"] == skill_snapshot_path(skill)
    assert data["history"][0]["subject"] == "snapshot one"


def test_snapshot_diff_endpoint_returns_raw_and_structured_diff(
    client: TestClient,
    git_repo: Path,
    skill: Skill,
) -> None:
    first_commit = _commit_snapshot(git_repo, skill, "snapshot original")
    skill.description = "Search wiki entries with filters."
    second_commit = _commit_snapshot(git_repo, skill, "snapshot changed")

    response = client.get(
        f"/api/v1/lifecycle/{skill.skill_id}/snapshot/diff",
        params={"from_ref": first_commit, "to_ref": second_commit},
    )

    assert response.status_code == 200
    data = response.json()
    assert "Search wiki entries with filters." in data["raw_diff"]
    assert data["diffs"][0]["field"] == "description"
    assert data["has_breaking_changes"] is False


def test_snapshot_diff_marks_breaking_schema_change(
    client: TestClient,
    git_repo: Path,
    skill: Skill,
) -> None:
    first_commit = _commit_snapshot(git_repo, skill, "snapshot original")
    skill.interface.input_schema = {
        "type": "object",
        "properties": {"query": {"type": "integer"}},
        "required": ["query"],
    }
    second_commit = _commit_snapshot(git_repo, skill, "snapshot breaking")

    response = client.get(
        f"/api/v1/lifecycle/{skill.skill_id}/snapshot/diff",
        params={"from_ref": first_commit, "to_ref": second_commit},
    )

    assert response.status_code == 200
    assert response.json()["has_breaking_changes"] is True


def test_release_tag_endpoint_creates_tag(client: TestClient, git_repo: Path, skill: Skill) -> None:
    commit_hash = _commit_snapshot(git_repo, skill, "snapshot release")

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-tag",
        json={"ref": commit_hash},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["tag_name"] == "skill/search_wiki/skill-12/v1.0.0"
    assert GitVersionStore(git_repo).tag_exists(data["tag_name"])


def test_duplicate_release_tag_returns_400(client: TestClient, git_repo: Path, skill: Skill) -> None:
    commit_hash = _commit_snapshot(git_repo, skill, "snapshot release")
    client.post(f"/api/v1/lifecycle/{skill.skill_id}/release-tag", json={"ref": commit_hash})

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-tag",
        json={"ref": commit_hash},
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_rollback_endpoint_creates_restore_commit(
    client: TestClient,
    git_repo: Path,
    skill: Skill,
) -> None:
    old_commit = _commit_snapshot(git_repo, skill, "snapshot original")
    tag_response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-tag",
        json={"ref": old_commit},
    )
    old_tag = tag_response.json()["tag_name"]
    skill.description = "Changed description."
    _commit_snapshot(git_repo, skill, "snapshot changed")

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/rollback",
        json={"source_ref": old_tag},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["commit_message"] == f"skill(search_wiki): restore from {old_tag}"
    history = GitVersionStore(git_repo).commit_history(skill_snapshot_path(skill))
    assert history[0].subject == data["commit_message"]
    assert any(item.commit_hash == old_commit for item in history)


def test_missing_skill_returns_404(client: TestClient) -> None:
    response = client.post("/api/v1/lifecycle/missing/snapshot", json={})

    assert response.status_code == 404


def test_missing_ref_returns_400(client: TestClient, skill: Skill) -> None:
    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/rollback",
        json={"source_ref": "missing-ref"},
    )

    assert response.status_code == 400
    assert "Git command failed" in response.json()["detail"]
