from __future__ import annotations

import hashlib
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from skillwiki.layers.skill_management.dialogue_demo import (
    DIAGNOSTIC_CASES, HELDOUT_CASES, DialogueTaskEvaluator, expected_graph, verify_graph,
)
from skillwiki.layers.skill_management.gepa_skillopt_adapter import (
    run_gepa_reflection, run_skillopt_reflection,
)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("optimizer tests must never open a network connection")
    monkeypatch.setattr(socket, "create_connection", fail)


def parent_fields(text):
    return dict(parent_skill_id="dialogue-demo", parent_version="1.0",
                parent_skill_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest())


def response(text):
    return dict(text=text, model="fixture-no-network", usage={"completion_tokens": 32},
                receipt={"fixture": True})


def failure_rows():
    return [{**dict(DIAGNOSTIC_CASES[0]), "output": '{"nodes": [], "edges": []}',
             "feedback": "speaker and line IDs were lost"}]


def test_official_gepa_reflects_on_real_graph_outputs_and_cache_keeps_holdout_separate():
    seed = "Make a graph."
    generated = "Preserve speaker and text per input line; use line-N ids and consecutive directed edges."
    prompts, task_calls = [], []

    def task_model(*, skill_text, dialogue, case_id):
        task_calls.append(case_id)
        graph = expected_graph(dialogue)
        if "Preserve speaker" not in skill_text:
            graph["nodes"][0]["speaker"] = "lost"
        return response(json.dumps(graph))

    def llm(**kwargs):
        prompts.append(kwargs)
        return response("```\n" + generated + "\n```")

    evaluator = DialogueTaskEvaluator(task_model)
    record = run_gepa_reflection(seed_text=seed, diagnostic_cases=DIAGNOSTIC_CASES,
        evaluator=evaluator, llm=llm, **parent_fields(seed))
    assert record.status == "candidate_generated", record.blocked_reason
    assert record.candidate_text == generated
    assert len(prompts) == 1
    assert prompts[0]["max_output_tokens"] == 4096 and prompts[0]["retries"] == 0
    assert "lost" in prompts[0]["user"] and "line-N" in prompts[0]["user"]
    assert all(case["input"] not in prompts[0]["user"] for case in HELDOUT_CASES)
    assert record.metadata["proposal_count"] == 1
    assert record.metadata["num_candidates"] == 2
    assert record.metadata["best_candidate"]["skill_text"] == generated
    # GEPA seed/train/full val repeated callbacks are cached as two texts x two inputs.
    assert evaluator.calls == 4
    assert set(task_calls) == {c["id"] for c in DIAGNOSTIC_CASES}
    assert evaluator.evaluate_holdout(generated)[0]["score"] == 1.0
    assert evaluator.calls == 5


