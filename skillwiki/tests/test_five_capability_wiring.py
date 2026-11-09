from __future__ import annotations

import hashlib
import json
import asyncio
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillwiki.api.routes import evolution, execution
from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.component_demo import digest, instruction
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillState


def test_execution_governance_context_is_fail_closed() -> None:
    passed = execution._evaluate_governance_context(
        {
            "source_refresh_state": "unchanged",
            "source_commit": "0123456789abcdef0123456789abcdef01234567",
            "skillguard_change_kind": "comment_only",
            "skillguard_environment_status": "known",
        }
    )
    assert passed["blocked"] is False
    assert passed["checks"]["arex"]["promotion_allowed"] is False
    assert passed["checks"]["skillguard"]["status"] == "pass"

    blocked = execution._evaluate_governance_context(
        {
            "source_refresh_state": "stale",
            "source_commit": "0123456789abcdef0123456789abcdef01234567",
        }
    )
    assert blocked["blocked"] is True
    assert blocked["checks"]["arex"]["observed_state"] == "stale"


def test_upstream_candidate_enters_existing_review_queue(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "governance"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Demo", "-c", "user.email=demo@local", "commit", "--allow-empty", "-qm", "base"], check=True)
    monkeypatch.setenv("SKILLOS_GOVERNANCE_REPO", str(repo))
    wiki = MemoryWikiManager()
    parent = asyncio.run(wiki.create(Skill(name="dialogue_candidate_parent", version="1.2.3",
        state=SkillState.RELEASED, implementation=SkillImplementation(prompt_template="parent instruction"))))
    state = SimpleNamespace(wiki=wiki, graph=None, execution_trace_path=tmp_path / "traces.jsonl")
    app = FastAPI()
    app.dependency_overrides[get_app_state] = lambda: state
    app.include_router(evolution.router, prefix="/api/v1")
    evolution.configure_persistent_stores(tmp_path)
    client = TestClient(app)
    candidate_text = "candidate content"
    candidate_hash = "sha256:" + hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
    response = client.post(
        "/api/v1/evolution/upstream-candidates",
        json={
            "component": "gepa",
            "skill_id": parent.skill_id,
            "parent_version": "1.2.3",
            "parent_hash": digest(instruction(parent)),
            "candidate_text": candidate_text,
            "candidate_hash": candidate_hash,
            "upstream_repository": "https://github.com/gepa-ai/gepa",
            "upstream_commit": "d771eb21b5dd3228bc3f567293d2ccfc423fc900",
            "upstream_version": "0.1.4",
            "upstream_license": "MIT",
            "failure_trace": [{"case": "kept-1", "status": "failed"}],
            "model_status": {"state": "not_called", "model": None},
            "trace_id": "trace-demo-1",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["source"] == "upstream:gepa"
    assert payload["metadata"]["candidate_status"] == "draft_pending_review"
    assert payload["metadata"]["auto_publish"] is False
    draft = payload["metadata"]["draft"]
    assert draft["persistence_stage"] == "complete" and draft["diffs"]
    assert asyncio.run(wiki.get(draft["draft_skill_id"])).state == SkillState.DRAFT
    repeated = client.post("/api/v1/evolution/upstream-candidates", json=json.loads(response.request.content))
    assert repeated.status_code == 200
    assert repeated.json()["metadata"]["draft"]["draft_skill_id"] == draft["draft_skill_id"]
    assert len(asyncio.run(wiki.list())) == 2

    listed = client.get("/api/v1/evolution/proposals").json()["proposals"]
    assert any(item["proposal_id"] == payload["proposal_id"] for item in listed)
    evolution.configure_persistent_stores(None)
