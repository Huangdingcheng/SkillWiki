from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.deps import get_app_state
from skillos.api.routes import ingest
from skillos.layers.skill_management.auditor import SkillAuditorAgent


class FakeWiki:
    def __init__(self) -> None:
        self.created = []

    async def create(self, skill):
        self.created.append(skill)
        return skill


def _client(wiki: FakeWiki | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ingest.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        graph=None,
        auditor=SkillAuditorAgent(),
    )
    return TestClient(app)


def _candidate_payload(**overrides):
    payload = {
        "source_type": "trajectory",
        "unit_id": "unit-1",
        "raw_content": "click search input, type query, submit",
        "name": "search_from_trajectory",
        "description": "Search from a trajectory and return a structured result.",
        "skill_type": "atomic",
        "tags": ["web", "search"],
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Search result summary"},
            },
        },
        "postconditions": ["output.summary exists"],
        "prompt_template": "Search for {query} and return a summary.",
        "evaluation": {
            "verifier_specs": [{"type": "json_exists", "path": "output.summary"}],
            "test_case_refs": ["unit-1-review"],
            "benchmark_task_ids": [],
            "validation_summary": "Human reviewer added verifier placeholder from import.",
        },
        "author": "reviewer",
    }
    payload.update(overrides)
    return payload


def test_audit_candidate_does_not_write_to_wiki_and_reports_review_warning() -> None:
    wiki = FakeWiki()
    client = _client(wiki)
    payload = _candidate_payload(
        postconditions=[],
        evaluation={
            "verifier_specs": [],
            "test_case_refs": [],
            "benchmark_task_ids": [],
            "validation_summary": None,
        },
    )

    response = client.post("/api/v1/ingest/audit-candidate", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["skill_name"] == "search_from_trajectory"
    assert data["passed"] is True
    assert any("candidate/draft Skill should define postconditions" in item for item in data["warnings"])
    assert wiki.created == []


def test_create_candidate_stores_s1_with_reviewed_fields() -> None:
    wiki = FakeWiki()
    client = _client(wiki)

    response = client.post("/api/v1/ingest/create-candidate", json=_candidate_payload())

    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["created_skill"]["state"] == "S1"
    assert data["audit"]["passed"] is True
    assert len(wiki.created) == 1
    created = wiki.created[0]
    assert created.state.value == "S1"
    assert created.interface.postconditions == ["output.summary exists"]
    assert created.evaluation.verifier_specs == [{"type": "json_exists", "path": "output.summary"}]
    assert created.provenance.source_type == "trajectory"
    assert created.provenance.source_ids == ["unit-1"]
    assert created.provenance.creation_context["paper_backlog_task"] == "E-P0-1"
    assert created.provenance.creation_context["human_review_required"] is True


def test_create_candidate_accepts_agent_execution_experience() -> None:
    wiki = FakeWiki()
    client = _client(wiki)

    response = client.post(
        "/api/v1/ingest/create-candidate",
        json=_candidate_payload(
            source_type="agent_execution",
            unit_id="execution:plan-1",
            raw_content='{"execution_id":"plan-1","status":"success"}',
            name="skill_from_execution_history",
            tags=["runtime"],
        ),
    )

    assert response.status_code == 201
    assert len(wiki.created) == 1
    created = wiki.created[0]
    assert created.state.value == "S1"
    assert created.provenance.source_type == "agent_execution"
    assert created.provenance.source_ids == ["execution:plan-1"]
    assert created.provenance.creation_context["paper_backlog_task"] == "C-P1-2"
    assert "agent_execution" in created.tags
    assert "candidate-review" in created.tags


def test_create_candidate_rejects_invalid_source_type() -> None:
    client = _client(FakeWiki())

    response = client.post(
        "/api/v1/ingest/create-candidate",
        json=_candidate_payload(source_type="unknown"),
    )

    assert response.status_code == 400
    assert "Unsupported source_type" in response.json()["detail"]
