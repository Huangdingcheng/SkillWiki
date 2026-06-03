from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.deps import get_app_state
from skillos.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillos.api.routes import ingest
from skillos.config.llm_config import LLMConfig
from skillos.layers.input_knowledge.pipeline import ExperiencePipeline
from skillos.layers.skill_management.auditor import SkillAuditorAgent
from skillos.models.skill_model import Skill, SkillImplementation
from skillos.utils.llm_client import LLMClient


class FakeWiki:
    def __init__(self, skills=None) -> None:
        self.created = []
        self._skills = list(skills or [])

    async def create(self, skill):
        self.created.append(skill)
        self._skills.append(skill)
        return skill

    async def list(self, *args, **kwargs):
        return list(self._skills)


def _client(wiki: FakeWiki | None = None, pipeline=None, graph=None) -> TestClient:
    app = FastAPI()
    app.include_router(ingest.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        graph=graph,
        pipeline=pipeline,
        auditor=SkillAuditorAgent(),
    )
    return TestClient(app)


def _dummy_pipeline() -> ExperiencePipeline:
    return ExperiencePipeline(LLMClient(LLMConfig(api_key="dummy")))


def _skill(name: str) -> Skill:
    return Skill(
        name=name,
        description=f"{name} helper Skill.",
        implementation=SkillImplementation(prompt_template=f"Run {name}."),
    )


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


def test_create_candidate_accepts_past_skills_relations() -> None:
    wiki = FakeWiki()
    client = _client(wiki)

    response = client.post(
        "/api/v1/ingest/create-candidate",
        json=_candidate_payload(
            source_type="past_skills",
            unit_id="legacy:login",
            raw_content='{"name":"legacy_login_flow"}',
            name="legacy_login_flow",
            skill_type="functional",
            dependency_ids=["click-id"],
            component_ids=["type-id"],
            sub_skill_ids=["type-id"],
            parent_skill_ids=["old-login-id"],
        ),
    )

    assert response.status_code == 201
    created = wiki.created[0]
    assert created.provenance.source_type == "past_skills"
    assert created.dependency_ids == ["click-id"]
    assert created.component_ids == ["type-id"]
    assert created.implementation.sub_skill_ids == ["type-id"]
    assert created.provenance.parent_skill_ids == ["old-login-id"]


def test_create_candidate_syncs_lightweight_heterogeneous_evidence_chain() -> None:
    wiki = MemoryWikiManager()
    graph = MemoryGraphManager()
    parent = asyncio.run(wiki.create(_skill("parent_login_flow")))
    component = asyncio.run(wiki.create(_skill("click_element")))
    asyncio.run(graph.sync_skill(parent))
    asyncio.run(graph.sync_skill(component))
    client = _client(wiki, graph=graph)

    response = client.post(
        "/api/v1/ingest/create-candidate",
        json=_candidate_payload(
            source_type="past_skills",
            unit_id="related-login:flow-v2",
            raw_content='{"name":"related_login_flow_v2"}',
            name="related_login_flow_v2",
            skill_type="functional",
            tags=["source_group:related-login-pack"],
            component_ids=[component.skill_id],
            sub_skill_ids=[component.skill_id],
            parent_skill_ids=[parent.skill_id],
        ),
    )

    assert response.status_code == 201
    created_id = response.json()["created_skill"]["skill_id"]
    hetero = asyncio.run(graph.get_hetero_graph())
    assert f"skill:{created_id}" in hetero.nodes
    assert "source:related-login-pack" in hetero.nodes
    assert f"execution:ingest:{created_id}" in hetero.nodes
    assert f"validation:ingest:{created_id}" in hetero.nodes
    assert f"version:{created_id}:1.0.0" in hetero.nodes
    edge_pairs = {
        (edge.source_id, edge.target_id, edge.edge_type.value)
        for edge in hetero.edges
    }
    assert ("source:related-login-pack", f"skill:{created_id}", "derived_from") in edge_pairs
    assert (f"skill:{created_id}", f"skill:{component.skill_id}", "composes_with") in edge_pairs
    assert (f"version:{created_id}:1.0.0", f"skill:{parent.skill_id}", "composes_with") in edge_pairs

    projection = asyncio.run(graph.project_hetero_to_skill_graph())
    assert any(
        edge.source_id == created_id and edge.target_id == parent.skill_id
        for edge in projection.edges
    )


