"""Pure M1 evidence applicability decisions for release candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_APPLICABILITY_FIELDS = (
    "contract_version",
    "environment_fingerprint",
    "verifier_version",
)
_ENVIRONMENT_FAILURE_CLASSES = {
    "credential",
    "dependency",
    "environment",
    "network",
    "sandbox",
    "timeout",
}
_WARNING_KEYS = ("warning", "warnings", "warning_codes")


@dataclass(frozen=True)
class EvidenceGateResult:
    """Decision plus the reason that must accompany it in a caller's record."""

    decision: str
    reason: str
    repair_allowed: bool = False
    matched_trace_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "repair_allowed": self.repair_allowed,
            "matched_trace_ids": list(self.matched_trace_ids),
        }


def decide_evidence_gate(
    manifest: Mapping[str, Any],
    traces: Sequence[Mapping[str, Any]],
    evaluation: Mapping[str, Any],
) -> EvidenceGateResult:
    """Return an M1 release decision without side effects.

    The function deliberately accepts mappings rather than introducing a second
    public schema. Callers can adapt their existing records, while ordinary CI
    and the full flow use exactly the same decision inputs and rules.
    """

    warning_reason = _warning_reason(manifest, traces, evaluation)
    if warning_reason:
        return _defer(warning_reason)
    if _string(manifest.get("parent_hash")):
        return _defer("parent lineage is not bound to the current Skill release contract")

    artifact_hash = _content_hash(manifest)
    if not artifact_hash:
        return _defer("artifact hash is missing")

    evaluated_hash = _string(evaluation.get("artifact_hash"))
    if not evaluated_hash:
        return _defer("evaluation artifact hash is missing")
    if evaluated_hash != artifact_hash:
        return _reject("evaluation artifact hash does not match manifest artifact hash")

    if bool(manifest.get("license_uncertain")) or bool(manifest.get("source_license_uncertain")):
        return _defer("source license applicability is uncertain")

    applicability = evaluation.get("applicability")
    if not isinstance(applicability, Mapping):
        return _defer("evaluation applicability is missing")
    for field in _APPLICABILITY_FIELDS:
        expected = _string(manifest.get(field)) if field == "contract_version" else ""
        observed = _string(applicability.get(field))
        if not observed or (field == "contract_version" and not expected):
            return _defer(f"{field} is missing from manifest or evaluation applicability")
        if expected and expected != observed:
            return _defer(f"{field} does not match evaluation applicability")

    trace_ids = evaluation.get("trace_ids")
    if not isinstance(trace_ids, Sequence) or isinstance(trace_ids, (str, bytes)) or not trace_ids:
        return _defer("evaluation has no trace references")
    indexed: dict[str, Mapping[str, Any]] = {}
    for trace in traces:
        if not isinstance(trace, Mapping):
            continue
        run_id = _string(trace.get("run_id"))
        if run_id:
            if run_id in indexed:
                return _defer("evaluation references duplicate trace ids")
            indexed[run_id] = trace

    matched: list[str] = []
    for raw_trace_id in trace_ids:
        trace_id = _string(raw_trace_id)
        if not trace_id or trace_id not in indexed:
            return _defer("a referenced execution trace is missing")
        trace = indexed[trace_id]
        matched.append(trace_id)

        trace_hash = _string(trace.get("artifact_hash"))
        if not trace_hash:
            return _defer("execution trace artifact hash is missing", matched)
        if trace_hash != artifact_hash:
            return _reject("execution trace artifact hash does not match manifest artifact hash", matched)

        for field in _APPLICABILITY_FIELDS[1:]:
            expected = _string(applicability.get(field))
            observed = _string(trace.get(field))
            if not observed:
                return _defer(f"execution trace {field} is missing", matched)
            if expected != observed:
                return _defer(f"execution trace {field} does not match evaluation applicability", matched)
            manifest_value = _string(manifest.get(field))
            if manifest_value and manifest_value != observed:
                return _defer(f"execution trace {field} does not match manifest", matched)

        if not _string(trace.get("task_id")) or not _string(trace.get("input_ref")):
            return _defer("execution trace task or input identity is missing", matched)
        if not _string(trace.get("evidence_path")):
            return _defer("execution trace evidence path is missing", matched)
        spans = trace.get("evidence_spans")
        if not isinstance(spans, Sequence) or isinstance(spans, (str, bytes)) or not spans:
            return _defer("execution trace evidence spans are missing", matched)

        status = _string(trace.get("status")).lower()
        failure_class = _string(trace.get("failure_class")).lower()
        if status in {"unknown", "timeout"}:
            return _defer(f"execution trace status is {status}", matched)
        if status == "failed":
            if not failure_class:
                return _defer("execution trace failure class is missing", matched)
            if failure_class in _ENVIRONMENT_FAILURE_CLASSES:
                return _defer(f"execution trace has environment failure: {failure_class}", matched)
            return _reject("execution trace has a skill or verifier failure", matched)
        if status != "passed":
            return _defer("execution trace status is missing or unsupported", matched)
        if failure_class:
            return _defer("passed execution trace carries a failure classification", matched)

    # Extended qualification is opt-in. Legacy M1 packets retain their
    # established contract; callers declaring a full U must provide all of it.
    if "required_U" in manifest:
        required = manifest["required_U"]
        if (not isinstance(required, list) or not required
                or any(not isinstance(pair, (list, tuple)) or len(pair) != 2
                       or any(not isinstance(item, str) or not item.strip() for item in pair)
                       for pair in required)):
            return _defer("required qualification U is missing or malformed", matched)
        required_pairs = [tuple(pair) for pair in required]
        if len(set(required_pairs)) != len(required_pairs):
            return _defer("required qualification U has duplicate inputs", matched)
        if len(set(matched)) != len(matched):
            return _defer("evaluation has duplicate trace references", matched)
        source = _string(manifest.get("source_snapshot"))
        if not source or _string(applicability.get("source_snapshot")) != source:
            return _defer("source snapshot does not match evaluation applicability", matched)
        if any(_string(indexed[rid].get("source_snapshot")) != source for rid in matched):
            return _defer("execution trace source snapshot does not match manifest", matched)
        observed_pairs = [(str(indexed[rid]["task_id"]), str(indexed[rid]["input_ref"]))
                          for rid in matched]
        if len(observed_pairs) != len(required_pairs) or set(observed_pairs) != set(required_pairs):
            return _defer("execution traces do not cover the complete required qualification U", matched)

    result = _string(evaluation.get("result")).lower()
    if result == "fail":
        return _reject("evaluation result is fail", matched)
    if result != "pass":
        return _defer("evaluation result is missing or unknown", matched)

    if evaluation.get("rollback_target") and evaluation.get("revalidated") is not True:
        return _defer("rollback target has not been revalidated", matched)

    return EvidenceGateResult(
        decision="accept",
        reason="evidence matches artifact, contract, environment, and verifier",
        matched_trace_ids=tuple(matched),
    )


