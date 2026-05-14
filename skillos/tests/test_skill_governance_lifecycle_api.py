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
from skillos.models import EdgeType, Skill, SkillEdge, SkillImplementation, SkillInterface, SkillState, SkillType


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
    def __init__(self, skill: Skill | None, extra_skills: list[Skill] | None = None) -> None:
        self.skill = skill
        self.skills = {
            item.skill_id: item
            for item in ([skill] if skill else []) + list(extra_skills or [])
        }

    async def get(self, skill_id: str) -> Skill | None:
        return self.skills.get(skill_id)


class FakeGraph:
    def __init__(self, edges: list[SkillEdge] | None = None) -> None:
        self.edges = list(edges or [])

    async def get_edges(
        self,
        skill_id: str,
        direction: str = "both",
        edge_type: EdgeType | None = None,
    ) -> list[SkillEdge]:
        result = [
            edge
            for edge in self.edges
            if (
                direction == "in" and edge.target_id == skill_id
                or direction == "out" and edge.source_id == skill_id
                or direction == "both" and skill_id in (edge.source_id, edge.target_id)
            )
        ]
        if edge_type:
            result = [edge for edge in result if edge.edge_type == edge_type]
        return result


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
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(wiki=FakeWiki(skill), graph=FakeGraph())
    return TestClient(app)


@pytest.fixture
def impact_client(git_repo: Path, skill: Skill, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SKILLOS_GOVERNANCE_REPO", str(git_repo))
    dependent = Skill(
        skill_id="fill_form",
        name="fill_form",
        version="1.0.0",
        description="Fill a web form.",
        skill_type=SkillType.FUNCTIONAL,
        domain="web",
        state=SkillState.RELEASED,
        tags=["web"],
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"fields": {"type": "object"}}},
            output_schema={"type": "object", "properties": {"success": {"type": "boolean"}}},
        ),
        implementation=SkillImplementation(
            prompt_template="Fill form",
            sub_skill_ids=[skill.skill_id],
        ),
    )
    edge = SkillEdge(
        edge_id="fill-form-composes-click",
        source_id=dependent.skill_id,
        target_id=skill.skill_id,
        edge_type=EdgeType.COMPOSES_WITH,
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=FakeWiki(skill, [dependent]),
        graph=FakeGraph([edge]),
    )
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
    assert data["diffs"][0]["change_category"] == "metadata_change"
    assert data["has_breaking_changes"] is False
    assert data["review_recommendation"] == "review_required"


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
    data = response.json()
    assert data["has_breaking_changes"] is True
    assert data["review_recommendation"] == "breaking_review_required"


def test_snapshot_diff_returns_impacted_skills_for_breaking_dependency(
    impact_client: TestClient,
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

    response = impact_client.get(
        f"/api/v1/lifecycle/{skill.skill_id}/snapshot/diff",
        params={"from_ref": first_commit, "to_ref": second_commit},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["has_breaking_changes"] is True
    assert data["impacted_skills"] == [
        {
            "skill_id": "fill_form",
            "skill_name": "fill_form",
            "skill_type": "functional",
            "state": "S4",
            "via_edge_type": "composes_with",
            "changed_skill_id": skill.skill_id,
            "method": "hin_meta_path_projection",
            "paper_basis": ["HIN Survey meta-path projection", "SkillX layered skill dependency"],
        }
    ]


def test_snapshot_diff_can_compare_versioned_snapshot_paths(
    client: TestClient,
    git_repo: Path,
    skill: Skill,
) -> None:
    first_commit = _commit_snapshot(git_repo, skill, "snapshot original")
    skill.version = "1.0.1"
    skill.description = "Search wiki entries with filters."
    second_commit = _commit_snapshot(git_repo, skill, "snapshot changed")

    response = client.get(
        f"/api/v1/lifecycle/{skill.skill_id}/snapshot/diff",
        params={
            "from_ref": first_commit,
            "to_ref": second_commit,
            "from_version": "1.0.0",
            "to_version": "1.0.1",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["from_snapshot_path"] == "skills/skill-1234567890/1.0.0.json"
    assert data["to_snapshot_path"] == "skills/skill-1234567890/1.0.1.json"
    assert data["snapshot_path"] == data["to_snapshot_path"]
    assert any(diff["field"] == "description" for diff in data["diffs"])
    assert data["review_recommendation"] == "review_required"


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


def test_propose_maintenance_change_creates_review_bundle(
    client: TestClient,
    git_repo: Path,
    skill: Skill,
) -> None:
    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/propose-maintenance-change",
        json={
            "proposal_id": "proposal-1",
            "patched_skill": {
                "version": "1.0.1",
                "description": "Search wiki entries with filters.",
            },
            "reason": "repair failed postcondition",
            "author": "SkillMaintainerAgent",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["proposal_id"] == "proposal-1"
    assert data["branch_name"] == "skill/search_wiki/skill-12-v1.0.1"
    assert data["snapshot_path"] == "skills/skill-1234567890/1.0.1.json"
    assert data["review_status"] == "review_required"
    assert data["has_breaking_changes"] is False
    assert any(diff["field"] == "description" for diff in data["structured_diff"])
    assert skill.version == "1.0.0"
    assert skill.description == "Search wiki entries."

    assert GitVersionStore(git_repo).current_branch() != data["branch_name"]
    review_snapshot = GitVersionStore(git_repo).read_file_at_ref(data["head_commit"], data["snapshot_path"])
    assert "Search wiki entries with filters." in review_snapshot


def test_propose_maintenance_change_returns_impacted_skills_for_breaking_change(
    impact_client: TestClient,
    skill: Skill,
) -> None:
    response = impact_client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/propose-maintenance-change",
        json={
            "proposal_id": "proposal-breaking",
            "patched_skill": {
                "version": "2.0.0",
                "interface": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "integer"}},
                        "required": ["query"],
                    }
                },
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["review_status"] == "breaking_review_required"
    assert data["impacted_skills"][0]["skill_id"] == "fill_form"
    assert data["impacted_skills"][0]["method"] == "hin_meta_path_projection"


def test_propose_maintenance_change_no_changes_does_not_commit(
    client: TestClient,
    git_repo: Path,
    skill: Skill,
) -> None:
    store = GitVersionStore(git_repo)
    base_branch = store.current_branch()
    base_commit = store.head_commit()

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/propose-maintenance-change",
        json={
            "proposal_id": "proposal-no-change",
            "patched_skill": {},
            "reason": "duplicate proposal",
            "author": "SkillMaintainerAgent",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["review_status"] == "no_changes"
    assert data["head_commit"] == base_commit
    assert data["structured_diff"] == []
    assert store.current_branch() == base_branch
    assert not store.branch_exists(data["branch_name"])


def test_governance_repository_status_is_read_only(
    client: TestClient,
    git_repo: Path,
) -> None:
    response = client.get("/api/v1/lifecycle/repository/status")

    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "git"
    assert data["is_git_repo"] is True
    assert data["branch"]
    assert data["dirty"] is False
    assert data["ahead"] == 0
    assert data["behind"] == 0


def test_propose_maintenance_change_rejects_skill_id_mismatch(
    client: TestClient,
    skill: Skill,
) -> None:
    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/propose-maintenance-change",
        json={
            "proposal_id": "proposal-bad",
            "patched_skill": {"skill_id": "other-skill"},
        },
    )

    assert response.status_code == 400
    assert "skill_id must match" in response.json()["detail"]


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
