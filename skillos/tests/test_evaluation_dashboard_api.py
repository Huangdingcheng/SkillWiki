from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.routes import evaluation


def _client(results_dir: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(evaluation, "RESULTS_DIR", results_dir)
    app = FastAPI()
    app.include_router(evaluation.router, prefix="/api/v1")
    return TestClient(app)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_evaluation_dashboard_merges_demo_summary_with_detail_results(tmp_path: Path, monkeypatch) -> None:
    _write_json(
        tmp_path / "latest_summary.json",
        {
            "task_count": 1,
            "mode_totals": {
                "no_skill": {"success": 0, "total": 1, "success_rate": 0.0},
                "raw_prompt": {"success": 0, "total": 1, "success_rate": 0.0},
                "with_skill": {"success": 1, "total": 1, "success_rate": 1.0},
            },
            "rows": [
                {
                    "task_id": "web_fill_login_form",
                    "no_skill": "failed",
                    "raw_prompt": "failed",
                    "with_skill": "success",
                    "winner": "with_skill",
                    "failure_reason": "no_skill: Path not found",
                }
            ],
        },
    )
    _write_json(
        tmp_path / "demo_benchmark_latest.json",
        {
            "generated_at": "2026-05-10T00:00:00Z",
            "results": [
                {
                    "task_id": "web_fill_login_form",
                    "domain": "web",
                    "mode": "with_skill",
                    "latency_ms": 12.5,
                    "verifier_passed": True,
                },
                {
                    "task_id": "web_fill_login_form",
                    "domain": "web",
                    "mode": "no_skill",
                    "latency_ms": 7.0,
                    "verifier_passed": False,
                },
            ],
        },
    )
    _write_json(
        tmp_path / "search_eval_latest.json",
        {
            "benchmark": "skill_search_eval",
            "schema_version": "search_eval.v0.2",
            "query_count": 1,
            "summary": {"lexical": {"top1_hit_rate": 1.0}},
            "comparison": {
                "lexical": {"top1_hit_rate": 1.0},
                "hybrid": {"top1_hit_rate": 1.0},
                "delta": {"top1_hit_rate": 0.0},
            },
            "results": [
                {
                    "query_id": "search_eval_001",
                    "query": "fill form",
                    "expected_skill_ids": ["fill_form"],
                    "lexical": {"top1_hit": True, "best_rank": 1, "results": [{"skill_id": "fill_form"}]},
                    "hybrid": {"top1_hit": True, "best_rank": 1, "results": [{"skill_id": "fill_form"}]},
                }
            ],
        },
    )
    _write_json(
        tmp_path / "llm_eval_latest.json",
        {
            "benchmark": "llm_planner_eval",
            "summary": {
                "task_count": 1,
                "mode_totals": {"llm": {"total": 1, "skipped": 1, "success_rate_excluding_api_failures": 0.0}},
                "rows": [{"task_id": "web_fill_login_form", "llm_status": "skipped", "llm_api_error_type": "missing_api_key"}],
            },
        },
    )
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/v1/evaluation/dashboard")

    assert response.status_code == 200
    data = response.json()
    demo = data["artifacts"]["demo_benchmark"]
    assert data["results_dir_present"] is True
    assert data["warnings"] == []
    assert demo["available"] is True
    assert demo["task_count"] == 1
    assert demo["mode_totals"]["with_skill"]["success_rate"] == 1.0
    assert demo["rows"][0]["domain"] == "web"
    assert demo["rows"][0]["with_skill_latency_ms"] == 12.5
    assert demo["rows"][0]["with_skill_verifier_passed"] is True
    search = data["artifacts"]["search_eval"]
    assert search["rows"][0]["lexical_top_skill"] == "fill_form"
    assert search["rows"][0]["hybrid_top1_hit"] is True
    planner = data["artifacts"]["llm_planner"]
    assert planner["rows"][0]["llm_api_error_type"] == "missing_api_key"


def test_evaluation_dashboard_returns_empty_artifacts_when_files_are_missing(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/v1/evaluation/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert data["results_dir_present"] is True
    assert len(data["warnings"]) == 4
    assert data["artifacts"]["demo_benchmark"]["available"] is False
    assert data["artifacts"]["demo_benchmark"]["rows"] == []
    assert data["artifacts"]["search_eval"]["available"] is False
    assert data["artifacts"]["llm_planner"]["available"] is False
