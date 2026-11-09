"""Shared, fail-closed budget ledger for model and embedding calls.

This module deliberately has no provider or network dependency.  Callers must
reserve a conservatively priced request before dispatching it and settle the
reservation with measured usage, or retain the reservation when usage is
unknown.  The same JSONL ledger can therefore be shared by independent
clients and survives process restarts.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import hashlib
import math
import os
import re
from pathlib import Path
import threading
from typing import Any, Iterator, Mapping
from uuid import uuid4


class BudgetError(Exception):
    """The ledger cannot safely authorize or account for a request."""


_SENSITIVE_PARTS = (
    "api_key", "apikey", "secret", "password", "passwd", "token",
    "credential", "authorization", "access_key", "private_key",
)
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_TOKEN_COUNT_FIELDS = {
    "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
    "cached_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    "input_token_bound", "output_token_cap", "max_tokens", "budget_max_output_tokens",
}


def _money(value: Any, *, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise BudgetError(f"{field} must be a finite non-negative amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BudgetError(f"{field} must be a finite non-negative amount") from exc
    if not amount.is_finite() or amount < 0:
        raise BudgetError(f"{field} must be a finite non-negative amount")
    return amount


def _safe_metadata(value: Any, key: str = "") -> Any:
    """Return JSON-safe metadata with credential-shaped fields redacted."""
    lowered = key.casefold().replace("-", "_")
    count_field = lowered in _TOKEN_COUNT_FIELDS and type(value) is int and value >= 0
    if not count_field and any(part in lowered for part in _SENSITIVE_PARTS):
        return "[REDACTED]"
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise BudgetError("metadata contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe_metadata(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item, key) for item in value]
    raise BudgetError("metadata must contain only JSON-safe values")


class CumulativeBudgetGuard:
    """A restart-safe cumulative CNY budget for model and embedding calls."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        total_limit_cny: Any = 10,
        per_kind_limits_cny: Mapping[str, Any] | None = None,
    ) -> None:
        ledger = Path(path)
        if not ledger.is_absolute() or str(ledger).startswith(("\\\\", "//")):
            raise BudgetError("budget ledger requires an absolute local-disk path")
        self.path = ledger.resolve()
        self.total_limit = _money(total_limit_cny, field="total_limit_cny")
        if self.total_limit <= 0:
            raise BudgetError("total_limit_cny must be positive")
        self.per_kind_limits = {
            str(kind): _money(limit, field=f"per_kind_limits_cny[{kind}]")
            for kind, limit in (per_kind_limits_cny or {}).items()
        }
        if any(limit > self.total_limit for limit in self.per_kind_limits.values()):
            raise BudgetError("per-kind limit cannot exceed total limit")
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(os.path.normcase(str(self.path)), threading.Lock())

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if not self._thread_lock.acquire(blocking=False):
            raise BudgetError("budget ledger is busy")
        handle = None
        locked = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = Path(str(self.path) + ".lock")
            handle = lock_path.open("a+b")
            if handle.seek(0, os.SEEK_END) == 0:
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
                raise BudgetError("budget ledger lock is unavailable") from exc
            yield
        except OSError as exc:
            raise BudgetError("budget ledger I/O failed; no further dispatch allowed") from exc
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

    def _append(self, row: dict[str, Any]) -> None:
        payload = {
            "schema": 1,
            "time_utc": datetime.now(timezone.utc).isoformat(),
            **row,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _read(self) -> dict[str, dict[str, Any]]:
        reservations: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return reservations
        try:
            with self.path.open(encoding="utf-8", newline="") as handle:
                for line in handle:
                    if not line.endswith("\n"):
                        raise BudgetError("incomplete budget ledger; manual reconciliation required")
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get("schema") != 1:
                        raise BudgetError("unsupported budget ledger schema")
                    event = row.get("event")
                    request_id = row.get("request_id")
                    if not isinstance(request_id, str) or not request_id:
                        raise BudgetError("invalid budget request identifier")
                    if event in ("reserve", "historical_import"):
                        if request_id in reservations:
                            raise BudgetError("duplicate budget reservation")
                        kind = row.get("kind")
                        if not isinstance(kind, str) or not kind.strip():
                            raise BudgetError("invalid budget kind")
                        estimate = _money(row.get("estimate_cny"), field="estimate_cny")
                        metadata = row.get("metadata", {})
                        if not isinstance(metadata, dict):
                            raise BudgetError("invalid budget metadata")
                        reservations[request_id] = {
                            "kind": kind,
                            "estimate": estimate,
                            "charge": estimate,
                            "settled": False,
                            "blocked": False,
                            "reconciled": False,
                        }
                        if event == "historical_import":
                            source = row.get("source_id")
                            evidence = row.get("evidence_sha256")
                            status = row.get("status")
                            if (not isinstance(source, str) or not source.strip()
                                    or not isinstance(evidence, str)
                                    or re.fullmatch(r"[0-9a-f]{64}", evidence) is None
                                    or status not in ("usage_settled", "unknown_retained")):
                                raise BudgetError("invalid historical import evidence")
                            if (request_id != "historical-" + hashlib.sha256(source.encode("utf-8")).hexdigest()
                                    or _money(row.get("charged_cny"), field="charged_cny") != estimate
                                    or (status == "unknown_retained" and estimate <= 0)):
                                raise BudgetError("invalid historical import accounting")
                            if any(prior.get("source_id") == source for key, prior in reservations.items()
                                   if key != request_id):
                                raise BudgetError("duplicate historical import source")
                            reservations[request_id].update({
                                "settled": True, "status": status, "source_id": source,
                                "evidence_sha256": evidence,
                            })
                    elif event == "settle":
                        if request_id not in reservations:
                            raise BudgetError("settlement has no reservation")
                        reservation = reservations[request_id]
                        if reservation["settled"]:
                            raise BudgetError("duplicate budget settlement")
                        if row.get("kind") != reservation["kind"]:
                            raise BudgetError("settlement kind does not match reservation")
                        status = row.get("status")
                        blocked = row.get("blocked", False)
                        if type(blocked) is not bool:
                            raise BudgetError("invalid budget blocked status")
                        if status in ("unknown_retained", "anomaly_retained"):
                            if row.get("actual_cny") is not None:
                                raise BudgetError("unknown settlement cannot include actual usage")
                            charge = reservation["estimate"]
                        elif status == "usage_settled":
                            charge = _money(row.get("actual_cny"), field="actual_cny")
                        else:
                            raise BudgetError("unknown budget settlement status")
                        if blocked != (status == "anomaly_retained"):
                            raise BudgetError("invalid budget anomaly status")
                        if "charged_cny" in row and _money(row["charged_cny"], field="charged_cny") != charge:
                            raise BudgetError("settlement charge does not match retained accounting")
                        reservation.update({"charge": charge, "settled": True, "status": status, "blocked": blocked})
                    elif event == "reconcile":
                        if request_id not in reservations:
                            raise BudgetError("reconciliation has no reservation")
                        reservation = reservations[request_id]
                        if not reservation.get("settled") or reservation.get("status") != "anomaly_retained":
                            raise BudgetError("reconciliation requires an anomaly-retained settlement")
                        if reservation.get("reconciled"):
                            raise BudgetError("duplicate budget reconciliation")
                        if row.get("kind") != reservation["kind"]:
                            raise BudgetError("reconciliation kind does not match reservation")
                        actual = _money(row.get("actual_cny"), field="actual_cny")
                        evidence = row.get("evidence_sha256")
                        if not isinstance(evidence, str) or re.fullmatch(r"[0-9a-f]{64}", evidence) is None:
                            raise BudgetError("reconciliation evidence_sha256 is invalid")
                        if "charged_cny" in row and _money(row["charged_cny"], field="charged_cny") != actual:
                            raise BudgetError("reconciliation charge does not match actual accounting")
                        reservation.update({
                            "charge": actual,
                            "status": "usage_settled",
                            "blocked": False,
                            "reconciled": True,
                        })
                    else:
                        raise BudgetError("unknown budget ledger event")
        except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError, UnicodeError) as exc:
            if isinstance(exc, BudgetError):
                raise
            raise BudgetError("invalid budget ledger; manual reconciliation required") from exc
        self._validate_totals(reservations)
        return reservations

    def _validate_totals(self, reservations: Mapping[str, Mapping[str, Any]]) -> None:
        total = sum((row["charge"] for row in reservations.values()), Decimal(0))
        if total > self.total_limit:
            raise BudgetError("budget ledger exceeds total limit")
        by_kind: dict[str, Decimal] = {}
        for row in reservations.values():
            by_kind[row["kind"]] = by_kind.get(row["kind"], Decimal(0)) + row["charge"]
        for kind, limit in self.per_kind_limits.items():
            if by_kind.get(kind, Decimal(0)) > limit:
                raise BudgetError(f"budget ledger exceeds {kind} limit")

    def reserve(self, kind: str, estimate: Any, metadata: Mapping[str, Any] | None = None) -> str:
        if not isinstance(kind, str) or not kind.strip():
            raise BudgetError("kind must be a non-empty string")
        kind = kind.strip()
        amount = _money(estimate, field="estimate")
        if amount <= 0:
            raise BudgetError("estimate must be positive; unpriced calls are rejected")
        if metadata is None:
            metadata_map: Mapping[str, Any] = {}
        elif isinstance(metadata, Mapping):
            metadata_map = metadata
        else:
            raise BudgetError("metadata must be a mapping")
        safe = _safe_metadata(metadata_map)
        with self._locked():
            reservations = self._read()
            if any(row.get("blocked") for row in reservations.values()):
                raise BudgetError("budget ledger has an unreconciled model/usage anomaly")
            total = sum((row["charge"] for row in reservations.values()), Decimal(0))
            if total + amount > self.total_limit:
                raise BudgetError("budget exhausted: cumulative estimate exceeds total limit")
            kind_total = sum((row["charge"] for row in reservations.values() if row["kind"] == kind), Decimal(0))
            kind_limit = self.per_kind_limits.get(kind)
            if kind_limit is not None and kind_total + amount > kind_limit:
                raise BudgetError(f"budget exhausted: {kind} limit exceeded")
            request_id = uuid4().hex
            self._append({
                "event": "reserve",
                "request_id": request_id,
                "kind": kind,
                "estimate_cny": str(amount),
                "metadata": safe,
            })
            return request_id

    def settle(self, request_id: str, actual: Any = None, *,
               metadata: Mapping[str, Any] | None = None, blocked: bool = False) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id:
            raise BudgetError("request_id must be a non-empty string")
        if type(blocked) is not bool or (blocked and actual is not None):
            raise BudgetError("anomalous usage must retain the full reservation")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise BudgetError("metadata must be a mapping")
        safe = _safe_metadata(metadata or {})
        with self._locked():
            reservations = self._read()
            reservation = reservations.get(request_id)
            if reservation is None:
                raise BudgetError("unknown budget reservation")
            if reservation["settled"]:
                raise BudgetError("budget reservation already settled")
            if actual is None:
                status = "anomaly_retained" if blocked else "unknown_retained"
                charge = reservation["estimate"]
                actual_value = None
            else:
                status = "usage_settled"
                charge = _money(actual, field="actual")
                actual_value = str(charge)
                updated = dict(reservations)
                updated[request_id] = {**reservation, "charge": charge, "settled": True}
                self._validate_totals(updated)
            receipt = {
                "event": "settle",
                "request_id": request_id,
                "kind": reservation["kind"],
                "status": status,
                "actual_cny": actual_value,
                "charged_cny": str(charge),
                "blocked": blocked,
                "metadata": safe,
            }
            self._append(receipt)
            return receipt

    def reconcile_anomaly(self, request_id: str, actual: Any, *,
                          evidence_sha256: str,
                          metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Append an auditable settlement for a previously retained anomaly.

        The original anomaly row remains immutable. Reconciliation is allowed
        only with a measured positive usage value and a SHA-256 receipt for the
        manual audit evidence.
        """
        if not isinstance(request_id, str) or not request_id:
            raise BudgetError("request_id must be a non-empty string")
        charge = _money(actual, field="actual")
        if charge <= 0:
            raise BudgetError("reconciled actual usage must be positive")
        if not isinstance(evidence_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", evidence_sha256) is None:
            raise BudgetError("reconciliation evidence_sha256 is invalid")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise BudgetError("metadata must be a mapping")
        safe = _safe_metadata(metadata or {})
        with self._locked():
            reservations = self._read()
            reservation = reservations.get(request_id)
            if reservation is None:
                raise BudgetError("unknown budget reservation")
            if not reservation.get("settled") or reservation.get("status") != "anomaly_retained":
                raise BudgetError("reconciliation requires an anomaly-retained settlement")
            if reservation.get("reconciled"):
                raise BudgetError("budget reservation already reconciled")
            updated = dict(reservations)
            updated[request_id] = {**reservation, "charge": charge, "blocked": False, "status": "usage_settled", "reconciled": True}
            self._validate_totals(updated)
            receipt = {
                "event": "reconcile",
                "request_id": request_id,
                "kind": reservation["kind"],
                "status": "usage_settled",
                "actual_cny": str(charge),
                "charged_cny": str(charge),
                "evidence_sha256": evidence_sha256.lower(),
                "metadata": safe,
            }
            self._append(receipt)
            return receipt

    def import_historical_charge(self, kind: str, amount: Any, *, source_id: str,
                                 evidence_sha256: str, unknown: bool = False,
                                 metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Append one immutable historical charge; exact re-imports are a no-op.

        ``amount`` is measured cost or a conservative retained upper bound when
        ``unknown=True``. A changed source requires manual reconciliation, never
        a reset or replacement of an earlier charge.
        """
        if not isinstance(kind, str) or not kind.strip():
            raise BudgetError("kind must be a non-empty string")
        if not isinstance(source_id, str) or not source_id.strip():
            raise BudgetError("historical source_id must be non-empty")
        if not isinstance(evidence_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", evidence_sha256) is None:
            raise BudgetError("historical evidence_sha256 must be an immutable SHA256 hash")
        if type(unknown) is not bool or (metadata is not None and not isinstance(metadata, Mapping)):
            raise BudgetError("invalid historical import metadata/status")
        charge = _money(amount, field="historical amount")
        if unknown and charge <= 0:
            raise BudgetError("unknown historical cost requires a positive retained bound")
        kind, source_id, evidence_sha256 = kind.strip(), source_id.strip(), evidence_sha256.lower()
        status = "unknown_retained" if unknown else "usage_settled"
        safe = _safe_metadata(metadata or {})
        request_id = "historical-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()
        with self._locked():
            reservations = self._read()
            previous = reservations.get(request_id)
            if previous is not None:
                if (previous.get("source_id") != source_id or previous.get("evidence_sha256") != evidence_sha256
                        or previous["kind"] != kind or previous["charge"] != charge or previous.get("status") != status):
                    raise BudgetError("historical source changed; manual reconciliation required")
                return {"request_id": request_id, "kind": kind, "status": status,
                        "charged_cny": str(charge), "imported": False}
            # The same evidence can contain multiple requests. The source id must
            # identify the immutable request/summary within that evidence.
            proposed = {**reservations, request_id: {"kind": kind, "charge": charge}}
            self._validate_totals(proposed)
            self._append({"event": "historical_import", "request_id": request_id,
                          "kind": kind, "estimate_cny": str(charge), "charged_cny": str(charge),
                          "source_id": source_id, "evidence_sha256": evidence_sha256,
                          "status": status, "metadata": safe})
            return {"request_id": request_id, "kind": kind, "status": status,
                    "charged_cny": str(charge), "imported": True}

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            reservations = self._read()
            by_kind: dict[str, Decimal] = {}
            for row in reservations.values():
                by_kind[row["kind"]] = by_kind.get(row["kind"], Decimal(0)) + row["charge"]
            return {
                "total_limit_cny": str(self.total_limit),
                "total_charged_cny": str(sum((row["charge"] for row in reservations.values()), Decimal(0))),
                "remaining_cny": str(self.total_limit - sum((row["charge"] for row in reservations.values()), Decimal(0))),
                "requests": len(reservations),
                "unsettled_requests": sum(not row["settled"] for row in reservations.values()),
                "retained_requests": sum(not row["settled"] or row.get("status") in ("unknown_retained", "anomaly_retained") for row in reservations.values()),
                "by_kind_cny": {kind: str(amount) for kind, amount in sorted(by_kind.items())},
                "blocked": any(row.get("blocked") for row in reservations.values()),
            }