def test_parse_document_returns_ctx2skill_evidence() -> None:
    client = _client(FakeWiki(), pipeline=_dummy_pipeline())

    response = client.post(
        "/api/v1/ingest/parse",
        json={
            "source_type": "document",
            "content": """
# Password reset procedure
1. Collect the user email.
2. Call the reset endpoint.
3. Verify the reset token.
The email is required and invalid tokens must fail.
Example: reset alice@example.com.
""",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_type"] == "document"
    assert data["unit_count"] == 1
    metadata = data["units"][0]["metadata"]
    assert metadata["ctx2skill_evidence"]["challenges"]
    assert metadata["candidate_interface"]["postconditions"]
    assert "Ctx2Skill-lite" in metadata["ctx2skill_evidence"]["paper_method"]
    specs = metadata["candidate_evaluation"]["verifier_specs"]
    spec_paths = {spec["path"] for spec in specs}
    assert "input.allowed_operations" in spec_paths
    assert "output.result.extracted_steps" in spec_paths
    assert {"type": "json_equals", "path": "output.verifier.passed", "value": True} in specs


def test_parse_script_returns_dry_run_execution_contract_specs() -> None:
    client = _client(FakeWiki(), pipeline=_dummy_pipeline())

    response = client.post(
        "/api/v1/ingest/parse",
        json={
            "source_type": "script",
            "content": """
python scripts/export_report.py --input data/raw.csv --output build/report.json
The script reads a CSV, writes a JSON report, and requires pandas.
Do not mutate source data during review.
""",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_type"] == "script"
    metadata = data["units"][0]["metadata"]
    prompt_template = metadata["candidate_implementation"]["prompt_template"]
    assert data["units"][0]["proposed_skill_name"] == "script_dry_run_analyzer"
    assert data["units"][0]["proposed_type"] == "functional"
    assert "functional" in metadata["layering_reason"]
    assert prompt_template.startswith(
        "Complete {task} as a script-grounded dry-run analysis"
    )
    formatted = prompt_template.format(
        task="inspect script",
        script_context="echo hello",
        dry_run=True,
        allowed_paths=["demo.sh"],
    )
    assert '{"result":' in formatted
    assert '"arguments":["..."]' in formatted
    specs = metadata["candidate_evaluation"]["verifier_specs"]
    assert {"type": "json_equals", "path": "input.dry_run", "value": True} in specs
    assert {"type": "json_array_nonempty", "path": "input.allowed_paths"} in specs
    assert {"type": "json_equals", "path": "output.result.mutation_avoided", "value": True} in specs
    assert {"type": "json_equals", "path": "output.verifier.passed", "value": True} in specs


def test_parse_past_skills_returns_skillx_layers_and_graph_preview() -> None:
    click = _skill("click_element")
    type_text = _skill("type_text")
    client = _client(FakeWiki([click, type_text]), pipeline=_dummy_pipeline())

    response = client.post(
        "/api/v1/ingest/parse",
        json={
            "source_type": "past_skills",
            "content": json.dumps([
                {
                    "name": "legacy_click",
                    "description": "Click one browser element.",
                    "skill_type": "atomic",
                },
                {
                    "name": "legacy_login_flow",
                    "description": "Log in by filling credentials and waiting for the dashboard.",
                    "steps": ["click username", "type username", "click submit"],
                    "dependencies": ["click_element", "type_text"],
                },
                {
                    "name": "plan_skill_repair",
                    "description": "Plan, review, and route maintenance actions for broken Skills.",
                },
            ]),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_type"] == "past_skills"
    assert data["unit_count"] == 3
    proposed_types = {unit["proposed_type"] for unit in data["units"]}
    assert {"atomic", "functional", "strategic"} <= proposed_types
    functional = next(unit for unit in data["units"] if unit["proposed_skill_name"] == "legacy_login_flow")
    relations = functional["metadata"]["candidate_relations"]
    assert click.skill_id in relations["dependency_ids"]
    assert type_text.skill_id in relations["dependency_ids"]
    assert functional["metadata"]["graph_relation_preview"]


def test_parse_past_skills_jsonl_enriches_schema_prompt_and_weak_relations() -> None:
    client = _client(FakeWiki(), pipeline=_dummy_pipeline())
    jsonl = "\n".join([
        json.dumps({
            "source": "anthropic-skills",
            "source_path": "docx/SKILL.md",
            "name": "docx",
            "description": "Create, edit, redline, and comment on .docx documents.",
            "instructions_markdown": "# DOCX Skill\nUse helper scripts with {placeholder} examples.",
            "files": ["SKILL.md", "scripts/office/pack.py", "scripts/office/unpack.py"],
            "implementation_hints": ["scripts/comment.py"],
        }),
        json.dumps({
            "name": "review_router",
            "description": "Plan, review, and route skill maintenance work.",
        }),
    ])

    response = client.post(
        "/api/v1/ingest/parse",
        json={
            "source_type": "past_skills",
            "content": jsonl,
            "metadata": {"max_candidates": 2},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["unit_count"] == 2
    docx_unit = next(unit for unit in data["units"] if unit["proposed_skill_name"] == "docx")
    metadata = docx_unit["metadata"]
    input_schema = metadata["candidate_interface"]["input_schema"]
    output_schema = metadata["candidate_interface"]["output_schema"]
    assert input_schema["required"] == ["task", "source_context", "artifact_type"]
    assert "source_files" in input_schema["properties"]
    assert "validation" in output_schema["properties"]
    assert "output.validation" in {
        spec["path"] for spec in metadata["candidate_evaluation"]["verifier_specs"]
    }
    assert "Legacy instructions excerpt" in metadata["candidate_implementation"]["prompt_template"]
    assert "{placeholder}" not in metadata["candidate_implementation"]["prompt_template"]
    assert metadata["graph_relation_preview"]
    assert metadata["candidate_relations"]["unresolved_components"]


def test_create_candidate_rejects_invalid_source_type() -> None:
    client = _client(FakeWiki())

    response = client.post(
        "/api/v1/ingest/create-candidate",
        json=_candidate_payload(source_type="unknown"),
    )

    assert response.status_code == 400
    assert "Unsupported source_type" in response.json()["detail"]
