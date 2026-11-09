from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skillwiki.api.deps import app_state
from skillwiki.api.main import _seed_demo_skills, create_app
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.layers.skill_repository import SkillWikiManager
from skillwiki.models import Skill, SkillImplementation, SkillInterface
from skillwiki.utils.llm_client import LLMClient


@pytest.fixture(autouse=True)
def no_model_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Persistence tests must never call a model")

    monkeypatch.setattr(LLMClient, "_do_request", forbidden)
    monkeypatch.setattr(LLMClient, "_stream_request", forbidden)
    for name in (
        "SKILLWIKI_LLM_BUDGET_LIMIT_CNY",
        "SKILLWIKI_LLM_BUDGET_TOTAL_LIMIT_CNY",
        "SKILLWIKI_LLM_BUDGET_LEDGER_PATH",
        "SKILLWIKI_LLM_BUDGET_SCOPE",
        "SKILLWIKI_LLM_BUDGET_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


def make_app(storage: Path):
    return create_app(
        api_key="test-key",
        repository_backend="git",
        skill_storage_dir=storage,
        seed_demo=False,
        llm_retry_count=0,
    )


def make_payload(name: str, **implementation):
    return {
        "name": name,
        "description": f"Persistent {name} from real source material",
        "skill_type": "atomic",
        "tags": ["source", "persistence"],
        "interface": {
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
        },
        "evaluation": {"validation_summary": "Source metadata retained; no execution claim"},
        "implementation": {"prompt_template": "Read and retain the provided source.", **implementation},
    }


def test_git_restart_rebuilds_real_graph_and_version_lineage(tmp_path):
    storage = tmp_path / "skills"
    with TestClient(make_app(storage)) as client:
        dependency = client.post("/api/v1/skills", json=make_payload("source_reader"))
        assert dependency.status_code == 201, dependency.text
        dependency_id = dependency.json()["skill_id"]
        original = client.post(
            "/api/v1/skills",
            json=make_payload("source_summary", sub_skill_ids=[dependency_id]),
        )
        assert original.status_code == 201, original.text
        original_id = original.json()["skill_id"]
        child = client.post(
            f"/api/v1/lifecycle/{original_id}/new-version",
            json={"bump": "patch", "description": "Updated source summary content"},
        )
        assert child.status_code == 200, child.text
        child_id = child.json()["skill_id"]

    # A second lifespan constructs a fresh manager and an empty graph, exactly
    # as a backend process restart does; no demo fixtures are requested.
    with TestClient(make_app(storage)) as client:
        details = client.get(f"/api/v1/skills/{child_id}/full")
        assert details.status_code == 200, details.text
        assert details.json()["description"] == "Updated source summary content"
        assert details.json()["implementation"]["sub_skill_ids"] == [dependency_id]
        assert details.json()["tags"] == ["source", "persistence"]
        assert details.json()["interface"] == make_payload("source_summary")["interface"] | {
            "preconditions": [], "postconditions": [], "side_effects": [],
        }
        # Derived identities must not inherit a parent's validation summary;
        # source/provenance remains available elsewhere in the record.
        assert details.json()["evaluation"]["validation_summary"] is None
        assert client.get(f"/api/v1/skills/{original_id}/full").status_code == 200
        assert set(app_state.graph._nodes) == {dependency_id, original_id, child_id}
        assert app_state.graph._nodes[child_id].tags == ["source", "persistence"]
        edges = {(edge.source_id, edge.target_id, edge.edge_type.value) for edge in app_state.graph._edges}
        assert (child_id, original_id, "evolved_from") in edges
        assert (child_id, dependency_id, "composes_with") in edges
        assert not any(edge.metadata.get("demo") for edge in app_state.graph._edges)
        assert app_state.graph._hetero_graph.nodes
        assert f"skill:{child_id}" in app_state.graph._hetero_graph.nodes
        graph = client.get("/api/v1/graph")
        assert graph.status_code == 200, graph.text
        assert child_id in {node["id"] for node in graph.json()["nodes"]}
        provenance = client.get("/api/v1/graph/heterogeneous")
        assert provenance.status_code == 200, provenance.text
        assert {node["id"] for node in provenance.json()["nodes"]} >= {
            f"skill:{dependency_id}", f"skill:{original_id}", f"skill:{child_id}",
        }
        assert any(edge["source"] == f"skill:{child_id}" for edge in provenance.json()["edges"])


@pytest.mark.asyncio
async def test_explicit_demo_seed_preserves_existing_identity_and_content():
    wiki = MemoryWikiManager()
    original = Skill(
        skill_id="click_element", name="click_element", version="2.0.0",
        description="User-owned click implementation",
        interface=SkillInterface(),
        implementation=SkillImplementation(prompt_template="Preserve user source."),
    )
    await wiki.create(original)
    await _seed_demo_skills(wiki)
    restored = await wiki.get(original.skill_id)
    assert restored.version == "2.0.0"
    assert restored.description == original.description
    assert restored.implementation.prompt_template == original.implementation.prompt_template
    assert [skill.version for skill in await wiki.list() if skill.name == original.name] == ["2.0.0"]


def test_create_app_defaults_to_no_seed_and_deepseek(tmp_path):
    app = create_app(api_key="test-key", skill_storage_dir=tmp_path)
    assert app.state.repository_backend == "git"
    assert app.state.seed_demo is False
    assert app.state.llm_cfg.model == "deepseek-flash"
    assert app.state.llm_cfg.api_url == "https://api.deepseek.com"
    assert app.state.llm_cfg.budget_limit_cny is None


def test_explicit_demo_seed_populates_demo_graph(tmp_path):
    app = create_app(
        api_key="test-key", repository_backend="memory",
        skill_storage_dir=tmp_path, seed_demo=True, llm_retry_count=0,
    )
    with TestClient(app) as client:
        graph = client.get("/api/v1/graph/heterogeneous")
        assert graph.status_code == 200, graph.text
        assert graph.json()["nodes"]
        assert graph.json()["edges"]
        assert any(edge.metadata.get("demo") for edge in app_state.graph._edges)


def test_explicit_budget_environment_reaches_initialized_clients(tmp_path, monkeypatch):
    ledger = tmp_path / "llm-budget.jsonl"
    for name, value in {
        "SKILLWIKI_LLM_BUDGET_LIMIT_CNY": "1",
        "SKILLWIKI_LLM_BUDGET_TOTAL_LIMIT_CNY": "10",
        "SKILLWIKI_LLM_BUDGET_LEDGER_PATH": str(ledger),
        "SKILLWIKI_LLM_BUDGET_SCOPE": "m1",
        "SKILLWIKI_LLM_BUDGET_MAX_OUTPUT_TOKENS": "2048",
    }.items():
        monkeypatch.setenv(name, value)
    app = make_app(tmp_path / "skills")
    with TestClient(app):
        config = app_state.llm._cfg
        assert config.retry_count == 0
        assert config.budget_limit_cny == 1
        assert config.budget_total_limit_cny == 10
        assert config.budget_ledger_path == str(ledger)
        assert config.budget_scope == "m1"
        assert config.budget_max_output_tokens == 2048
    assert not ledger.exists(), "Configuring a budget must not spend or reserve money"


def test_snapshot_and_diff_survive_git_restart(tmp_path, monkeypatch):
    storage = tmp_path / "skills"
    governance = tmp_path / "governance"
    subprocess.run(["git", "init", "--quiet", str(governance)], check=True)
    monkeypatch.setenv("SKILLOS_GOVERNANCE_REPO", str(governance))
    with TestClient(make_app(storage)) as client:
        response = client.post("/api/v1/skills", json=make_payload("snapshot_source"))
        assert response.status_code == 201, response.text
        original_id = response.json()["skill_id"]
        snapshot = client.post(f"/api/v1/lifecycle/{original_id}/snapshot", json={})
        assert snapshot.status_code == 200, snapshot.text
        base_commit = snapshot.json()["commit"]
        response = client.post(
            f"/api/v1/lifecycle/{original_id}/new-version",
            json={"bump": "patch", "description": "Updated preserved snapshot"},
        )
        assert response.status_code == 200, response.text
        child_id = response.json()["skill_id"]
        snapshot = client.post(f"/api/v1/lifecycle/{child_id}/snapshot", json={})
        assert snapshot.status_code == 200, snapshot.text

    with TestClient(make_app(storage)) as client:
        difference = client.get(
            f"/api/v1/lifecycle/{child_id}/snapshot/diff",
            params={"from_ref": base_commit, "from_version": "1.0.0"},
        )
        assert difference.status_code == 200, difference.text
        assert difference.json()["diffs"]
        history = client.get(f"/api/v1/lifecycle/{child_id}/snapshot/history")
        assert history.status_code == 200, history.text
        assert history.json()["history"]
        fresh_wiki = SkillWikiManager(storage_dir=storage)
        assert asyncio.run(fresh_wiki.get(original_id)).version == "1.0.0"
        assert asyncio.run(fresh_wiki.get(child_id)).version == "1.0.1"
