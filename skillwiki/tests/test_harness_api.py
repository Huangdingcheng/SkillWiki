from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillwiki.api.routes import harness
from skillwiki.layers.skill_runtime import SkillExecutor
from skillwiki.config.llm_config import LLMConfig
from skillwiki.utils.llm_client import LLMClient
from skillwiki.models.skill_model import (
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


def _budgeted_client(wiki: MemoryWikiManager, graph: MemoryGraphManager, tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(harness.router, prefix="/api/v1")
    cfg = LLMConfig(
        api_key="test", model="deepseek-flash", api_url="https://api.deepseek.com",
        budget_limit_cny=1, budget_total_limit_cny=10,
        budget_ledger_path=str(tmp_path / "budget.jsonl"), budget_scope="m1",
        budget_max_output_tokens=2048,
    )
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        graph=graph,
        executor=SkillExecutor(skill_registry=wiki),
        repair=None,
        llm=LLMClient(cfg),
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
async def test_harness_verify_loop_api_preserves_failure_without_repair_provider():
    wiki = MemoryWikiManager()
    graph = MemoryGraphManager()
    skill = await wiki.create(_broken_draft())
    await graph.sync_skill(skill)
    client = _client(wiki, graph)

    response = client.post(
        f"/api/v1/harness/{skill.skill_id}/verify-loop",
        json={"harness": "local_skillwiki", "max_attempts": 3},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["attempts"][0]["harness"] == "local_skillwiki"
    assert data["status"] == "needs_human_review"
    assert data["promotion_allowed"] is False
    assert data["attempt_count"] == 1
    assert data["score"]["verifier_pass_rate"] == 0.0
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


def test_harness_budget_rejects_external_codex_before_dispatch(tmp_path):
    client = _budgeted_client(MemoryWikiManager(), MemoryGraphManager(), tmp_path)
    response = client.post(
        "/api/v1/harness/any-skill/verify-loop",
        json={"harness": "codex_cli"},
    )
    assert response.status_code == 403
    assert "budget guard" in response.json()["detail"]


def test_historical_benchflow_runs_are_read_only_and_keep_admission_blocked():
    app = FastAPI()
    app.include_router(harness.router, prefix="/api/v1")
    client = TestClient(app)

    response = client.get("/api/v1/harness/benchflow/runs")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert [run["skill_mode"] for run in data["runs"]] == ["no-skill", "with-skill", "with-skill"]
    assert [run["observed_reward"] for run in data["runs"]] == [0.833, 1.0, 1.0]
    assert all(run["evidence_kind"] == "historical_real_run" for run in data["runs"])
    assert all(run["admission_status"] == "blocked" for run in data["runs"])
    assert all(run["evidence_integrity"] == "missing" for run in data["runs"])
    assert all(run["model_result_eligible"] is False for run in data["runs"])
    assert all(run["promotion_allowed"] is False for run in data["runs"])
    assert all(run["skill_binding"] is None for run in data["runs"])
    assert all(run["cost"]["usage_source"] == "provider_response" for run in data["runs"])

    run_id = data["runs"][1]["run_id"]
    detail = client.get(f"/api/v1/harness/benchflow/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run_id
    assert client.get("/api/v1/harness/benchflow/runs/unknown").status_code == 404


def test_current_benchflow_registry_exposes_blocked_receipt_without_promotion():
    app = FastAPI()
    app.include_router(harness.router, prefix="/api/v1")
    client = TestClient(app)

    response = client.get("/api/v1/harness/benchflow/current-runs")
    assert response.status_code == 200
    rows = response.json()["runs"]
    row = next(item for item in rows if item["run_id"] == "r1-no-skill")
    assert row["status"] == "blocked"
    assert row["evidence_integrity"] == "mismatch"
    assert row["observed_reward"] is None
    assert row["model_result_eligible"] is False
    assert row["promotion_allowed"] is False
    assert client.get("/api/v1/harness/benchflow/current-runs/r1-no-skill").status_code == 200
    assert client.get("/api/v1/harness/benchflow/current-runs/..%2Fsecret").status_code == 404