def test_gepa_failed_transport_has_one_attempt_and_no_retry():
    calls = []
    def llm(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("injected transport failure")
    record = run_gepa_reflection(seed_text="seed", diagnostic_cases=DIAGNOSTIC_CASES,
        evaluator=lambda *_: {"score": 0, "output": "{}", "feedback": "invalid"},
        llm=llm, **parent_fields("seed"))
    assert record.status.startswith("blocked_")
    assert len(calls) == 1
    assert record.candidate_text == "seed"
    assert record.metadata["transport_attempts"][0]["state"] == "failed"
    assert record.model_status["state"] == "failed"


@pytest.mark.parametrize("method", ["gepa", "skillopt"])
@pytest.mark.parametrize("bad", [{"retries": 1}, {"max_output_tokens": 4097}, {"max_output_tokens": 0}])
def test_invalid_controls_fail_before_any_model_call(method, bad):
    def llm(**kwargs):
        pytest.fail("model must not be called")
    if method == "gepa":
        result = run_gepa_reflection(seed_text="seed", diagnostic_cases=DIAGNOSTIC_CASES,
            evaluator=lambda *_: pytest.fail("evaluator must not run"), llm=llm,
            **parent_fields("seed"), **bad)
    else:
        result = run_skillopt_reflection(parent_skill="seed", failure_trace=failure_rows(),
            llm=llm, **parent_fields("seed"), **bad)
    assert result.status == "blocked_controls"


def test_parent_hash_mismatch_and_holdout_fail_before_calls():
    def llm(**kwargs):
        pytest.fail("model must not be called")
    for fields, cases, status in [
        ({**parent_fields("seed"), "parent_skill_hash": "sha256:bad"}, DIAGNOSTIC_CASES, "blocked_parent_hash_mismatch"),
        (parent_fields("seed"), HELDOUT_CASES, "blocked_dataset"),
    ]:
        record = run_gepa_reflection(seed_text="seed", diagnostic_cases=cases,
            evaluator=lambda *_: pytest.fail("evaluator must not run"), llm=llm, **fields)
        assert record.status == status
    patched = run_skillopt_reflection(parent_skill="seed", failure_trace=HELDOUT_CASES,
        llm=llm, **parent_fields("seed"))
    assert patched.status == "blocked_dataset"


def test_official_skillopt_analyst_and_patch_engine_use_bounded_isolated_transport():
    parent = "# Skill\nold\n"
    calls = []
    before = {k: v for k, v in sys.modules.items() if k.startswith("skillopt")}
    def llm(**kwargs):
        calls.append(kwargs)
        return response(json.dumps({"patch": {"edits": [{"op": "replace", "target": "old", "content": "preserve speaker"}]}}))
    result = run_skillopt_reflection(parent_skill=parent, failure_trace=failure_rows(),
        llm=llm, **parent_fields(parent))
    assert result.status == "patched", result.blocked_reason
    assert result.updated_skill == "# Skill\npreserve speaker\n"
    assert len(calls) == 1 and calls[0]["retries"] == 0 and calls[0]["max_output_tokens"] == 4096
    assert "speaker and line IDs" in calls[0]["user"]
    assert all(c["input"] not in calls[0]["user"] for c in HELDOUT_CASES)
    attempts = result.candidate.metadata["transport_attempts"]
    assert attempts[0]["upstream_retries"] == 3 and attempts[0]["upstream_output_tokens"] == 16384
    assert result.patch_report[0]["status"] == "applied_replace"
    assert result.candidate.provenance["source_sha256"]["skillopt/gradient/reflect.py"]
    assert {k: v for k, v in sys.modules.items() if k.startswith("skillopt")} == before


@pytest.mark.parametrize("edits,status", [
    ([{"op": "append", "content": str(i)} for i in range(5)], "blocked_controls"),
    ([{"op": "replace", "target": "missing", "content": "new"}], "blocked_patch_report"),
    ([{"op": "insert_after", "target": "missing", "content": "new"}], "blocked_patch_report"),
    ([{"op": "replace", "target": "old", "content": "old"}], "blocked_noop_patch"),
    ([{"op": "replace", "target": "old", "content": "new"}, {"op": "delete", "target": "missing"}], "blocked_patch_report"),
])
def test_skillopt_rejects_excess_skipped_fallback_noop_or_partial_patches_atomically(edits, status):
    parent = "old"
    result = run_skillopt_reflection(parent_skill=parent, failure_trace=failure_rows(),
        llm=lambda **_: response(json.dumps({"patch": {"edits": edits}})), **parent_fields(parent))
    assert result.status == status, result.blocked_reason
    assert result.updated_skill == parent and result.candidate.candidate_text == parent


def test_skillopt_concurrent_sessions_never_cross_bind_transports():
    def one(label):
        return run_skillopt_reflection(parent_skill="old", failure_trace=failure_rows(),
            llm=lambda **_: response(json.dumps({"patch": {"edits": [{"op": "replace", "target": "old", "content": label}]}})),
            **parent_fields("old"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, ["first", "second"]))
    assert [r.updated_skill for r in results] == ["first", "second"]


def test_graph_evaluator_does_not_retry_failed_dispatch_or_reuse_changed_input():
    calls = []
    def model(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("ambiguous dispatch outcome")
    evaluator = DialogueTaskEvaluator(model)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            evaluator("skill", DIAGNOSTIC_CASES[0])
    assert len(calls) == evaluator.calls == 1
    with pytest.raises(RuntimeError):
        evaluator("skill", {**DIAGNOSTIC_CASES[0], "input": "A: changed\nB: input"})
    assert len(calls) == evaluator.calls == 2


@pytest.mark.parametrize("method", ["gepa", "skillopt"])
def test_truncated_model_response_preserves_accounting_and_fails_closed(method):
    calls = []
    def llm(**kwargs):
        calls.append(kwargs)
        return {**response("partial"), "finish_reason": "length"}
    if method == "gepa":
        record = run_gepa_reflection(seed_text="seed", diagnostic_cases=DIAGNOSTIC_CASES,
            evaluator=lambda *_: {"score": 0, "output": "{}", "feedback": "invalid"},
            llm=llm, **parent_fields("seed"))
    else:
        record = run_skillopt_reflection(parent_skill="seed", failure_trace=failure_rows(),
            llm=llm, **parent_fields("seed")).candidate
    assert len(calls) == 1 and record.status.startswith("blocked_")
    assert record.model_status == {"state": "failed", "model": "fixture-no-network", "usage": {"completion_tokens": 32}}


def test_skillopt_source_tamper_blocks_before_dispatch(tmp_path):
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "skillopt/gradient/reflect.py"
    target.parent.mkdir(parents=True)
    original = root / "artifacts/20260929-skillopt-c-ref-42/source/skillopt/gradient/reflect.py"
    target.write_bytes(original.read_bytes() + b"\n# tampered\n")
    result = run_skillopt_reflection(parent_skill="seed", failure_trace=failure_rows(),
        llm=lambda **_: pytest.fail("source mismatch must prevent dispatch"), source_root=tmp_path,
        **parent_fields("seed"))
    assert result.status == "blocked_skillopt_reflection"
    assert "differs from pinned archive" in result.blocked_reason


@pytest.mark.parametrize("output", ["bad JSON", "[]", '{"nodes":[],"edges":[]}', '{"nodes":null,"edges":null}'])
def test_verifier_rejects_malformed_or_wrong_graph(output):
    assert verify_graph(DIAGNOSTIC_CASES[0]["input"], output)[0] is False


def test_verifier_rejects_reversed_edges_and_text_changes():
    text = DIAGNOSTIC_CASES[0]["input"]
    graph = expected_graph(text)
    graph["edges"] = [{"from": "line-2", "to": "line-1"}]
    assert verify_graph(text, graph)[0] is False
    graph = expected_graph(text)
    graph["nodes"][0]["text"] = "fabricated"
    assert verify_graph(text, graph)[0] is False
