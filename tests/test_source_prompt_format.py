"""Source-to-ingest prompt-format contract; fixed responses, no model requests."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skillwiki"))

from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes import ingest
from skillwiki.layers.input_knowledge.pipeline import ExperiencePipeline, _normalize_candidates
from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
from skillwiki.utils.llm_client import LLMResponse


LITERAL = '  原文：{"result": true}; {not: json}; {red, blue}; https://example.invalid/{id}; {{keep}}\n' + "中文长文须完整保留。" * 900 + "\n  "


class FixedSourceResponses:
    """Replace only the external chat operation with four recorded shapes."""

    def __init__(self, prompt=LITERAL, mode="omitted"):
        candidate = {
            "name": "source_prompt_contract",
            "description": "Return a source-grounded structured result.",
            "skill_type": "atomic",
            "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
            "prompt_template": prompt,
            "score": 0.73,
        }
        if mode != "omitted":
            candidate["prompt_format"] = mode
        self.responses = [
            {"actions": ["Return the documented result."], "proposed_skill_name": "source_prompt_contract", "confidence": 0.7},
            {"normalized": [{"verb": "return", "object": "result", "description": "Return the documented result."}]},
            {"description": "Return a source-grounded structured result.", "skill_type": "atomic", "tags": ["source"], "confidence": 0.7},
            {"context_pack": {"summary": "Fixed source contract"},
             "challenges": [{"task_id": "source-contract-1", "task": "Return a result", "rubric": ["Uses the source"]}],
             "candidates": [candidate], "selected_reason": "Fixed response self-score only."},
        ]

    def chat(self, messages):
        assert self.responses, "No repair/retry or extra model call is allowed"
        return LLMResponse(json.dumps(self.responses.pop(0), ensure_ascii=False), "fixed-source-fixture",
                           usage={"total_tokens": 10}, finish_reason="stop")


class OfflineOnly:
    _cfg = SimpleNamespace(api_key="dummy")

    def chat(self, messages):
        raise AssertionError("Offline import must not call a model")


def client_for(pipeline):
    wiki = MemoryWikiManager()
    app = FastAPI()
    app.include_router(ingest.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki, graph=None, pipeline=pipeline, auditor=SkillAuditorAgent()
    )
    return TestClient(app), wiki


@pytest.mark.parametrize("source_type", ["document", "script", "api_doc", "trajectory"])
def test_generated_instruction_retains_literal_mode_and_original_through_review_storage(source_type):
    pipeline = ExperiencePipeline(FixedSourceResponses())
    client, wiki = client_for(pipeline)
    source = "  fixed source {original}\n  "
    parsed = client.post("/api/v1/ingest/parse", json={"source_type": source_type, "content": source})
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["success"] is True
    unit = parsed.json()["units"][0]
    metadata = unit["metadata"]
    selected = metadata["ctx2skill_evidence"]["selected_candidate"]
    assert selected.get("prompt_format") == "literal"
    assert selected["prompt_template"] == LITERAL
    assert selected["score"] == 0.73
    assert metadata["source"]["content"] == source
    assert metadata["candidate_implementation"].get("prompt_format") == "literal"
    request = {
        "source_type": source_type, "unit_id": unit["unit_id"], "raw_content": source,
        "name": unit["proposed_skill_name"], "description": unit["proposed_description"],
        "skill_type": unit["proposed_type"], **metadata["candidate_interface"],
        **metadata["candidate_implementation"], "evaluation": metadata["candidate_evaluation"],
    }
    reviewed = ingest.CandidateSkillReviewRequest.model_validate(request)
    assert reviewed.model_dump().get("prompt_format") == "literal"
    created = client.post("/api/v1/ingest/create-candidate", json=request)
    assert created.status_code == 201, created.text
    assert created.json()["audit"]["passed"] is True
    stored = asyncio.run(wiki.get(created.json()["created_skill"]["skill_id"]))
    assert stored.implementation.model_dump().get("prompt_format") == "literal"
    assert stored.implementation.prompt_template == LITERAL
    assert stored.provenance.creation_context["source"]["content"] == source


def test_generated_instruction_keeps_literal_mode_in_automatic_import():
    client, wiki = client_for(ExperiencePipeline(FixedSourceResponses()))
    response = client.post("/api/v1/ingest/parse-and-create", json={"source_type": "document", "content": "Fixed source."})
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    stored = asyncio.run(wiki.get(response.json()["created_skills"][0]["skill_id"]))
    assert stored.implementation.model_dump().get("prompt_format") == "literal"
    assert stored.implementation.prompt_template == LITERAL


@pytest.mark.parametrize("mode", ["literal", "python_format", None])
def test_review_builder_preserves_mode_and_literal_whitespace(mode):
    request = ingest.CandidateSkillReviewRequest(
        source_type="past_skills", unit_id="source-contract", name="review_contract",
        description="Review a source prompt.", prompt_template=LITERAL, prompt_format=mode,
    )
    skill = ingest._build_candidate_skill(request)
    assert skill.implementation.model_dump().get("prompt_format") == mode
    if mode == "literal":
        assert skill.implementation.prompt_template == LITERAL


@pytest.mark.parametrize("mode", ["literal", "python_format", None])
def test_normalization_preserves_declared_mode(mode):
    source = {"description": "Prompt contract", "prompt_template": LITERAL, "prompt_format": mode}
    candidate = _normalize_candidates([source], "document", {"summary": "Prompt contract"})[0]
    assert candidate.get("prompt_format") == mode
    assert candidate["prompt_template"] == LITERAL


@pytest.mark.parametrize("mode", ["jinja2", "Literal", "", True, 1, [], {}])
def test_invalid_review_mode_is_rejected_before_storage(mode):
    client, wiki = client_for(ExperiencePipeline(OfflineOnly()))
    response = client.post("/api/v1/ingest/create-candidate", json={
        "source_type": "document", "unit_id": "invalid-format", "name": "invalid_format",
        "description": "Reject invalid format", "prompt_template": "Return a result", "prompt_format": mode,
    })
    assert response.status_code == 422, response.text
    assert asyncio.run(wiki.count()) == 0


@pytest.mark.parametrize("mode", ["jinja2", "Literal", "", True, 1, [], {}])
def test_invalid_generated_mode_fails_without_fallback(mode):
    result = ExperiencePipeline(FixedSourceResponses(mode=mode)).process("Fixed source", "document")
    assert result.success is False
    assert result.units == []
    assert any("prompt_format" in error for error in result.errors)


@pytest.mark.parametrize("mode", ["literal", "python_format", None])
@pytest.mark.parametrize("nested", [False, True])
def test_past_skill_explicit_template_preserves_mode_and_text(mode, nested):
    implementation = {"prompt_template": LITERAL, "prompt_format": mode}
    entry = {"name": "past_template_contract", "description": "Imported declared implementation."}
    entry.update({"implementation": implementation} if nested else implementation)
    result = ExperiencePipeline(OfflineOnly()).process(json.dumps(entry, ensure_ascii=False), "past_skills")
    assert result.success is True
    candidate = result.units[0].metadata["candidate_implementation"]
    assert candidate.get("prompt_format") == mode
    assert candidate["prompt_template"] == LITERAL


@pytest.mark.parametrize("field", ["instructions_markdown", "body", "content"])
def test_past_skill_instruction_text_is_literal_and_never_escaped_or_truncated(field):
    entry = {"name": "past_literal_contract", "description": "Imported literal instructions.", field: LITERAL}
    result = ExperiencePipeline(OfflineOnly()).process(json.dumps(entry, ensure_ascii=False), "past_skills")
    assert result.success is True
    implementation = result.units[0].metadata["candidate_implementation"]
    assert implementation.get("prompt_format") == "literal"
    assert LITERAL in implementation["prompt_template"]


def test_past_free_text_retains_full_original_instruction():
    result = ExperiencePipeline(OfflineOnly()).process(LITERAL, "past_skills")
    assert result.success is True
    implementation = result.units[0].metadata["candidate_implementation"]
    assert implementation.get("prompt_format") == "literal"
    assert LITERAL in implementation["prompt_template"]


@pytest.mark.parametrize("source_type,values,expected", [
    ("document", {"task": "TASK", "document_context": "CONTEXT", "allowed_operations": []}, "Complete TASK using only CONTEXT."),
    ("api_doc", {"endpoint": "/status", "parameters": {}}, "Complete the API task for /status using validated {}."),
    ("script", {"task": "TASK", "script_context": "CONTEXT", "dry_run": True, "allowed_paths": []}, "Complete TASK as a script-grounded dry-run analysis from CONTEXT."),
])
def test_old_offline_default_templates_keep_python_format_semantics(source_type, values, expected):
    result = ExperiencePipeline(OfflineOnly()).process("Fixed source", source_type)
    assert result.success is True
    implementation = result.units[0].metadata["candidate_implementation"]
    assert implementation.get("prompt_format") in (None, "python_format")
    assert implementation["prompt_template"].format(**values).startswith(expected)
