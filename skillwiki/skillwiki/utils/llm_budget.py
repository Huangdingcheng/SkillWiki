"""Small opt-in, fail-closed budget guard for the approved DeepSeek text route.

The JSONL ledger contains estimates, not provider invoices. Every HTTP attempt
is reserved and fsynced before dispatch. Ambiguous outcomes keep the reservation.
Never delete/reset this ledger between backend runs or use separate ledgers for
clients sharing a budget. Local disk only; cooperating clients must use this guard.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Iterator
from urllib.parse import urlparse
from uuid import uuid4

from ..config.llm_config import LLMConfig


class BudgetError(Exception):
    """Budget accounting cannot safely authorize this request."""


# Approved upper rates: cache-miss input 2 CNY/M, output 8 CNY/M. Cached
# input is deliberately charged at the cache-miss estimate (not an invoice).
_MODEL = "deepseek-flash"
_INPUT_RATE = Decimal("0.000002")
_OUTPUT_RATE = Decimal("0.000008")
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BudgetError("Invalid ledger monetary value") from exc
    if not amount.is_finite() or amount < 0:
        raise BudgetError("Invalid ledger monetary value")
    return amount


def _token_count(value: Any) -> bool:
    return type(value) is int and value >= 0


class PersistentLLMBudget:
    @staticmethod
    def validate_route(config: LLMConfig) -> None:
        """Validate the priced route without opening or creating a ledger."""
        try:
            endpoint = urlparse(config.api_url)
            approved = (endpoint.scheme == "https" and endpoint.hostname == "api.deepseek.com"
                        and endpoint.port in (None, 443) and not endpoint.username
                        and not endpoint.password and not endpoint.query and not endpoint.fragment
                        and endpoint.path.rstrip("/") in ("", "/v1", "/chat/completions",
                                                            "/v1/chat/completions"))
        except ValueError as exc:
            raise BudgetError("Budget price is approved only for the official DeepSeek endpoint") from exc
        if not approved:
            raise BudgetError("Budget price is approved only for the official DeepSeek endpoint")
        if config.model != _MODEL:
            raise BudgetError("Budget has no approved price for the configured model")

    def __init__(self, config: LLMConfig) -> None:
        self.scope = config.budget_scope
        self.limit = _money(config.budget_limit_cny)
        self.total_limit = _money(config.budget_total_limit_cny)
        self.output_cap = config.budget_max_output_tokens
        path = Path(config.budget_ledger_path or "")
        if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
            raise BudgetError("Budget ledger requires an absolute local-disk path")
        self.path = path.resolve()
        self.validate_route(config)
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(os.path.normcase(str(self.path)), threading.Lock())

    def prepare_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate the final payload after overrides, then enforce the output cap."""
        return self.prepare_bounded_payload(payload, self.output_cap)

    @staticmethod
    def prepare_bounded_payload(payload: Dict[str, Any], output_cap: int) -> Dict[str, Any]:
        """Shared policy for persistent and cumulative ledger callers."""
        if type(output_cap) is not int or output_cap < 1:
            raise BudgetError("Budget requires a positive integer configured output cap")
        allowed = {"model", "messages", "temperature", "max_tokens", "stream",
                   "top_p", "frequency_penalty", "presence_penalty", "stop",
                   "response_format", "thinking"}
        if set(payload) - allowed:
            raise BudgetError("Unsupported request parameters under the budget guard")
        if payload.get("model") != _MODEL:
            raise BudgetError("Budget has no approved price for the requested model")
        if payload.get("stream") is not False:
            raise BudgetError("Streaming is not supported under the budget guard")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or any(
            not isinstance(message, dict) or set(message) != {"role", "content"}
            or message.get("role") not in ("system", "user", "assistant")
            or not isinstance(message.get("content"), str) for message in messages
        ):
            raise BudgetError("Budget supports only plain text chat messages")
        output = payload.get("max_tokens")
        if type(output) is not int or output < 1:
            raise BudgetError("Budget requires a positive integer output limit")
        if "thinking" in payload and payload["thinking"] != {"type": "disabled"}:
            raise BudgetError("Only disabled thinking is supported by this budget route")
        if "response_format" in payload and payload["response_format"] not in (
            {"type": "text"}, {"type": "json_object"}
        ):
            raise BudgetError("Unsupported response format under the budget guard")
        prepared = dict(payload)
        prepared["max_tokens"] = min(output, output_cap)
        # Do not depend on a provider's mutable default thinking mode.
        prepared["thinking"] = {"type": "disabled"}
        return prepared

    @staticmethod
    def input_bound(payload: Dict[str, Any]) -> int:
        # A byte-level token cannot exceed one token per UTF-8 byte. Count the
        # entire serialized text request, plus generous special-token framing
        # (1024 base + 256/message). No char/4 heuristic or remote tokenizer.
        try:
            serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            return len(serialized.encode("utf-8")) + 1024 + 256 * len(payload["messages"])
        except (TypeError, ValueError, UnicodeError) as exc:
            raise BudgetError("Cannot conservatively size the request") from exc

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if not self._thread_lock.acquire(blocking=False):
            raise BudgetError("Budget ledger is busy; concurrent model calls are rejected")
        handle = None
        locked = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(str(self.path) + ".lock", "a+b")
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise BudgetError("Budget ledger is busy or locking is unsupported") from exc
            yield
        except OSError as exc:
            raise BudgetError("Budget ledger I/O failed; no further dispatch allowed") from exc
        finally:
            if handle is not None:
                if locked:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self._thread_lock.release()

    def _append(self, event: Dict[str, Any]) -> None:
        row = {"schema": 1, "time_utc": datetime.now(timezone.utc).isoformat(),
               "accounting": "conservative_estimate_cny_not_invoice", **event}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _read(self) -> Dict[str, Dict[str, Any]]:
        reservations: Dict[str, Dict[str, Any]] = {}
        if not self.path.exists():
            return reservations
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.endswith("\n"):
                        raise BudgetError("Incomplete budget ledger; manual reconciliation required")
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get("schema") != 1:
                        raise BudgetError("Unsupported budget ledger schema")
                    key = row["request_id"]
                    if not isinstance(key, str) or not key:
                        raise BudgetError("Invalid budget request identifier")
                    if row["event"] == "reserve":
                        if key in reservations:
                            raise BudgetError("Duplicate budget reservation")
                        if not isinstance(row["scope"], str) or not row["scope"]:
                            raise BudgetError("Invalid budget scope")
                        if (row["requested_model"] != _MODEL
                                or _money(row["input_cny_per_million"]) != 2
                                or _money(row["output_cny_per_million"]) != 8
                                or not _token_count(row["input_token_bound"])
                                or not _token_count(row["output_token_cap"])
                                or _money(row["reserved_cny"]) != (
                                    row["input_token_bound"] * _INPUT_RATE
                                    + row["output_token_cap"] * _OUTPUT_RATE)):
                            raise BudgetError("Invalid budget reservation amount or tariff")
                        if _money(row["scope_limit_cny"]) > _money(row["total_limit_cny"]):
                            raise BudgetError("Invalid budget limits")
                        reservations[key] = {**row, "charge": _money(row["reserved_cny"]),
                                             "settled": False}
                    elif row["event"] == "settle":
                        if key not in reservations or reservations[key]["settled"]:
                            raise BudgetError("Invalid budget settlement")
                        original = reservations[key]
                        charge = _money(row["charged_cny"])
                        if row["scope"] != original["scope"] or type(row["blocked"]) is not bool:
                            raise BudgetError("Invalid budget settlement scope/status")
                        if row["status"] == "usage_settled":
                            prompt, completion = row["prompt_tokens"], row["completion_tokens"]
                            if (row["blocked"] or row["actual_model"] != _MODEL
                                    or not _token_count(prompt) or prompt < 1
                                    or not _token_count(completion)
                                    or prompt > original["input_token_bound"]
                                    or completion > original["output_token_cap"]
                                    or charge != prompt * _INPUT_RATE + completion * _OUTPUT_RATE
                                    or charge != _money(row["usage_estimated_cny"])):
                                raise BudgetError("Invalid usage settlement amount")
                        elif row["status"] == "missing_usage_retained":
                            if row["blocked"] or charge != original["charge"]:
                                raise BudgetError("Unknown usage must retain its reservation")
                        elif row["status"] == "anomaly_retained":
                            if not row["blocked"] or charge < original["charge"]:
                                raise BudgetError("Anomalous usage must retain its reservation")
                        else:
                            raise BudgetError("Unknown budget settlement status")
                        reservations[key]["charge"] = charge
                        reservations[key]["settled"] = True
                        reservations[key]["blocked"] = row.get("blocked", False)
                        reservations[key]["status"] = row["status"]
                    else:
                        raise BudgetError("Unknown budget ledger event")
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise BudgetError("Invalid budget ledger; manual reconciliation required") from exc
        return reservations

    def request(self, payload: Dict[str, Any], dispatch: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        input_tokens = self.input_bound(payload)
        output_tokens = payload["max_tokens"]
        reserved = input_tokens * _INPUT_RATE + output_tokens * _OUTPUT_RATE
        with self._locked():
            rows = self._read()
            if any(row.get("blocked") for row in rows.values()):
                raise BudgetError("Budget ledger has an unreconciled model/usage anomaly")
            scope_total = sum((row["charge"] for row in rows.values()
                               if row["scope"] == self.scope), Decimal(0))
            total = sum((row["charge"] for row in rows.values()), Decimal(0))
            if scope_total + reserved > self.limit or total + reserved > self.total_limit:
                raise BudgetError("LLM budget exhausted: conservative reservation would exceed limit")
            # Limits may be tightened, but silently raising a persistent scope
            # or sprint limit on restart is disallowed.
            if any(self.total_limit > _money(row["total_limit_cny"]) or
                   (row["scope"] == self.scope and self.limit > _money(row["scope_limit_cny"]))
                   for row in rows.values()):
                raise BudgetError("Persistent budget limits cannot be raised automatically")
            key = uuid4().hex
            self._append({"event": "reserve", "request_id": key, "scope": self.scope,
                          "requested_model": payload["model"],
                          "input_token_bound": input_tokens, "output_token_cap": output_tokens,
                          "input_cny_per_million": "2", "output_cny_per_million": "8",
                          "scope_limit_cny": str(self.limit),
                          "total_limit_cny": str(self.total_limit), "reserved_cny": str(reserved)})
            try:
                raw = dispatch()
            except BaseException:
                # This includes network errors, cancellation, and parsing errors.
                # A missing settlement remains a full outstanding reservation.
                raise
            self._settle(key, reserved, input_tokens, output_tokens, raw)
            return raw

    def _settle(self, key: str, reserved: Decimal, input_bound: int, output_cap: int, raw: Any) -> None:
        model = raw.get("model") if isinstance(raw, dict) else None
        usage = raw.get("usage") if isinstance(raw, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        details = usage.get("prompt_tokens_details")
        cached = usage.get("prompt_cache_hit_tokens",
                           details.get("cached_tokens") if isinstance(details, dict) else None)
        uncached = usage.get("prompt_cache_miss_tokens")
        known = _token_count(prompt) and prompt > 0 and _token_count(completion)
        anomaly = model != _MODEL
        if known:
            anomaly = anomaly or prompt > input_bound or completion > output_cap
        if cached is not None:
            anomaly = anomaly or not _token_count(cached) or (known and cached > prompt)
        if uncached is not None:
            anomaly = anomaly or not _token_count(uncached) or (known and uncached > prompt)
        if _token_count(cached) and _token_count(uncached) and known:
            anomaly = anomaly or cached + uncached != prompt
        total = usage.get("total_tokens")
        if total is not None:
            anomaly = anomaly or not _token_count(total) or (known and total != prompt + completion)
        estimate = prompt * _INPUT_RATE + completion * _OUTPUT_RATE if known and model == _MODEL else None
        charge = max(reserved, estimate or Decimal(0)) if anomaly else (estimate if known else reserved)
        self._append({"event": "settle", "request_id": key, "scope": self.scope,
                      "actual_model": model if isinstance(model, str) else None,
                      "prompt_tokens": prompt if _token_count(prompt) else None,
                      "completion_tokens": completion if _token_count(completion) else None,
                      "total_tokens": usage.get("total_tokens") if _token_count(usage.get("total_tokens")) else None,
                      "prompt_cache_hit_tokens": cached if _token_count(cached) else None,
                      "prompt_cache_miss_tokens": uncached if _token_count(uncached) else None,
                      "usage_estimated_cny": str(estimate) if estimate is not None else None,
                      "charged_cny": str(charge), "blocked": bool(anomaly),
                      "status": "anomaly_retained" if anomaly else ("usage_settled" if known else "missing_usage_retained")})
        if anomaly:
            raise BudgetError("Unpriced response model or usage exceeds reserved bounds; ledger blocked")

    def snapshot(self) -> Dict[str, Any]:
        """Read a restart-safe summary without any provider call."""
        with self._locked():
            rows = self._read()
            return {"scope": self.scope, "requests": len(rows),
                    "accounting": "conservative_estimate_cny_not_invoice",
                    "scope_charged_cny": str(sum((row["charge"] for row in rows.values()
                        if row["scope"] == self.scope), Decimal(0))),
                    "total_charged_cny": str(sum((row["charge"] for row in rows.values()), Decimal(0))),
                    "unsettled_requests": sum(not row["settled"] for row in rows.values()),
                    "retained_requests": sum(not row["settled"] or row.get("status") != "usage_settled"
                                             for row in rows.values()),
                    "blocked": any(row.get("blocked") for row in rows.values())}
