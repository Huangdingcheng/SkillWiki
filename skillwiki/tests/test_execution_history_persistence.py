"""Actual disk restart, immutable identity and failure-evidence regression."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from skillwiki.api import execution_history as storage
from skillwiki.api.deps import get_app_state
from skillwiki.api.routes import execution
from skillwiki.api.schemas import ExecutionStepResult


def app_state(tmp_path):
    return SimpleNamespace(execution_trace_path=tmp_path / "runtime-traces/retrieval-traces.jsonl")


def store_result(app, ident="failed:one", passed=False, score=0.0):
    step = ExecutionStepResult(step_id="step-1", skill_id="isolated-fixture", skill_name="fixture",
                               status="failed", result={"nodes": [], "score": score}, error="verification failed")
    unit = execution._build_execution_experience_unit(
        execution_id=ident, goal="fixed task", status="failed", steps=[step],
        final_state={"draft": True, "evidence_kind": "fixture"}, retrieved_skills=["isolated-fixture"],
        retrieved_skill_versions={"isolated-fixture": "1.0.1"}, provenance_refs=["fixture-source"],
        verifier_passed=passed, verifier_summary={"passed": passed, "score": score}, total_latency_ms=0)
    execution._store_execution_history(app=app, execution_id=ident, goal="fixed task", status="failed",
        steps=[step], success_count=0, total_latency_ms=0, retrieved_skill_count=1, experience_unit=unit)
    return unit


def client_for(app):
    web = FastAPI()
    web.include_router(execution.router)
    web.dependency_overrides[get_app_state] = lambda: app
    return TestClient(web)


def test_fresh_app_reads_full_result_after_memory_is_lost(tmp_path, monkeypatch):
    first = app_state(tmp_path)
    store_result(first)
    monkeypatch.setattr(execution, "_execution_history", [])
    with client_for(app_state(tmp_path)) as client:
        summary = client.get("/execution/history").json()
        row_response = client.get("/execution/history/failed:one")
        assert row_response.status_code == 200
        row = row_response.json()
        assert len(summary) == 1 and summary[0]["status"] == "failed"
        assert row["steps"][0]["result"] == {"nodes": [], "score": 0.0}
        assert row["verifier_passed"] is False and row["verifier_summary"]["score"] == 0.0
        assert row["final_state"] == {"draft": True, "evidence_kind": "fixture"}
        unit = client.get("/execution/history/failed:one/experience").json()
        assert unit["metadata"]["retrieved_skill_versions"] == {"isolated-fixture": "1.0.1"}
        assert json.loads(unit["raw_content"])["verifier_passed"] is False


def test_zero_and_missing_score_stay_different_after_reload(tmp_path):
    app = app_state(tmp_path)
    store_result(app, "zero", False, 0.0)
    store_result(app, "missing", None, None)
    rows = {row["execution_id"]: row for row in storage.load(storage.history_directory(app))}
    assert rows["zero"]["verifier_summary"]["score"] == 0.0
    assert rows["missing"]["verifier_summary"]["score"] is None
    assert rows["missing"]["verifier_passed"] is None
    assert rows["zero"]["status"] == rows["missing"]["status"] == "failed"


def test_same_receipt_deduplicates_and_changed_same_identity_is_refused(tmp_path):
    app = app_state(tmp_path)
    unit = store_result(app)
    row = storage.load(storage.history_directory(app))[0]
    again = dict(row, created_at="later")
    assert storage.save(storage.history_directory(app), again) == row
    assert len(list(storage.history_directory(app).glob("*.json"))) == 1
    changed = dict(row, status="success")
    with pytest.raises(ValueError, match="different immutable evidence"):
        storage.save(storage.history_directory(app), changed)
    assert storage.load(storage.history_directory(app))[0]["status"] == "failed"


def test_different_stores_never_share_process_history(tmp_path):
    store_result(app_state(tmp_path / "one"))
    assert execution._history_rows(app_state(tmp_path / "two")) == []


def test_atomic_publish_failure_leaves_no_partial_receipt(tmp_path, monkeypatch):
    directory = tmp_path / "history"
    def denied(*args):
        raise PermissionError("injected publish failure")
    monkeypatch.setattr(storage.os, "link", denied)
    with pytest.raises(PermissionError):
        storage.save(directory, {"execution_id": "one", "created_at": "2026-10-25T00:00:00"})
    assert list(directory.iterdir()) == []


def test_persist_failure_cannot_return_a_successful_history_write(tmp_path, monkeypatch):
    from fastapi import HTTPException
    def unavailable(*args):
        raise PermissionError("disk denied")
    monkeypatch.setattr(execution, "save_execution_history", unavailable)
    with pytest.raises(HTTPException) as error:
        store_result(app_state(tmp_path))
    assert error.value.status_code == 503
    assert "do not retry execution" in error.value.detail


def test_corrupt_evidence_is_error_not_empty_success(tmp_path):
    app = app_state(tmp_path)
    store_result(app)
    next(storage.history_directory(app).glob("*.json")).write_text("{", encoding="utf-8")
    with client_for(app_state(tmp_path)) as client:
        response = client.get("/execution/history")
        assert response.status_code == 503


def test_pending_atomic_temp_is_never_exposed_as_a_run(tmp_path):
    directory = tmp_path / "history"
    directory.mkdir()
    (directory / ".pending-interrupted.tmp").write_text("{", encoding="utf-8")
    assert storage.load(directory) == []
