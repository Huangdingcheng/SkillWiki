"""Internal schema and fail-closed validator for M3 paired evaluations."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PAIR_SCHEMA_VERSION = "m3-p1-pair-20260922"
DEVELOPMENT_FIXTURE_SCOPE = "development_fixture"
PairArm = Literal["with_skill", "without_skill"]
TraceStatus = Literal["passed", "failed", "unknown", "timeout", "blocked"]
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_TASK_FIELDS = (
    "source_id",
    "source_revision",
    "task_id",
    "task_family",
    "split",
    "task_digest",
    "input_ref",
    "task_input_hash",
)
_CONFIG_FIELDS = (
    "model",
    "harness",
    "environment_fingerprint",
    "tool_allowlist",
    "seed",
    "temperature",
    "top_p",
    "timeout_s",
    "max_tokens",
    "budget",
    "retry_policy",
    "cache_memory_namespace",
)
_VERIFIER_FIELDS = ("verifier_ref", "verifier_version", "verifier_hash")


class CostEnvelope(BaseModel):
    """Known cost fields; absent accounting remains null rather than zero."""

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    tool_calls: int | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usd: float | None = Field(default=None, ge=0)


class PairManifest(BaseModel):
    """Task/config/verifier identity rechecked when aggregating both arms."""

    model_config = ConfigDict(validate_assignment=True)

    schema_version: Literal[PAIR_SCHEMA_VERSION] = PAIR_SCHEMA_VERSION
    scope: Literal["development_fixture"] = DEVELOPMENT_FIXTURE_SCOPE
    pair_id: str = Field(min_length=1)
    task: Dict[str, Any]
    config: Dict[str, Any]
    verifier: Dict[str, Any]
    task_hash: str = Field(pattern=_DIGEST_PATTERN)
    skill_artifact_hash: str = Field(pattern=_DIGEST_PATTERN)
    config_hash: str = Field(pattern=_DIGEST_PATTERN)
    verifier_hash: str = Field(pattern=_DIGEST_PATTERN)
    pair_hash: str = Field(pattern=_DIGEST_PATTERN)
    repeats: int = Field(default=1, ge=1)
    denominator: int = Field(ge=2)

    @field_validator("task")
    @classmethod
    def validate_task(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        _require_fields(value, _TASK_FIELDS, "task")
        _require_digest(value["task_digest"], "task.task_digest")
        _require_digest(value["task_input_hash"], "task.task_input_hash")
        return value

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        _require_fields(value, _CONFIG_FIELDS, "config")
        if not isinstance(value["tool_allowlist"], list):
            raise ValueError("config.tool_allowlist must be a list")
        if not isinstance(value["budget"], dict) or not value["budget"]:
            raise ValueError("config.budget must be a non-empty object")
        if not isinstance(value["retry_policy"], dict) or not value["retry_policy"]:
            raise ValueError("config.retry_policy must be a non-empty object")
        return value

    @field_validator("verifier")
    @classmethod
    def validate_verifier(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        _require_fields(value, _VERIFIER_FIELDS, "verifier")
        _require_digest(value["verifier_hash"], "verifier.verifier_hash")
        return value

    @model_validator(mode="after")
    def validate_denominator(self) -> "PairManifest":
        expected = 2 * self.repeats
        if self.denominator != expected:
            raise ValueError(f"denominator must equal 2 * repeats ({expected})")
        return self


class PairTrace(BaseModel):
    """Normalized persisted development record for one treatment arm."""

    model_config = ConfigDict(validate_assignment=True, allow_inf_nan=False)

    schema_version: Literal[PAIR_SCHEMA_VERSION] = PAIR_SCHEMA_VERSION
    scope: Literal["development_fixture"] = DEVELOPMENT_FIXTURE_SCOPE
    pair_id: str = Field(min_length=1)
    pair_hash: str = Field(pattern=_DIGEST_PATTERN)
    arm: PairArm
    repeat_index: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    task_hash: str = Field(pattern=_DIGEST_PATTERN)
    skill_artifact_hash: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    config_hash: str = Field(pattern=_DIGEST_PATTERN)
    verifier_hash: str = Field(pattern=_DIGEST_PATTERN)
    status: TraceStatus
    failure_class: str = Field(min_length=1)
    unknown_reason: str = ""
    score: float | None = Field(default=None, ge=0, le=1)
    cost: CostEnvelope = Field(default_factory=CostEnvelope)
    latency_ms: float | None = Field(default=None, ge=0)
    evidence_path: str = Field(min_length=1)
    evidence_digest: str = Field(pattern=_DIGEST_PATTERN)
    verifier_spans: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_failure_fields(self) -> "PairTrace":
        if self.status == "passed" and self.failure_class != "none":
            raise ValueError("passed trace must use failure_class='none'")
        if self.status in {"unknown", "timeout", "blocked"} and not self.unknown_reason:
            raise ValueError(f"{self.status} trace requires unknown_reason")
        return self


class PairEnvelope(BaseModel):
    """Aggregate pair record with complete denominator and explicit blocking."""

    schema_version: str = PAIR_SCHEMA_VERSION
    scope: Literal["development_fixture"] = DEVELOPMENT_FIXTURE_SCOPE
    pair_id: str
    pair_hash: str
    task_hash: str
    config_hash: str
    verifier_hash: str
    denominator: int
    expected_denominator: int
    counts: Dict[str, Dict[str, int]]
    trace_ids: List[str]
    pair_status: Literal["completed", "unknown", "blocked"]
    result: Literal["pass", "fail", "unknown", "blocked"]
    decision: Literal["accept", "defer"]
    reason: str
    validation_errors: List[str] = Field(default_factory=list)
    score_delta: float | None = None
    cost_delta: Dict[str, float | None] = Field(default_factory=dict)
    traces: List[PairTrace] = Field(default_factory=list)


def canonical_hash(value: Mapping[str, Any]) -> str:
    """Hash a sorted JSON projection with the shared LF canonicalization."""

    canonical = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_pair_manifest(
    *,
    task: Mapping[str, Any],
    skill_artifact_hash: str,
    config: Mapping[str, Any],
    verifier: Mapping[str, Any],
    pair_id: str,
    repeats: int = 1,
) -> PairManifest:
    """Build separate task, Skill, config, verifier and pair identities."""

    task_payload = dict(task)
    config_payload = dict(config)
    verifier_payload = dict(verifier)
    _require_fields(task_payload, _TASK_FIELDS, "task")
    _require_fields(config_payload, _CONFIG_FIELDS, "config")
    _require_fields(verifier_payload, _VERIFIER_FIELDS, "verifier")
    _require_digest(skill_artifact_hash, "skill_artifact_hash")
    task_hash = canonical_hash(task_payload)
    config_hash = canonical_hash(config_payload)
    verifier_hash = canonical_hash(verifier_payload)
    pair_hash = _pair_hash(
        pair_id=pair_id,
        task_hash=task_hash,
        skill_artifact_hash=skill_artifact_hash,
        config_hash=config_hash,
        verifier_hash=verifier_hash,
        repeats=repeats,
    )
    return PairManifest(
        pair_id=pair_id,
        task=task_payload,
        config=config_payload,
        verifier=verifier_payload,
        task_hash=task_hash,
        skill_artifact_hash=skill_artifact_hash,
        config_hash=config_hash,
        verifier_hash=verifier_hash,
        pair_hash=pair_hash,
        repeats=repeats,
        denominator=2 * repeats,
    )


def normalize_official_result(
    raw_result: str | Path,
    *,
    manifest: PairManifest,
    arm: PairArm,
    run_id: str,
    repeat_index: int,
    evidence_path: str,
) -> PairTrace:
    """Normalize one persisted internal result without substituting a verifier."""

    raw_path = Path(raw_result).resolve()
    raw_bytes = raw_path.read_bytes()
    loaded = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("persisted result must be a JSON object")
    return _trace_from_raw(
        loaded, raw_bytes, manifest=manifest, arm=arm, run_id=run_id,
        repeat_index=repeat_index, evidence_path=evidence_path,
    )


def _trace_from_raw(
    raw_result: Mapping[str, Any],
    raw_bytes: bytes,
    *,
    manifest: PairManifest,
    arm: PairArm,
    run_id: str,
    repeat_index: int,
    evidence_path: str,
) -> PairTrace:
    """Project one byte-bound internal record without inferring provenance."""

    raw_status = str(raw_result.get("status") or "unknown").strip().lower()
    status_map = {
        "pass": "passed",
        "passed": "passed",
        "success": "passed",
        "fail": "failed",
        "failed": "failed",
        "error": "unknown",
        "unknown": "unknown",
        "timeout": "timeout",
        "timed_out": "timeout",
        "blocked": "blocked",
    }
    status = status_map.get(raw_status, "unknown")
    if status == "passed":
        failure_class = "none"
        unknown_reason = ""
    elif status == "failed":
        failure_class = str(raw_result.get("failure_class") or "official_verifier")
        unknown_reason = ""
    elif status == "timeout":
        failure_class = str(raw_result.get("failure_class") or "timeout")
        unknown_reason = str(raw_result.get("unknown_reason") or raw_result.get("failure_reason") or "official run timed out")
    elif status == "blocked":
        failure_class = str(raw_result.get("failure_class") or "sandbox")
        unknown_reason = str(raw_result.get("unknown_reason") or raw_result.get("failure_reason") or "official run is blocked")
    else:
        failure_class = str(raw_result.get("failure_class") or "verifier")
        unknown_reason = str(raw_result.get("unknown_reason") or raw_result.get("failure_reason") or "official result is unavailable")

    cost_payload = raw_result.get("cost")
    cost = CostEnvelope.model_validate(cost_payload if isinstance(cost_payload, Mapping) else {})
    score = raw_result.get("score")
    if score is None:
        score = raw_result.get("reward")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    if status in {"passed", "failed"} and score is None:
        status = "unknown"
        failure_class = "verifier"
        unknown_reason = "persisted result has no numeric score or reward"
    spans = raw_result.get("verifier_spans")
    verifier_spans = [dict(item) for item in spans if isinstance(item, Mapping)] if isinstance(spans, list) else []
    return PairTrace(
        pair_id=manifest.pair_id,
        pair_hash=manifest.pair_hash,
        arm=arm,
        repeat_index=repeat_index,
        run_id=run_id,
        task_hash=manifest.task_hash,
        skill_artifact_hash=manifest.skill_artifact_hash if arm == "with_skill" else None,
        config_hash=manifest.config_hash,
        verifier_hash=manifest.verifier_hash,
        status=status,
        failure_class=failure_class,
        unknown_reason=unknown_reason,
        score=score,
        cost=cost,
        latency_ms=raw_result.get("latency_ms") if isinstance(raw_result.get("latency_ms"), (int, float)) else None,
        evidence_path=evidence_path,
        evidence_digest=hashlib.sha256(raw_bytes).hexdigest(),
        verifier_spans=verifier_spans,
    )


def validate_pair_envelope(
    manifest: PairManifest,
    traces: Sequence[PairTrace | Mapping[str, Any]],
) -> PairEnvelope:
    """Validate arm invariants and aggregate without hiding unknown/blocked runs."""

    normalized = [trace if isinstance(trace, PairTrace) else PairTrace.model_validate(trace) for trace in traces]
    errors: List[str] = _manifest_errors(manifest)
    if len(normalized) != manifest.denominator:
        errors.append(f"denominator {len(normalized)} does not match expected {manifest.denominator}")

    seen: set[tuple[str, int]] = set()
    run_ids: set[str] = set()
    evidence_paths: set[str] = set()
    counts: Dict[str, Dict[str, int]] = {
        "with_skill": defaultdict(int),
        "without_skill": defaultdict(int),
    }
    for trace in normalized:
        counts[trace.arm][trace.status] += 1
        key = (trace.arm, trace.repeat_index)
        if key in seen:
            errors.append(f"duplicate arm/repeat: {trace.arm}/{trace.repeat_index}")
        seen.add(key)
        if trace.run_id in run_ids:
            errors.append(f"duplicate run_id: {trace.run_id}")
        run_ids.add(trace.run_id)
        resolved_evidence = str(Path(trace.evidence_path).resolve())
        if resolved_evidence in evidence_paths:
            errors.append(f"duplicate evidence_path: {resolved_evidence}")
        evidence_paths.add(resolved_evidence)
        if trace.pair_id != manifest.pair_id:
            errors.append(f"pair_id mismatch for {trace.run_id}")
        if trace.pair_hash != manifest.pair_hash:
            errors.append(f"pair_hash mismatch for {trace.run_id}")
        if trace.task_hash != manifest.task_hash:
            errors.append(f"task_hash mismatch for {trace.run_id}")
        if trace.config_hash != manifest.config_hash:
            errors.append(f"config_hash mismatch for {trace.run_id}")
        if trace.verifier_hash != manifest.verifier_hash:
            errors.append(f"verifier_hash mismatch for {trace.run_id}")
        errors.extend(_evidence_errors(trace, manifest))
        if trace.arm == "with_skill" and trace.skill_artifact_hash != manifest.skill_artifact_hash:
            errors.append(f"with_skill artifact hash mismatch for {trace.run_id}")
        if trace.arm == "without_skill" and trace.skill_artifact_hash is not None:
            errors.append(f"without_skill must not carry a Skill artifact hash: {trace.run_id}")

    expected_keys = {(arm, repeat) for arm in ("with_skill", "without_skill") for repeat in range(1, manifest.repeats + 1)}
    missing = sorted(expected_keys - seen)
    if missing:
        errors.append(f"missing arm/repeat records: {missing}")

    trace_ids = [trace.run_id for trace in normalized]
    if errors:
        pair_status: Literal["completed", "unknown", "blocked"] = "blocked"
        result: Literal["pass", "fail", "unknown", "blocked"] = "blocked"
        reason = "; ".join(errors)
    elif any(trace.status == "blocked" for trace in normalized):
        pair_status = "blocked"
        result = "blocked"
        reason = _status_reason(normalized, "blocked")
    elif any(trace.status in {"unknown", "timeout"} for trace in normalized):
        pair_status = "unknown"
        result = "unknown"
        reason = _status_reason(normalized, "unknown")
    else:
        pair_status = "completed"
        result = "fail" if any(trace.status == "failed" for trace in normalized) else "pass"
        reason = "both development-fixture arms completed with persisted verifier results"

    score_delta = _score_delta(normalized) if pair_status == "completed" else None
    cost_delta = _cost_delta(normalized) if pair_status == "completed" else _empty_cost_delta(normalized)
    return PairEnvelope(
        pair_id=manifest.pair_id,
        pair_hash=manifest.pair_hash,
        task_hash=manifest.task_hash,
        config_hash=manifest.config_hash,
        verifier_hash=manifest.verifier_hash,
        denominator=len(normalized),
        expected_denominator=manifest.denominator,
        counts={arm: dict(values) for arm, values in counts.items()},
        trace_ids=trace_ids,
        pair_status=pair_status,
        result=result,
        decision="accept" if pair_status == "completed" else "defer",
        reason=reason,
        validation_errors=errors,
        score_delta=score_delta,
        cost_delta=cost_delta,
        traces=normalized,
    )


def _status_reason(traces: Sequence[PairTrace], target: str) -> str:
    reasons = [
        f"{trace.arm}/{trace.repeat_index}: {trace.status} ({trace.unknown_reason})"
        for trace in traces
        if (target == "unknown" and trace.status in {"unknown", "timeout"}) or trace.status == target
    ]
    return "; ".join(reasons) or target


def _score_delta(traces: Sequence[PairTrace]) -> float | None:
    by_arm: Dict[str, List[float]] = {"with_skill": [], "without_skill": []}
    for trace in traces:
        if trace.score is None:
            return None
        by_arm[trace.arm].append(trace.score)
    if not by_arm["with_skill"] or not by_arm["without_skill"]:
        return None
    return round(mean(by_arm["with_skill"]) - mean(by_arm["without_skill"]), 6)


def _cost_delta(traces: Sequence[PairTrace]) -> Dict[str, float | None]:
    fields = ("tool_calls", "model_calls", "input_tokens", "output_tokens", "usd")
    result: Dict[str, float | None] = {}
    for field in fields:
        values: Dict[str, List[float]] = {"with_skill": [], "without_skill": []}
        for trace in traces:
            value = getattr(trace.cost, field)
            if value is None:
                values = {}
                break
            values[trace.arm].append(float(value))
        if values and values["with_skill"] and values["without_skill"]:
            result[field] = round(mean(values["with_skill"]) - mean(values["without_skill"]), 6)
        else:
            result[field] = None
    return result


def _empty_cost_delta(traces: Sequence[PairTrace]) -> Dict[str, float | None]:
    return {field: None for field in ("tool_calls", "model_calls", "input_tokens", "output_tokens", "usd")}


def _require_fields(value: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    if not value:
        raise ValueError(f"{label} must be a non-empty object")
    missing = [field for field in fields if field not in value or value[field] is None]
    blank = [field for field in fields if isinstance(value.get(field), str) and not value[field].strip()]
    if missing or blank:
        names = sorted(set(missing + blank))
        raise ValueError(f"{label} missing required fields: {names}")


def _require_digest(value: Any, label: str) -> None:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a 64-character lowercase hex digest")


def _pair_hash(
    *,
    pair_id: str,
    task_hash: str,
    skill_artifact_hash: str,
    config_hash: str,
    verifier_hash: str,
    repeats: int,
) -> str:
    return canonical_hash(
        {
            "schema_version": PAIR_SCHEMA_VERSION,
            "scope": DEVELOPMENT_FIXTURE_SCOPE,
            "pair_id": pair_id,
            "task_hash": task_hash,
            "skill_artifact_hash": skill_artifact_hash,
            "config_hash": config_hash,
            "verifier_hash": verifier_hash,
            "repeats": repeats,
        }
    )


def _manifest_errors(manifest: PairManifest) -> List[str]:
    errors: List[str] = []
    try:
        PairManifest.model_validate(manifest.model_dump())
    except ValueError as exc:
        errors.append(f"manifest schema is invalid: {exc}")
    computed_task = canonical_hash(manifest.task)
    computed_config = canonical_hash(manifest.config)
    computed_verifier = canonical_hash(manifest.verifier)
    if manifest.task_hash != computed_task:
        errors.append("manifest task_hash does not match task content")
    if manifest.config_hash != computed_config:
        errors.append("manifest config_hash does not match config content")
    if manifest.verifier_hash != computed_verifier:
        errors.append("manifest verifier_hash does not match verifier content")
    computed_pair = _pair_hash(
        pair_id=manifest.pair_id,
        task_hash=computed_task,
        skill_artifact_hash=manifest.skill_artifact_hash,
        config_hash=computed_config,
        verifier_hash=computed_verifier,
        repeats=manifest.repeats,
    )
    if manifest.pair_hash != computed_pair:
        errors.append("manifest pair_hash does not match current manifest content")
    return errors


def _evidence_errors(trace: PairTrace, manifest: PairManifest) -> List[str]:
    path = Path(trace.evidence_path)
    if not path.is_file():
        return [f"evidence file is missing for {trace.run_id}: {path}"]
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"evidence is unreadable for {trace.run_id}: {exc}"]
    if not isinstance(raw, dict):
        return [f"evidence must be a JSON object for {trace.run_id}"]

    errors: List[str] = []
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != trace.evidence_digest:
        errors.append(f"evidence digest mismatch for {trace.run_id}")
    expected = {
        "scope": DEVELOPMENT_FIXTURE_SCOPE,
        "pair_id": manifest.pair_id,
        "pair_hash": manifest.pair_hash,
        "task_hash": manifest.task_hash,
        "config_hash": manifest.config_hash,
        "verifier_hash": manifest.verifier_hash,
        "skill_artifact_hash": manifest.skill_artifact_hash if trace.arm == "with_skill" else None,
        "arm": trace.arm,
        "run_id": trace.run_id,
        "repeat_index": trace.repeat_index,
        "evidence_path": str(path.resolve()),
    }
    for field, expected_value in expected.items():
        if raw.get(field) != expected_value:
            errors.append(f"raw {field} mismatch for {trace.run_id}")
    try:
        replayed = _trace_from_raw(
            raw, raw_bytes, manifest=manifest, arm=trace.arm, run_id=trace.run_id,
            repeat_index=trace.repeat_index, evidence_path=trace.evidence_path,
        ).model_dump()
    except (TypeError, ValueError) as exc:
        errors.append(f"raw result is invalid for {trace.run_id}: {exc}")
    else:
        for field, actual in trace.model_dump().items():
            if actual != replayed[field]:
                errors.append(f"normalized {field} differs from persisted result for {trace.run_id}")
    return errors
