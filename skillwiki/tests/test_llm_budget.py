"""Budget contract tests: all dispatches are mocked; no paid/provider calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from skillwiki.config import AgentLLMConfig, LLMConfig
from skillwiki.utils.llm_client import (LLMBudgetError, LLMClient, LLMRateLimitError,
                                       LLMTimeoutError, Message)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("httpx.Client", Mock(side_effect=AssertionError("No HTTP allowed in budget tests")))


def config(tmp_path: Path, **updates) -> LLMConfig:
    data = dict(api_key="test-not-a-real-key", api_url="https://api.deepseek.com",
                model="deepseek-flash", max_tokens=1000, retry_count=0, retry_delay=0,
                budget_limit_cny=1, budget_total_limit_cny=10,
                budget_scope="m1", budget_ledger_path=str(tmp_path / "budget.jsonl"),
                budget_max_output_tokens=128)
    data.update(updates)
    return LLMConfig(**data)


def response(**updates):
    raw = dict(model="deepseek-flash",
               choices=[dict(message=dict(content="answer"), finish_reason="stop")],
               usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                          prompt_cache_hit_tokens=6, prompt_cache_miss_tokens=4))
    raw.update(updates)
    return raw


def ledger(tmp_path):
    return [json.loads(line) for line in (tmp_path / "budget.jsonl").read_text().splitlines()]


def test_exhausted_budget_never_dispatches(tmp_path):
    client = LLMClient(config(tmp_path, budget_limit_cny=0))
    client._do_request = Mock(return_value=response())
    with pytest.raises(LLMBudgetError, match="exhausted"):
        client.chat([Message.user("hello")])
    client._do_request.assert_not_called()
    assert not (tmp_path / "budget.jsonl").exists()


@pytest.mark.parametrize("failure", [LLMTimeoutError("unknown server outcome"), ValueError("bad JSON")])
def test_failure_retains_reservation_across_restart(tmp_path, failure):
    cfg = config(tmp_path)
    client = LLMClient(cfg)
    client._do_request = Mock(side_effect=failure)
    with pytest.raises(type(failure)):
        client.chat([Message.user("hello")])
    rows = ledger(tmp_path)
    assert len(rows) == 1 and rows[0]["event"] == "reserve"
    restarted = LLMClient(cfg).budget_snapshot()
    assert restarted["unsettled_requests"] == 1
    assert restarted["scope_charged_cny"] == rows[0]["reserved_cny"]


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 10},
                                    {"prompt_tokens": 0, "completion_tokens": 0}])
def test_missing_or_incomplete_usage_keeps_full_reservation(tmp_path, usage):
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response(usage=usage))
    assert client.chat([Message.user("hello")]).content == "answer"
    rows = ledger(tmp_path)
    assert rows[1]["status"] == "missing_usage_retained"
    assert rows[1]["charged_cny"] == rows[0]["reserved_cny"]
    assert rows[1]["usage_estimated_cny"] is None


def test_usage_settles_and_records_cached_fields_and_returned_model(tmp_path):
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response())
    client.chat([Message.user("hello")])
    reserve, settle = ledger(tmp_path)
    assert settle["actual_model"] == "deepseek-flash"
    assert settle["prompt_tokens"] == 10 and settle["completion_tokens"] == 5
    assert settle["prompt_cache_hit_tokens"] == 6 and settle["prompt_cache_miss_tokens"] == 4
    assert Decimal(settle["charged_cny"]) == Decimal("0.000060")
    assert Decimal(settle["charged_cny"]) < Decimal(reserve["reserved_cny"])
    assert settle["accounting"] == "conservative_estimate_cny_not_invoice"
    assert "hello" not in json.dumps([reserve, settle])
    assert "test-not-a-real-key" not in json.dumps([reserve, settle])


def test_retry_reserves_each_attempt_and_zero_retry_does_not_sleep(tmp_path, monkeypatch):
    client = LLMClient(config(tmp_path, retry_count=1))
    client._do_request = Mock(side_effect=[LLMTimeoutError("timeout"), response()])
    client.chat([Message.user("hello")])
    rows = ledger(tmp_path)
    assert [row["event"] for row in rows] == ["reserve", "reserve", "settle"]
    assert Decimal(client.budget_snapshot()["total_charged_cny"]) == (
        Decimal(rows[0]["reserved_cny"]) + Decimal(rows[2]["charged_cny"]))
    no_retry = LLMClient(config(tmp_path))
    no_retry._do_request = Mock(side_effect=LLMRateLimitError("limited"))
    sleep = Mock()
    monkeypatch.setattr("skillwiki.utils.llm_client.time.sleep", sleep)
    with pytest.raises(LLMRateLimitError):
        no_retry.chat([Message.user("hello")])
    assert no_retry._do_request.call_count == 1
    sleep.assert_not_called()


def test_retry_does_not_dispatch_when_first_uncertain_attempt_exhausts_limit(tmp_path):
    client = LLMClient(config(tmp_path, retry_count=1, budget_limit_cny=0.006))
    client._do_request = Mock(side_effect=LLMTimeoutError("timeout"))
    with pytest.raises(LLMBudgetError, match="exhausted"):
        client.chat([Message.user("hello")])
    assert client._do_request.call_count == 1


def test_output_override_is_capped_and_unicode_uses_byte_bound(tmp_path):
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response())
    client.chat([Message.user("你好🌍" * 100)], max_tokens=99999, extra={"max_tokens": 88888})
    sent = client._do_request.call_args.args[0]
    assert sent["max_tokens"] == 128
    assert sent["thinking"] == {"type": "disabled"}
    assert ledger(tmp_path)[0]["input_token_bound"] >= len(("你好🌍" * 100).encode()) + 1280


@pytest.mark.parametrize("extra", [{"model": "unpriced-model"}, {"stream": True},
                                    {"n": 2}, {"max_completion_tokens": 999999},
                                    {"tools": []}, {"thinking": {"type": "enabled"}}])
def test_unsafe_extra_rejected_before_dispatch(tmp_path, extra):
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response())
    with pytest.raises(LLMBudgetError):
        client.chat([Message.user("hello")], extra=extra)
    client._do_request.assert_not_called()


@pytest.mark.parametrize("updates", [{"model": "deepseek-chat"},
                                      {"api_url": "https://reseller.example/v1"}])
def test_unknown_pricing_rejected_at_initialization(tmp_path, updates):
    with pytest.raises(LLMBudgetError, match="approved"):
        LLMClient(config(tmp_path, **updates))


def test_returned_unpriced_model_is_recorded_and_blocks_further_calls(tmp_path):
    cfg = config(tmp_path)
    client = LLMClient(cfg)
    client._do_request = Mock(return_value=response(model="unknown-priced-model"))
    with pytest.raises(LLMBudgetError, match="Unpriced"):
        client.chat([Message.user("hello")])
    rows = ledger(tmp_path)
    assert rows[1]["actual_model"] == "unknown-priced-model"
    assert rows[1]["charged_cny"] == rows[0]["reserved_cny"]
    restarted = LLMClient(cfg)
    restarted._do_request = Mock(return_value=response())
    with pytest.raises(LLMBudgetError, match="anomaly"):
        restarted.chat([Message.user("hello")])
    restarted._do_request.assert_not_called()


def test_usage_over_bound_blocks_ledger(tmp_path):
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response(usage={"prompt_tokens": 10, "completion_tokens": 9999}))
    with pytest.raises(LLMBudgetError, match="bounds"):
        client.chat([Message.user("hello")])
    assert client.budget_snapshot()["blocked"]
    assert Decimal(ledger(tmp_path)[1]["charged_cny"]) > Decimal(ledger(tmp_path)[0]["reserved_cny"])


def test_stream_cannot_bypass_with_extra(tmp_path):
    client = LLMClient(config(tmp_path))
    client._stream_request = Mock()
    with pytest.raises(LLMBudgetError, match="Streaming"):
        list(client.stream_chat([Message.user("hello")], extra={"stream": False}))
    client._stream_request.assert_not_called()


def test_shared_ledger_rejects_concurrent_attempt(tmp_path):
    cfg = config(tmp_path)
    client = LLMClient(cfg)
    other = LLMClient(cfg)
    other._do_request = Mock(return_value=response())
    with client._budget._locked():
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(other.chat, [Message.user("hello")])
            with pytest.raises(LLMBudgetError, match="concurrent"):
                future.result()
    other._do_request.assert_not_called()


def test_separate_process_lock_rejects_dispatch_and_releases_on_exit(tmp_path):
    cfg = config(tmp_path)
    client = LLMClient(cfg)
    # This child has a fresh process-local lock registry, so rejection proves
    # the OS file lock is effective. HTTP is forbidden in the child as well.
    script = (
        "import sys; from skillwiki.config import LLMConfig; "
        "from skillwiki.utils.llm_client import LLMClient, LLMBudgetError, Message; "
        "c=LLMClient(LLMConfig.model_validate_json(sys.argv[1])); "
        "c._do_request=lambda p: (_ for _ in ()).throw(AssertionError('HTTP forbidden')); \n"
        "try: c.chat([Message.user('hello')])\n"
        "except LLMBudgetError as e: print(str(e))\n"
        "else: raise AssertionError('concurrency was not blocked')\n"
    )
    with client._budget._locked():
        result = subprocess.run([sys.executable, "-c", script, cfg.model_dump_json()],
                                capture_output=True, text=True, timeout=15, check=True)
    assert "busy" in result.stdout
    client._do_request = Mock(return_value=response())
    assert client.chat([Message.user("after unlock")]).content == "answer"


def test_total_budget_shared_across_scopes_and_restarts(tmp_path):
    first = LLMClient(config(tmp_path, budget_limit_cny=0.006, budget_total_limit_cny=0.006))
    first._do_request = Mock(return_value=response(usage=None))
    first.chat([Message.user("hello")])
    second = LLMClient(config(tmp_path, budget_scope="m2", budget_limit_cny=0.006,
                              budget_total_limit_cny=0.006))
    second._do_request = Mock(return_value=response())
    with pytest.raises(LLMBudgetError, match="exhausted"):
        second.chat([Message.user("hello")])
    second._do_request.assert_not_called()
    assert second.budget_snapshot()["scope_charged_cny"] == "0"
    assert Decimal(second.budget_snapshot()["total_charged_cny"]) > 0


def test_corrupt_ledger_fails_closed(tmp_path):
    (tmp_path / "budget.jsonl").write_text('{"event":')
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response())
    with pytest.raises(LLMBudgetError, match="Incomplete"):
        client.chat([Message.user("hello")])
    client._do_request.assert_not_called()


@pytest.mark.parametrize("replacement", ["[]\n", "null\n", "{}\n"])
def test_nonrecord_ledger_fails_closed(tmp_path, replacement):
    (tmp_path / "budget.jsonl").write_text(replacement)
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response())
    with pytest.raises(LLMBudgetError):
        client.chat([Message.user("hello")])
    client._do_request.assert_not_called()


def test_torn_or_invalid_settlement_cannot_reset_reservation(tmp_path):
    client = LLMClient(config(tmp_path))
    client._do_request = Mock(return_value=response(usage=None))
    client.chat([Message.user("hello")])
    rows = ledger(tmp_path)
    rows[1]["charged_cny"] = "0"
    (tmp_path / "budget.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(LLMBudgetError, match="retain"):
        client.chat([Message.user("hello")])
    assert client._do_request.call_count == 1


def test_persistent_limits_cannot_silently_increase(tmp_path):
    first = LLMClient(config(tmp_path, budget_limit_cny=0.1))
    first._do_request = Mock(return_value=response())
    first.chat([Message.user("hello")])
    second = LLMClient(config(tmp_path, budget_limit_cny=1))
    second._do_request = Mock(return_value=response())
    with pytest.raises(LLMBudgetError, match="raised"):
        second.chat([Message.user("hello")])
    second._do_request.assert_not_called()


def test_budget_is_inherited_by_agent_config(tmp_path):
    cfg = config(tmp_path)
    merged = AgentLLMConfig(agent_type="planner", max_tokens=9000).merge_with_global(cfg)
    assert merged.budget_limit_cny == cfg.budget_limit_cny
    assert merged.budget_ledger_path == cfg.budget_ledger_path
    assert merged.budget_max_output_tokens == 128


def test_partial_budget_config_rejected():
    with pytest.raises(ValueError, match="Budget requires"):
        LLMConfig(api_key="test", budget_limit_cny=1)
