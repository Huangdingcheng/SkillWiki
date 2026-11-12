from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes import evolution
from skillwiki.layers.feedback_evolution import RepairResult, SkillMonitor
from skillwiki.layers.skill_governance import canonical_skill_artifact_hash
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillState


@pytest.fixture
def repair_client(tmp_path):
    wiki = MemoryWikiManager()
    parent = asyncio.run(wiki.create(Skill(
        name="repair_review_parent", version="1.2.3", state=SkillState.RELEASED,
        implementation=SkillImplementation(prompt_template="Original instruction"),
    )))
    parent.evaluation.harness_validation = {"status": "passed", "old_proof": True}
    parent = asyncio.run(wiki.update(parent.skill_id, evaluation=parent.evaluation))
    state = SimpleNamespace(wiki=wiki, monitor=SkillMonitor(),
                            repair=SimpleNamespace(repair=AsyncMock()))
    app = FastAPI()
    app.include_router(evolution.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: state
    evolution.configure_persistent_stores(tmp_path)
    try:
        with TestClient(app) as client:
            yield client, state, parent, tmp_path
    finally:
        evolution.configure_persistent_stores(None)


def test_successful_repair_persists_reviewable_patch_without_mutating_parent(repair_client):
    client, state, parent, store = repair_client
    before = parent.model_dump(mode="json")
    candidate = parent.model_copy(deep=True)
    candidate.bump_version("patch")
    candidate.state = SkillState.DRAFT
    candidate.implementation.prompt_template = "Repaired instruction"
    state.repair.repair.return_value = RepairResult(
        skill_id=parent.skill_id, success=True, fix_type="prompt_fix",
        repaired_skill=candidate, root_cause="Missing return constraint",
        repair_notes="Require the declared output", confidence=0.8,
    )
    response = client.post(f"/api/v1/evolution/repair/{parent.skill_id}")
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result.get("proposal_id"), "A successful repair must persist a review proposal"
    assert result["repaired_skill_id"] is None
    evolution.configure_persistent_stores(store)
    proposals = client.get("/api/v1/evolution/proposals").json()["proposals"]
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["proposal_id"] == result["proposal_id"]
    assert proposal["status"] == "pending"
    assert proposal["requires_human_review"] is True
    assert proposal["validation_status"] == "untested"
    metadata = proposal["metadata"]
    assert metadata["parent_version"] == "1.2.3"
    assert metadata["parent_hash"] == canonical_skill_artifact_hash(parent)
    assert metadata["parent_content"]["implementation"]["prompt_template"] == "Original instruction"
    assert "harness_validation" not in metadata["parent_content"]["evaluation"]
    assert metadata["auto_publish"] is False
    patch = metadata["patched_skill"]
    assert patch["implementation"]["prompt_template"] == "Repaired instruction"
    assert "evaluation" not in patch
    assert "state" not in patch
    assert "skill_id" not in patch
    accepted = client.post(f"/api/v1/evolution/proposals/{proposal['proposal_id']}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["next_action"]["endpoint"].endswith("/propose-maintenance-change")
    assert asyncio.run(state.wiki.get(parent.skill_id)).model_dump(mode="json") == before
    assert len(asyncio.run(state.wiki.list())) == 1
    assert state.repair.repair.await_count == 1


def test_failed_repair_has_no_success_proposal(repair_client):
    client, state, parent, _ = repair_client
    state.repair.repair.return_value = RepairResult(skill_id=parent.skill_id, error="Provider unavailable")
    response = client.post(f"/api/v1/evolution/repair/{parent.skill_id}")
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert client.get("/api/v1/evolution/proposals").json()["total"] == 0


def test_deprecation_recommendation_is_review_only(repair_client):
    client, state, parent, store = repair_client
    state.repair.repair.return_value = RepairResult(
        skill_id=parent.skill_id, success=True, fix_type="deprecate",
        should_deprecate=True, root_cause="Source no longer supported", confidence=0.9,
    )
    response = client.post(f"/api/v1/evolution/repair/{parent.skill_id}")
    assert response.status_code == 200
    evolution.configure_persistent_stores(store)
    proposals = client.get("/api/v1/evolution/proposals").json()["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["recommended_action"] == "deprecate"
    assert proposals[0]["metadata"]["patched_skill"] is None
    assert asyncio.run(state.wiki.get(parent.skill_id)).state == SkillState.RELEASED


@pytest.mark.parametrize("wrong_parent, missing_patch, status", [(True, False, 409), (False, True, 502)])
def test_invalid_success_does_not_enqueue(repair_client, wrong_parent, missing_patch, status):
    client, state, parent, _ = repair_client
    candidate = None if missing_patch else parent.model_copy(deep=True)
    if wrong_parent:
        candidate.skill_id = "different-parent"
    state.repair.repair.return_value = RepairResult(
        skill_id=parent.skill_id, success=True, repaired_skill=candidate,
    )
    assert client.post(f"/api/v1/evolution/repair/{parent.skill_id}").status_code == status
    assert client.get("/api/v1/evolution/proposals").json()["total"] == 0


def test_persistence_failure_is_not_reported_as_success(repair_client, monkeypatch):
    client, state, parent, store = repair_client
    state.repair.repair.return_value = RepairResult(
        skill_id=parent.skill_id, success=True, repaired_skill=parent.model_copy(deep=True),
    )
    persist = evolution._persist_proposal_queue
    monkeypatch.setattr(evolution, "_persist_proposal_queue", lambda: None)
    response = client.post(f"/api/v1/evolution/repair/{parent.skill_id}")
    assert response.status_code == 503
    assert response.json()["detail"]["live_skill_updated"] is False
    assert len(asyncio.run(state.wiki.list())) == 1
    unsaved_id = response.json()["detail"]["proposal_id"]
    assert client.get("/api/v1/evolution/proposals").json()["total"] == 0
    assert client.post(f"/api/v1/evolution/proposals/{unsaved_id}/accept").status_code == 404
    monkeypatch.setattr(evolution, "_persist_proposal_queue", persist)
    retried = client.post(f"/api/v1/evolution/repair/{parent.skill_id}")
    assert retried.status_code == 200
    assert retried.json()["proposal_id"] != unsaved_id
    evolution.configure_persistent_stores(store)
    assert client.get("/api/v1/evolution/proposals").json()["total"] == 1


def test_same_patch_deduplicates_but_different_parent_version_does_not(repair_client):
    client, state, parent, _ = repair_client
    state.repair.repair.return_value = RepairResult(
        skill_id=parent.skill_id, success=True, repaired_skill=parent.model_copy(deep=True),
    )
    endpoint = f"/api/v1/evolution/repair/{parent.skill_id}"
    first = client.post(endpoint).json()
    repeated = client.post(endpoint).json()
    assert repeated["proposal_id"] == first["proposal_id"]
    asyncio.run(state.wiki.update(parent.skill_id, version="1.2.4"))
    updated = client.post(endpoint).json()
    assert updated["proposal_id"] != first["proposal_id"]
    assert client.get("/api/v1/evolution/proposals").json()["total"] == 2
