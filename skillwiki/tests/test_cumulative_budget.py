from __future__ import annotations

import json

import pytest

from skillwiki.utils.cumulative_budget import BudgetError, CumulativeBudgetGuard


def test_cumulative_overflow_across_model_and_embedding(tmp_path):
    guard = CumulativeBudgetGuard(
        tmp_path / "budget.jsonl",
        total_limit_cny=10,
        per_kind_limits_cny={"model": 8, "embedding": 9},
    )
    model = guard.reserve("model", 6, {"model": "deepseek-flash"})
    embedding = guard.reserve("embedding", 4, {"model": "text-embedding-v4"})
    guard.settle(model, 6)
    guard.settle(embedding, 4)
    with pytest.raises(BudgetError, match="cumulative"):
        guard.reserve("model", 0.01, {"model": "deepseek-flash"})


def test_explicit_authorized_total_preserves_default_and_existing_rows(tmp_path):
    path = tmp_path / "budget.jsonl"
    default = CumulativeBudgetGuard(path)
    assert default.snapshot()["total_limit_cny"] == "10"
    request = default.reserve("model", 1)
    default.settle(request, 0.5)
    authorized = CumulativeBudgetGuard(path, total_limit_cny=1000)
    assert authorized.snapshot()["total_limit_cny"] == "1000"
    assert authorized.snapshot()["total_charged_cny"] == "0.5"


def test_unknown_usage_is_retained(tmp_path):
    path = tmp_path / "budget.jsonl"
    guard = CumulativeBudgetGuard(path, total_limit_cny=10)
    request_id = guard.reserve("model", 2, {"model": "deepseek-flash"})
    result = guard.settle(request_id)
    assert result["status"] == "unknown_retained"
    snapshot = guard.snapshot()
    assert snapshot["total_charged_cny"] == "2"
    assert snapshot["retained_requests"] == 1
    assert snapshot["unsettled_requests"] == 0
    assert "deepseek-flash" in path.read_text(encoding="utf-8")


def test_restart_reload_preserves_reservation_and_allows_settlement(tmp_path):
    path = tmp_path / "budget.jsonl"
    first = CumulativeBudgetGuard(path, total_limit_cny=10)
    request_id = first.reserve("embedding", 1.25, {"provider": "paperqa"})
    second = CumulativeBudgetGuard(path, total_limit_cny=10)
    assert second.snapshot()["total_charged_cny"] == "1.25"
    second.settle(request_id, 0.5)
    assert CumulativeBudgetGuard(path, total_limit_cny=10).snapshot()["total_charged_cny"] == "0.5"


def test_malformed_ledger_fails_closed(tmp_path):
    path = tmp_path / "budget.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(BudgetError, match="invalid budget ledger"):
        CumulativeBudgetGuard(path).snapshot()


def test_secret_metadata_is_redacted_and_unpriced_call_rejected(tmp_path):
    path = tmp_path / "budget.jsonl"
    guard = CumulativeBudgetGuard(path)
    with pytest.raises(BudgetError, match="finite"):
        guard.reserve("model", None, {"api_key": "do-not-persist"})
    guard.reserve("model", 1, {"api_key": "do-not-persist", "nested": {"token": "also-secret"}})
    content = path.read_text(encoding="utf-8")
    assert "do-not-persist" not in content
    assert "also-secret" not in content
    row = json.loads(content.splitlines()[0])
    assert row["metadata"]["api_key"] == "[REDACTED]"
    assert row["metadata"]["nested"]["token"] == "[REDACTED]"


def test_usage_counts_are_metadata_not_credentials(tmp_path):
    path = tmp_path / "budget.jsonl"
    guard = CumulativeBudgetGuard(path)
    key = guard.reserve("model", 1, {"input_token_bound": 100, "max_tokens": 10})
    guard.settle(key, 0.1, metadata={"usage": {"prompt_tokens": 9, "completion_tokens": 1},
                                     "access_token": "secret"})
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["metadata"]["input_token_bound"] == 100
    assert rows[1]["metadata"]["usage"]["prompt_tokens"] == 9
    assert rows[1]["metadata"]["access_token"] == "[REDACTED]"
    guard.reserve("embedding", 1, {"prompt_tokens": "not-a-count-secret"})
    assert "not-a-count-secret" not in path.read_text()


def test_historical_import_is_restart_idempotent_and_unknown_cost_retained(tmp_path):
    path = tmp_path / "budget.jsonl"
    kwargs = dict(source_id="prior-ledger/request-1", evidence_sha256="a" * 64, unknown=True)
    first = CumulativeBudgetGuard(path).import_historical_charge("model", 6, **kwargs)
    second = CumulativeBudgetGuard(path).import_historical_charge("model", 6, **kwargs)
    assert first["imported"] and not second["imported"]
    guard = CumulativeBudgetGuard(path)
    assert guard.snapshot()["total_charged_cny"] == "6"
    assert guard.snapshot()["retained_requests"] == 1
    guard.reserve("embedding", 4)
    with pytest.raises(BudgetError, match="cumulative"):
        guard.reserve("model", "0.01")
    assert len(path.read_text().splitlines()) == 2


def test_historical_import_rejects_changed_evidence_or_amount(tmp_path):
    guard = CumulativeBudgetGuard(tmp_path / "budget.jsonl")
    guard.import_historical_charge("model", 1, source_id="prior", evidence_sha256="a" * 64)
    for amount, evidence in [(2, "a" * 64), (1, "b" * 64)]:
        with pytest.raises(BudgetError, match="changed"):
            guard.import_historical_charge("model", amount, source_id="prior", evidence_sha256=evidence)
    with pytest.raises(BudgetError, match="SHA256"):
        guard.import_historical_charge("model", 1, source_id="new", evidence_sha256="unverified")


def test_blocked_anomaly_retains_and_prevents_next_reservation(tmp_path):
    guard = CumulativeBudgetGuard(tmp_path / "budget.jsonl")
    key = guard.reserve("model", 2)
    guard.settle(key, blocked=True, metadata={"actual_model": "unpriced"})
    assert guard.snapshot()["blocked"]
    assert guard.snapshot()["total_charged_cny"] == "2"
    with pytest.raises(BudgetError, match="anomaly"):
        guard.reserve("embedding", 1)


def test_anomaly_can_be_reconciled_once_with_hashed_audit(tmp_path):
    guard = CumulativeBudgetGuard(tmp_path / "budget.jsonl")
    key = guard.reserve("model", 2)
    guard.settle(key, blocked=True, metadata={"actual_model": "unpriced"})
    receipt = guard.reconcile_anomaly(key, "0.018340", evidence_sha256="a" * 64,
                                      metadata={"manual_audit": True})
    assert receipt["status"] == "usage_settled"
    assert guard.snapshot()["blocked"] is False
    assert guard.snapshot()["total_charged_cny"] == "0.018340"
    guard.reserve("embedding", 1)
    with pytest.raises(BudgetError, match="requires an anomaly"):
        guard.reconcile_anomaly(key, "0.018340", evidence_sha256="a" * 64)


def test_tampered_historical_charge_fails_closed(tmp_path):
    path = tmp_path / "budget.jsonl"
    guard = CumulativeBudgetGuard(path)
    guard.import_historical_charge("model", 2, source_id="old", evidence_sha256="a" * 64)
    row = json.loads(path.read_text())
    row["charged_cny"] = "0"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(BudgetError, match="historical import accounting"):
        guard.snapshot()
