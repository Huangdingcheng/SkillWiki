from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from skillwiki.config.llm_config import LLMConfig
from skillwiki.utils.llm_client import LLMBudgetError, LLMClient, LLMTimeoutError, Message


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("httpx.Client", Mock(side_effect=AssertionError("No HTTP allowed in cumulative budget tests")))


def _config(path) -> LLMConfig:
    return LLMConfig(
        api_key="test",
        api_url="https://api.deepseek.com",
        model="deepseek-flash",
        max_tokens=16,
        retry_count=3,
        cumulative_budget_ledger_path=str(path),
        cumulative_budget_total_limit_cny=10,
    )


def test_cumulative_model_call_uses_shared_ledger_and_server_usage(monkeypatch, tmp_path) -> None:
    client = LLMClient(_config(tmp_path / "cumulative.jsonl"))
    calls = []
    monkeypatch.setattr(
        client,
        "_do_request",
        lambda payload: calls.append(payload) or {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        },
    )
    response = client.chat([Message.user("hello")])
    assert response.model == "deepseek-flash"
    assert len(calls) == 1
    snapshot = client.budget_snapshot()
    assert float(snapshot["total_charged_cny"]) > 0
    assert snapshot["by_kind_cny"]["model"] == snapshot["total_charged_cny"]
    receipt = response.raw["budget_receipt"]
    assert receipt == client.last_budget_receipt
    assert receipt["metadata"]["actual_model"] == "deepseek-flash"
    assert receipt["metadata"]["usage"]["prompt_tokens"] == 4


def test_cumulative_model_call_retains_unknown_usage(monkeypatch, tmp_path) -> None:
    ledger = tmp_path / "cumulative.jsonl"
    client = LLMClient(_config(ledger))
    monkeypatch.setattr(
        client,
        "_do_request",
        lambda payload: {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        },
    )
    client.chat([Message.user("hello")])
    snapshot = client.budget_snapshot()
    assert snapshot["retained_requests"] == 1
    assert snapshot["unsettled_requests"] == 0


@pytest.mark.parametrize("api_url", ["https://reseller.example/v1", "https://api.deepseek.com.evil.test",
                                      "http://api.deepseek.com", "https://api.deepseek.com:444",
                                      "https://api.deepseek.com/v1?x=1", "https://api.deepseek.com/beta"])
def test_cumulative_unapproved_endpoint_rejected_before_dispatch(tmp_path, api_url):
    cfg = _config(tmp_path / "budget.jsonl").model_copy(update={"api_url": api_url})
    with pytest.raises(LLMBudgetError, match="approved"):
        LLMClient(cfg)


@pytest.mark.parametrize("extra", [{"n": 2}, {"tools": []}, {"max_completion_tokens": 99999},
                                  {"thinking": {"type": "enabled"}}, {"stream": True},
                                  {"model": "deepseek-chat"},
                                  {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
                                  {"max_tokens": 0}])
def test_cumulative_payload_rejected_before_reservation(tmp_path, extra):
    path = tmp_path / "budget.jsonl"
    client = LLMClient(_config(path))
    client._do_request = Mock()
    with pytest.raises(LLMBudgetError):
        client.chat([Message.user("hello")], extra=extra)
    client._do_request.assert_not_called()
    assert not path.exists()


def test_cumulative_payload_cap_and_disabled_thinking(tmp_path):
    cfg = _config(tmp_path / "budget.jsonl").model_copy(update={"budget_max_output_tokens": 7})
    client = LLMClient(cfg)
    client._do_request = Mock(return_value={"model": "deepseek-flash", "choices": [{"message": {"content": "ok"}}]})
    client.chat([Message.user("hello")], max_tokens=99999)
    payload = client._do_request.call_args.args[0]
    assert payload["max_tokens"] == 7
    assert payload["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("updates", [
    {"model": "unpriced-provider-model"},
    {"usage": {"prompt_tokens": 10, "completion_tokens": 9999}},
    {"usage": {"prompt_tokens": 999999, "completion_tokens": 1}},
    {"usage": {"prompt_tokens": -1, "completion_tokens": 1}},
    {"usage": {"prompt_tokens": True, "completion_tokens": 1}},
    {"usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 1}},
    {"usage": {"prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_hit_tokens": 11}},
    {"usage": {"prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_hit_tokens": 5, "prompt_cache_miss_tokens": 4}},
])
def test_cumulative_anomaly_retains_full_and_blocks_restart(tmp_path, updates):
    path = tmp_path / "budget.jsonl"
    cfg = _config(path)
    client = LLMClient(cfg)
    raw = {"model": "deepseek-flash", "usage": {"prompt_tokens": 10, "completion_tokens": 1},
           "choices": [{"message": {"content": "ok"}}], **updates}
    client._do_request = Mock(return_value=raw)
    with pytest.raises(LLMBudgetError, match="Unpriced"):
        client.chat([Message.user("hello")])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[1]["charged_cny"] == rows[0]["estimate_cny"]
    assert client.last_budget_receipt["status"] == "anomaly_retained"
    restarted = LLMClient(cfg)
    restarted._do_request = Mock()
    with pytest.raises(LLMBudgetError, match="anomaly"):
        restarted.chat([Message.user("hello")])
    restarted._do_request.assert_not_called()


def test_cumulative_failure_has_one_attempt_and_retains_across_restart(tmp_path, monkeypatch):
    path = tmp_path / "budget.jsonl"
    cfg = _config(path)
    client = LLMClient(cfg)
    client._do_request = Mock(side_effect=LLMTimeoutError("unknown"))
    sleep = Mock()
    monkeypatch.setattr("skillwiki.utils.llm_client.time.sleep", sleep)
    with pytest.raises(LLMTimeoutError):
        client.chat([Message.user("hello")])
    assert client._do_request.call_count == 1
    sleep.assert_not_called()
    assert client.last_budget_receipt["status"] == "unknown_retained"
    assert LLMClient(cfg).budget_snapshot()["retained_requests"] == 1
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["metadata"]["retry_count"] == 0
    assert Decimal(rows[1]["charged_cny"]) == Decimal(rows[0]["estimate_cny"])
