from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.deps import get_app_state
from skillos.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillos.api.routes import harness
from skillos.layers.skill_runtime import SkillExecutor
from skillos.models.skill_model import (
    Skill,
    SkillEvaluation,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillTestCase,
)


def _client(wiki: MemoryWikiManager, graph: MemoryGraphManager) -> TestClient:
    app = FastAPI()
    app.include_router(harness.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        graph=graph,
        executor=SkillExecutor(skill_registry=wiki),
        repair=None,
    )
    return TestClient(app)


def _broken_draft() -> Skill:
    return Skill(
        name="api_harness_extract_email",
        description="Extract an email field from text.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["text", "email"],
            },
            output_schema={"type": "object", "properties": {"email": {"type": "string"}}},
            postconditions=["output.email exists"],
        ),
        implementation=SkillImplementation(code="output['summary'] = input_data.get('text', '')"),
        test_cases=[
            SkillTestCase(
                name="email case",
                input_data={"text": "Contact Ada at ada@example.com", "email": "ada@example.com"},
            )
        ],
        evaluation=SkillEvaluation(
            verifier_specs=[{"type": "json_exists", "path": "output.email"}],
        ),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )


@pytest.mark.asyncio
async def test_harness_verify_loop_api_repairs_and_lists_evidence():
    wiki = MemoryWikiManager()
    graph = MemoryGraphManager()
    skill = await wiki.create(_broken_draft())
    await graph.sync_skill(skill)
    client = _client(wiki, graph)

    response = client.post(
        f"/api/v1/harness/{skill.skill_id}/verify-loop",
        json={"harness": "local_skillos", "max_attempts": 3},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "verified"
    assert data["promotion_allowed"] is True
    assert data["attempt_count"] == 2
    assert data["score"]["overall"] >= 0.75
    loop_id = data["loop_id"]

    detail = client.get(f"/api/v1/harness/{loop_id}")
    assert detail.status_code == 200
    assert detail.json()["loop_id"] == loop_id

    listed = client.get("/api/v1/harness", params={"limit": 5})
    assert listed.status_code == 200
    assert any(item["loop_id"] == loop_id for item in listed.json()["loops"])


@pytest.mark.asyncio
async def test_harness_verify_loop_api_rejects_missing_skill():
    client = _client(MemoryWikiManager(), MemoryGraphManager())

    response = client.post("/api/v1/harness/missing/verify-loop", json={})

    assert response.status_code == 404
