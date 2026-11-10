"""Reference-only applicability evidence and a deterministic bounded planner.

This module records evidence and emits plans. It never decides release,
rollback, oracle, verifier, or skill mutation outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

APPLICABILITY_STATUSES = frozenset({"applicable", "stale", "unknown", "invalid"})
PRIMARY_ACTIONS = frozenset({"probe", "retest", "defer"})
PLANNER_ACTIONS = frozenset({"probe", "retest", "repair", "defer", "rollback"})
PLANNER_MODES = frozenset({"primary", "non_primary"})
EXECUTION_STATUSES = frozenset({"failure", "timeout", "reject", "defer", "blocked", "untested", "success"})
LEDGER_MODEL = "per_record_single_slot_v1"
_LEAK_KEYS = frozenset({
    "truth", "truth_affected", "truth_labels", "hidden_probe_edges",
    "oracle", "oracle_truth", "oracle_labels", "oracle_outcome",
    "verifier", "verifier_truth", "verifier_labels", "verifier_outcome",
    "holdout", "holdout_labels", "holdout_outcome", "heldout",
})
_REF_RE = re.compile(r"^(?P<ref>[^#\s]+)#(?P<digest>sha256:[0-9a-fA-F]{64})$")
_DRIFT_FIELDS = frozenset({
    "source_commit", "version", "license", "environment_digest",
    "dependency_manifest_digest", "validator_id_hash",
})


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field_name} must be a single-line string")
    return value.strip()


def _digest(value: Any, field_name: str) -> str:
    value = _text(value, field_name)
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value.lower()


def _tuple_text(values: Iterable[Any], field_name: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence")
    result = tuple(_text(item, f"{field_name}[]") for item in values)
    if required and not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _reference(value: Any, field_name: str) -> str:
    value = _text(value, field_name)
    if not _REF_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be ref#sha256:<64 hex>")
    return value


def _reference_tuple(values: Iterable[Any], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence")
    result = tuple(_reference(item, f"{field_name}[]") for item in values)
    if not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


@dataclass(frozen=True)
class CommonInputEnvelope:
    """Frozen D-COMP-154 common-input identity carried by every record."""

    fixture_manifest_digest: str
    task_digest: str
    task_family: str
    prompt_digest: str
    model: str
    provider: str
    seeds: tuple[int, ...]
    retry_policy: str
    temperature: float
    max_tokens: int
    environment_image_digest: str
    dependency_manifest_digest: str
    oracle_digest: str
    verifier_digest: str
    holdout_digest: str

    def __post_init__(self) -> None:
        for name in (
            "fixture_manifest_digest", "task_digest", "prompt_digest",
            "environment_image_digest", "dependency_manifest_digest",
            "oracle_digest", "verifier_digest", "holdout_digest",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), f"common_input.{name}"))
        for name in ("task_family", "model", "provider", "retry_policy"):
            object.__setattr__(self, name, _text(getattr(self, name), f"common_input.{name}"))
        if type(self.temperature) is not float or self.temperature != 0.0:
            raise ValueError("common_input.temperature must be frozen at 0.0")
        if type(self.max_tokens) is not int or self.max_tokens != 8192:
            raise ValueError("common_input.max_tokens must be frozen at 8192")
        if isinstance(self.seeds, (str, bytes)):
            raise ValueError("common_input.seeds must be a sequence")
        seeds = tuple(self.seeds)
        if not seeds or any(type(seed) is not int or seed < 0 for seed in seeds):
            raise ValueError("common_input.seeds must contain non-negative integers")
        object.__setattr__(self, "seeds", seeds)

    @classmethod
    def from_value(cls, value: "CommonInputEnvelope | Mapping[str, Any]") -> "CommonInputEnvelope":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("common_input_envelope must be a CommonInputEnvelope or mapping")
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return {name: list(value) if name == "seeds" else value for name, value in {
            "fixture_manifest_digest": self.fixture_manifest_digest, "task_digest": self.task_digest,
            "task_family": self.task_family, "prompt_digest": self.prompt_digest, "model": self.model,
            "provider": self.provider, "seeds": self.seeds, "retry_policy": self.retry_policy,
            "temperature": self.temperature, "max_tokens": self.max_tokens,
            "environment_image_digest": self.environment_image_digest,
            "dependency_manifest_digest": self.dependency_manifest_digest,
            "oracle_digest": self.oracle_digest, "verifier_digest": self.verifier_digest,
            "holdout_digest": self.holdout_digest,
        }.items()}

    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StatusLedger:
    """Per-record, single-slot ledger.

    One sidecar record represents exactly one planned slot. Its slot must have
    exactly one classified outcome; ``untested`` and ``blocked`` remain in the
    denominator rather than being silently dropped.
    """

    planned: int = 1
    executed: int = 0
    terminal: int = 0
    scored: int = 0
    failed: int = 0
    timeout: int = 0
    reject: int = 0
    defer: int = 0
    blocked: int = 0
    untested: int = 0

    def __post_init__(self) -> None:
        values = self.to_dict()
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise ValueError("status ledger values must be non-negative integers")
        if self.planned != 1:
            raise ValueError("per-record ledger requires planned=1")
        outcomes = (self.failed, self.timeout, self.reject, self.defer, self.blocked, self.untested, self.scored)
        if sum(outcomes) != self.planned:
            raise ValueError("each planned slot must have exactly one classified outcome")
        terminal_outcomes = self.failed + self.timeout + self.reject + self.scored
        if self.executed != terminal_outcomes or self.terminal != terminal_outcomes:
            raise ValueError("executed and terminal must equal classified terminal outcomes")

    @property
    def failure(self) -> int:
        """Compatibility alias; serialized ledgers use the protocol name ``failed``."""
        return self.failed

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in (
            "planned", "executed", "terminal", "scored", "failed", "timeout",
            "reject", "defer", "blocked", "untested",
        )}

    @classmethod
    def from_value(cls, value: "StatusLedger | Mapping[str, Any]") -> "StatusLedger":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("status_ledger must be a StatusLedger or mapping")
        values = dict(value)
        if "failure" in values:
            if "failed" in values and values["failed"] != values["failure"]:
                raise ValueError("failure and failed ledger counts disagree")
            values["failed"] = values.pop("failure")
        return cls(**values)


@dataclass(frozen=True)
class ImpactEdge:
    source: str
    relation: str
    target: str
    evidence_ref: str

    def __post_init__(self) -> None:
        for name in ("source", "relation", "target"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "evidence_ref", _reference(self.evidence_ref, "evidence_ref"))

    def to_dict(self) -> dict[str, str]:
        return {"from": self.source, "relation": self.relation, "to": self.target, "evidence_ref": self.evidence_ref}


@dataclass(frozen=True)
class EvidenceApplicabilityRecord:
    record_id: str
    receipt_ref: str
    source_identity: Mapping[str, str]
    package_digest: str
    environment_fingerprint: str
    dependency_manifest_digest: str
    validator_identity: Mapping[str, str]
    task_families: tuple[str, ...]
    skill_ids: tuple[str, ...]
    impact_edges: tuple[ImpactEdge, ...]
    applicability_status: str
    unknown_reason: str | None
    evidence_refs: tuple[str, ...]
    trace_refs: tuple[str, ...]
    created_at: str
    planner_action: str = "defer"
    schema_version: str = "skillwiki.evidence_applicability.v2"
    common_input_envelope: CommonInputEnvelope | Mapping[str, Any] | None = None
    drift_fields: tuple[str, ...] = ()
    execution_status: str = "untested"
    status_ledger: StatusLedger | Mapping[str, Any] = field(default_factory=lambda: StatusLedger(untested=1))

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _text(self.record_id, "record_id"))
        object.__setattr__(self, "receipt_ref", _reference(self.receipt_ref, "receipt_ref"))
        object.__setattr__(self, "package_digest", _digest(self.package_digest, "package_digest"))
        object.__setattr__(self, "environment_fingerprint", _text(self.environment_fingerprint, "environment_fingerprint"))
        object.__setattr__(self, "dependency_manifest_digest", _digest(self.dependency_manifest_digest, "dependency_manifest_digest"))
        object.__setattr__(self, "task_families", _tuple_text(self.task_families, "task_families", required=True))
        object.__setattr__(self, "skill_ids", _tuple_text(self.skill_ids, "skill_ids"))
        object.__setattr__(self, "evidence_refs", _reference_tuple(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "trace_refs", _reference_tuple(self.trace_refs, "trace_refs"))
        object.__setattr__(self, "created_at", _text(self.created_at, "created_at"))
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version"))
        if self.applicability_status not in APPLICABILITY_STATUSES:
            raise ValueError(f"unsupported applicability_status: {self.applicability_status}")
        if self.applicability_status in {"unknown", "stale", "invalid"} and not self.unknown_reason:
            raise ValueError("unknown/stale/invalid applicability requires unknown_reason")
        if self.applicability_status == "applicable" and self.unknown_reason:
            raise ValueError("applicable record cannot carry unknown_reason")
        if self.planner_action not in PLANNER_ACTIONS:
            raise ValueError(f"unsupported planner_action: {self.planner_action}")
        if self.execution_status not in EXECUTION_STATUSES:
            raise ValueError(f"unsupported execution_status: {self.execution_status}")
        if self.common_input_envelope is None:
            raise ValueError("common_input_envelope is required")
        object.__setattr__(self, "common_input_envelope", CommonInputEnvelope.from_value(self.common_input_envelope))
        drift = _tuple_text(self.drift_fields, "drift_fields")
        unknown_drift = sorted(set(drift) - _DRIFT_FIELDS)
        if unknown_drift:
            raise ValueError(f"unsupported drift_fields: {unknown_drift}")
        object.__setattr__(self, "drift_fields", tuple(sorted(set(drift))))
        ledger = StatusLedger.from_value(self.status_ledger)
        status_slot = "failed" if self.execution_status == "failure" else self.execution_status
        if self.execution_status == "success":
            if ledger.scored < 1 or ledger.planned < ledger.scored or ledger.executed < ledger.scored or ledger.terminal < ledger.scored:
                raise ValueError("success record requires a planned, executed, terminal, scored ledger")
        elif getattr(ledger, status_slot, 0) < 1:
            raise ValueError(f"{self.execution_status} record requires a matching status ledger count")
        object.__setattr__(self, "status_ledger", ledger)
        source = dict(self.source_identity)
        for key in ("repository", "commit", "version", "license"):
            source[key] = _text(source.get(key), f"source_identity.{key}")
        object.__setattr__(self, "source_identity", source)
        validator = dict(self.validator_identity)
        for key in ("id", "version_or_hash"):
            validator[key] = _text(validator.get(key), f"validator_identity.{key}")
        object.__setattr__(self, "validator_identity", validator)
        edges = tuple(edge if isinstance(edge, ImpactEdge) else ImpactEdge(**edge) for edge in self.impact_edges)
        object.__setattr__(self, "impact_edges", edges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "record_id": self.record_id,
            "receipt_ref": self.receipt_ref, "source_identity": dict(self.source_identity),
            "package_digest": self.package_digest,
            "environment_dependency": {"environment_fingerprint": self.environment_fingerprint,
                                       "dependency_manifest_digest": self.dependency_manifest_digest},
            "validator_identity": dict(self.validator_identity),
            "scope": {"skill_ids": list(self.skill_ids), "task_families": list(self.task_families)},
            "impact_edges": [edge.to_dict() for edge in self.impact_edges],
            "applicability_status": self.applicability_status, "unknown_reason": self.unknown_reason,
            "evidence_refs": list(self.evidence_refs), "trace_refs": list(self.trace_refs),
            "created_at": self.created_at, "planner_action": self.planner_action,
            "common_input_envelope": self.common_input_envelope.to_dict(),
            "drift_fields": list(self.drift_fields), "execution_status": self.execution_status,
            "ledger_model": LEDGER_MODEL, "status_ledger": self.status_ledger.to_dict(),
        }

    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PlannerBudget:
    probe_calls: int = 4
    retest_calls: int = 8
    repair_calls: int = 0
    rollback_calls: int = 0

    def __post_init__(self) -> None:
        for name in ("probe_calls", "retest_calls", "repair_calls", "rollback_calls"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class PlannerAction:
    action: str
    record_id: str
    reason: str
    evidence_refs: tuple[str, ...]
    estimated_cost: int
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "record_id": self.record_id, "reason": self.reason,
                "evidence_refs": list(self.evidence_refs), "estimated_cost": self.estimated_cost, "rank": self.rank}


@dataclass(frozen=True)
class ApplicabilityPlan:
    planner_version: str
    input_digest: str
    actions: tuple[PlannerAction, ...]
    budget: PlannerBudget
    counts: Mapping[str, int]
    planner_mode: str = "primary"

    def to_dict(self) -> dict[str, Any]:
        return {"planner_version": self.planner_version, "input_digest": self.input_digest,
                "planner_mode": self.planner_mode, "actions": [a.to_dict() for a in self.actions],
                "budget": {name: getattr(self.budget, name) for name in ("probe_calls", "retest_calls", "repair_calls", "rollback_calls")},
                "counts": dict(self.counts),
                "authority": "plan_only; evidence_gate/release/rollback remain authoritative"}


@dataclass(frozen=True)
class ApplicabilityAdapter:
    """Protocol boundary that binds all records to one frozen input envelope."""

    common_input: CommonInputEnvelope | Mapping[str, Any]
    planner_mode: str = "primary"

    def __post_init__(self) -> None:
        object.__setattr__(self, "common_input", CommonInputEnvelope.from_value(self.common_input))
        if self.planner_mode not in PLANNER_MODES:
            raise ValueError(f"unsupported planner_mode: {self.planner_mode}")

    def plan(self, records: Sequence[EvidenceApplicabilityRecord], *,
             drift_fields: Mapping[str, Iterable[str]] | None = None,
             budget: PlannerBudget = PlannerBudget()) -> ApplicabilityPlan:
        validate_common_input_envelope(records, self.common_input)
        return plan_applicability(records, drift_fields=drift_fields, budget=budget,
                                  planner_mode=self.planner_mode, common_input=self.common_input)


def _find_forbidden_keys(value: Any, path: str = "$", seen: set[int] | None = None) -> list[str]:
    seen = set() if seen is None else seen
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return []
        seen.add(identity)
        found: list[str] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).strip().lower() in _LEAK_KEYS:
                found.append(child_path)
            found.extend(_find_forbidden_keys(child, child_path, seen))
        return found
    if isinstance(value, (list, tuple, set)):
        return [item for index, child in enumerate(value) for item in _find_forbidden_keys(child, f"{path}[{index}]", seen)]
    return []


def validate_common_input_envelope(records: Sequence[EvidenceApplicabilityRecord], expected: CommonInputEnvelope | Mapping[str, Any]) -> str:
    envelope = CommonInputEnvelope.from_value(expected)
    for record in records:
        if record.common_input_envelope != envelope:
            raise ValueError(f"record {record.record_id} is not bound to the frozen common-input envelope")
    return envelope.digest()


def _bound_drift_fields(record: EvidenceApplicabilityRecord, drift_fields: Mapping[str, Iterable[str]] | None) -> tuple[str, ...]:
    if drift_fields is None:
        return record.drift_fields
    if not isinstance(drift_fields, Mapping):
        raise TypeError("drift_fields must map record_id to fields; global fan-out is rejected")
    if record.record_id not in drift_fields:
        raise ValueError(f"drift_fields missing record {record.record_id}")
    supplied = tuple(sorted(set(_tuple_text(drift_fields[record.record_id], f"drift_fields[{record.record_id}]"))))
    if supplied != record.drift_fields:
        raise ValueError(f"drift_fields for {record.record_id} do not match sidecar binding")
    return record.drift_fields


def _validate_planner_inputs(records: Sequence[EvidenceApplicabilityRecord], drift_fields: Mapping[str, Iterable[str]] | None,
                             common_input: CommonInputEnvelope | Mapping[str, Any] | None) -> dict[str, tuple[str, ...]]:
    if not records:
        raise ValueError("records must not be empty")
    result: dict[str, tuple[str, ...]] = {}
    for record in records:
        if not isinstance(record, EvidenceApplicabilityRecord):
            raise TypeError("records must contain EvidenceApplicabilityRecord instances")
        leaked = _find_forbidden_keys(record.to_dict())
        if leaked:
            raise ValueError(f"planner input contains forbidden truth/holdout fields: {leaked}")
        result[record.record_id] = _bound_drift_fields(record, drift_fields)
    if common_input is not None:
        validate_common_input_envelope(records, common_input)
    return result


def plan_applicability(records: Sequence[EvidenceApplicabilityRecord], *,
                       drift_fields: Mapping[str, Iterable[str]] | None = None,
                       budget: PlannerBudget = PlannerBudget(),
                       planner_version: str = "skillwiki.evidence-applicability-planner.v2",
                       planner_mode: str = "primary",
                       common_input: CommonInputEnvelope | Mapping[str, Any] | None = None) -> ApplicabilityPlan:
    """Create a deterministic bounded plan without reading oracle truth."""
    if planner_mode not in PLANNER_MODES:
        raise ValueError(f"unsupported planner_mode: {planner_mode}")
    bound_fields = _validate_planner_inputs(records, drift_fields, common_input)
    planner_version = _text(planner_version, "planner_version")
    if len({record.record_id for record in records}) != len(records):
        raise ValueError("record_id values must be unique")
    if planner_mode == "primary" and any(record.planner_action in {"repair", "rollback"} for record in records):
        raise ValueError("repair/rollback require explicit planner_mode='non_primary'")
    input_payload = {"planner_version": planner_version, "planner_mode": planner_mode,
                     "drift_fields": {key: bound_fields[key] for key in sorted(bound_fields)},
                     "common_input": CommonInputEnvelope.from_value(common_input).to_dict() if common_input is not None else None,
                     "records": [record.to_dict() for record in sorted(records, key=lambda item: item.record_id)],
                     "budget": {name: getattr(budget, name) for name in ("probe_calls", "retest_calls", "repair_calls", "rollback_calls")}}
    input_digest = "sha256:" + hashlib.sha256(json.dumps(input_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    actions: list[tuple[int, str, str, EvidenceApplicabilityRecord, int]] = []
    for record in records:
        direct = bool(bound_fields[record.record_id])
        incoming = sorted(edge.source for edge in record.impact_edges if edge.target == record.record_id)
        if record.applicability_status == "invalid":
            action, priority, reason, cost = "defer", 0, record.unknown_reason or "invalid applicability evidence", 0
        elif planner_mode == "non_primary" and record.planner_action in {"repair", "rollback"}:
            action, priority, reason, cost = record.planner_action, 1, f"sidecar requested {record.planner_action} plan", 1
        elif record.applicability_status in {"unknown", "stale"} or direct:
            action, priority, reason, cost = "probe", 1, record.unknown_reason or "applicability requires an evidence probe", 1
        elif incoming:
            action, priority, reason, cost = "retest", 2, "observed impact edge requires downstream retest", 1
        else:
            action, priority, reason, cost = "defer", 3, "no observed drift impact for this receipt", 0
        actions.append((priority, action, reason, record, cost))
    counts = {action: 0 for action in sorted(PLANNER_ACTIONS)}
    selected: list[PlannerAction] = []
    used = {"probe": 0, "retest": 0, "repair": 0, "rollback": 0}
    limits = {"probe": budget.probe_calls, "retest": budget.retest_calls, "repair": budget.repair_calls, "rollback": budget.rollback_calls}
    for rank, (_, action, reason, record, cost) in enumerate(sorted(actions, key=lambda row: (row[0], row[1], row[3].record_id)), start=1):
        if action in limits and used[action] >= limits[action]:
            action, reason, cost = "defer", f"{action} budget exhausted; applicability remains unverified", 0
        elif action in used:
            used[action] += 1
        counts[action] += 1
        selected.append(PlannerAction(action, record.record_id, reason, record.evidence_refs, cost, rank))
    return ApplicabilityPlan(planner_version, input_digest, tuple(selected), budget, counts, planner_mode)


__all__ = ["APPLICABILITY_STATUSES", "PRIMARY_ACTIONS", "PLANNER_ACTIONS", "PLANNER_MODES", "EXECUTION_STATUSES", "LEDGER_MODEL",
           "ApplicabilityAdapter", "ApplicabilityPlan", "CommonInputEnvelope", "EvidenceApplicabilityRecord", "ImpactEdge", "PlannerAction",
           "PlannerBudget", "StatusLedger", "plan_applicability", "validate_common_input_envelope"]