def _content_hash(manifest: Mapping[str, Any]) -> str:
    content_hash = manifest.get("content_hash")
    if isinstance(content_hash, Mapping):
        return _string(content_hash.get("value"))
    return _string(content_hash)


def _warning_reason(
    manifest: Mapping[str, Any],
    traces: Sequence[Mapping[str, Any]],
    evaluation: Mapping[str, Any],
) -> str:
    """Treat explicit warnings and pass-with-warnings conclusions as non-release."""
    sources: list[tuple[str, Mapping[str, Any]]] = [
        ("manifest", manifest),
        ("evaluation", evaluation),
    ]
    sources.extend((f"execution trace {index}", trace) for index, trace in enumerate(traces))
    for label, source in sources:
        for key in _WARNING_KEYS:
            value = source.get(key)
            if value:
                return f"{label} contains {key}; release acceptance is blocked"
        for key in ("conclusion", "review_conclusion"):
            value = source.get(key)
            if isinstance(value, str) and "warning" in value.lower():
                return f"{label} conclusion contains warnings; release acceptance is blocked"
    return ""


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _defer(reason: str, matched: Sequence[str] = ()) -> EvidenceGateResult:
    return EvidenceGateResult(
        decision="defer",
        reason=reason,
        matched_trace_ids=tuple(matched),
    )


def _reject(reason: str, matched: Sequence[str] = ()) -> EvidenceGateResult:
    return EvidenceGateResult(
        decision="reject",
        reason=reason,
        matched_trace_ids=tuple(matched),
    )
