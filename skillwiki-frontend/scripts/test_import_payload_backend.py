"""Actual TS UI builders to real backend audit/import/native runtime, offline."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skillwiki"))
spec = importlib.util.spec_from_file_location("source_fixtures", ROOT / "tests/test_source_prompt_format.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.utils.llm_client import LLMResponse


def builder(unit, source_type):
    process = subprocess.run(
        ["node", str(ROOT / "skillwiki-frontend/scripts/knowledge-import-payload.mjs")],
        input=json.dumps({"unit": unit, "sourceType": source_type}, ensure_ascii=False),
        encoding="utf-8", capture_output=True, timeout=15,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize("source_type", ["document", "trajectory", "api_doc", "script"])
@pytest.mark.parametrize("mode", ["literal", "python_format", None, "omitted"])
def test_actual_ui_builder_preserves_backend_contract(source_type, mode):
    prompt = fixtures.LITERAL if mode == "literal" else "Return result for {task}."
    pipeline = fixtures.ExperiencePipeline(fixtures.FixedSourceResponses(prompt=prompt))
    client, wiki = fixtures.client_for(pipeline)
    response = client.post("/api/v1/ingest/parse", json={"source_type": source_type, "content": "Fixed source context."})
    assert response.status_code == 200, response.text
    unit = response.json()["units"][0]
    for implementation in [unit["metadata"]["candidate_implementation"], unit["metadata"]["ctx2skill_evidence"]["selected_candidate"]]:
        if mode == "omitted":
            implementation.pop("prompt_format", None)
        else:
            implementation["prompt_format"] = mode
    built = builder(unit, source_type)
    assert built["ok"], built
    payload = built["payload"]
    assert payload["prompt_template"] == prompt
    if mode == "omitted":
        assert "prompt_format" not in payload
    else:
        assert payload["prompt_format"] == mode
    audited = client.post("/api/v1/ingest/audit-candidate", json=payload)
    assert audited.status_code == 200, audited.text
    assert audited.json()["passed"] is True, audited.json()
    created = client.post("/api/v1/ingest/create-candidate", json=payload)
    assert created.status_code == 201, created.text
    stored = asyncio.run(wiki.get(created.json()["created_skill"]["skill_id"]))
    assert stored.implementation.prompt_format == (None if mode == "omitted" else mode)
    assert stored.implementation.prompt_template == prompt

    class FixedRuntime:
        def __init__(self):
            self.calls = []

        def chat(self, messages):
            self.calls.append([message.to_dict() for message in messages])
            return LLMResponse('{"result":"ok"}', "offline-ui-test", {}, "stop")

    llm = FixedRuntime()
    record = asyncio.run(SkillExecutor(llm_client=llm, max_retries=0).execute_single(stored, {"task": "ACTUAL_TASK"}))
    assert record.status.value == "success", record.error_message
    assert llm.calls[0][1]["content"] == (prompt if mode == "literal" else "Return result for ACTUAL_TASK.")


@pytest.mark.parametrize("mode", ["unknown", "", True, 1, [], {}])
def test_actual_ui_builder_rejects_unknown_mode(mode):
    pipeline = fixtures.ExperiencePipeline(fixtures.FixedSourceResponses())
    client, _ = fixtures.client_for(pipeline)
    unit = client.post("/api/v1/ingest/parse", json={"source_type": "document", "content": "Fixed source context."}).json()["units"][0]
    unit["metadata"]["candidate_implementation"]["prompt_format"] = mode
    before = copy.deepcopy(unit)
    result = builder(unit, "document")
    assert result["ok"] is False, result
    assert "prompt_format" in result["error"]
    assert unit == before


def test_explicit_null_is_not_replaced_by_selected_candidate_literal():
    pipeline = fixtures.ExperiencePipeline(fixtures.FixedSourceResponses(prompt="Return result for {task}."))
    client, _ = fixtures.client_for(pipeline)
    unit = client.post("/api/v1/ingest/parse", json={"source_type": "document", "content": "Fixed source context."}).json()["units"][0]
    unit["metadata"]["candidate_implementation"]["prompt_format"] = None
    built = builder(unit, "document")
    assert built["ok"], built
    assert built["payload"]["prompt_format"] is None


@pytest.mark.parametrize("mode", ["literal", "python_format", None, "omitted"])
def test_past_skill_prompt_reaches_actual_ui_and_backend(mode):
    prompt = fixtures.LITERAL if mode == "literal" else "Return result for {task}."
    implementation = {"prompt_template": prompt}
    if mode != "omitted":
        implementation["prompt_format"] = mode
    entry = {
        "name": "past_ui_contract", "description": "Preserve declared imported instructions.",
        "implementation": implementation,
        "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
    }
    client, wiki = fixtures.client_for(fixtures.ExperiencePipeline(fixtures.OfflineOnly()))
    parsed = client.post("/api/v1/ingest/parse", json={"source_type": "past_skills", "content": json.dumps(entry, ensure_ascii=False)})
    assert parsed.status_code == 200, parsed.text
    built = builder(parsed.json()["units"][0], "past_skills")
    assert built["ok"], built
    assert built["payload"]["prompt_template"] == prompt
    assert built["payload"].get("prompt_format") == (None if mode == "omitted" else mode)
    created = client.post("/api/v1/ingest/create-candidate", json=built["payload"])
    assert created.status_code == 201, created.text
    assert created.json()["audit"]["passed"] is True
    stored = asyncio.run(wiki.get(created.json()["created_skill"]["skill_id"]))
    assert stored.implementation.prompt_format == (None if mode == "omitted" else mode)
    assert stored.implementation.prompt_template == prompt


def test_selected_candidate_fallback_keeps_its_own_prompt_format():
    client, _ = fixtures.client_for(fixtures.ExperiencePipeline(fixtures.FixedSourceResponses()))
    unit = client.post("/api/v1/ingest/parse", json={"source_type": "document", "content": "Fixed source context."}).json()["units"][0]
    unit["metadata"]["candidate_implementation"] = {}
    built = builder(unit, "document")
    assert built["ok"], built
    assert built["payload"]["prompt_template"] == fixtures.LITERAL
    assert built["payload"]["prompt_format"] == "literal"
