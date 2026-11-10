from __future__ import annotations

from copy import deepcopy

import pytest

from skillwiki.layers.skill_governance.evidence_gate import decide_evidence_gate


def _records() -> tuple[dict, list[dict], dict]:
    manifest = {
        "artifact_id": "skill-1@1.0.0",
        "skill_id": "skill-1",
        "version": "1.0.0",
        "content_hash": {
            "algorithm": "sha256",
            "encoding": "utf-8-lf-canonical",
            "value": "hash-1",
        },
        "source_snapshot": "snapshot-1",
        "contract_version": "contract-v1",
        "dependency_ids": [],
        "license_refs": ["MIT"],
    }
    traces = [
        {
            "run_id": "run-1",
            "artifact_hash": "hash-1",
            "task_id": "task-1",
            "input_ref": "fixture-1",
            "environment_fingerprint": "env-1",
            "model_harness_config": "fixture:deterministic-v1",
            "verifier_version": "verifier-v1",
            "status": "passed",
            "failure_class": "",
            "unknown_reason": "",
            "evidence_path": "artifacts/run-1/result.json",
            "evidence_spans": [{"verifier_path": "output.result", "passed": True}],
        }
    ]
    evaluation = {
        "evaluation_id": "eval-1",
        "artifact_hash": "hash-1",
        "trace_ids": ["run-1"],
        "applicability": {
            "contract_version": "contract-v1",
            "environment_fingerprint": "env-1",
            "verifier_version": "verifier-v1",
        },
        "result": "pass",
        "decision": "accept",
        "reason": "all checks passed",
        "actor": "ci",
        "created_at": "2026-09-22T00:00:00Z",
    }
    return manifest, traces, evaluation


def _decision(manifest: dict, traces: list[dict], evaluation: dict) -> str:
    return decide_evidence_gate(manifest, traces, evaluation).decision


def test_same_hash_and_conditions_accept() -> None:
    manifest, traces, evaluation = _records()

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "accept"
    assert result.reason == "evidence matches artifact, contract, environment, and verifier"


def test_artifact_hash_mismatch_rejects_even_when_trace_passed() -> None:
    manifest, traces, evaluation = _records()
    manifest["content_hash"]["value"] = "hash-new"

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "reject"
    assert "artifact hash" in result.reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_version", "contract-v2"),
        ("environment_fingerprint", "env-2"),
        ("verifier_version", "verifier-v2"),
    ],
)
def test_condition_mismatch_defers(field: str, value: str) -> None:
    manifest, traces, evaluation = _records()
    if field == "contract_version":
        manifest[field] = value
    elif field == "environment_fingerprint":
        traces[0][field] = value
    else:
        traces[0][field] = value

    assert _decision(manifest, traces, evaluation) == "defer"


def test_contract_applicability_mismatch_defers() -> None:
    manifest, traces, evaluation = _records()
    evaluation["applicability"]["contract_version"] = "contract-v2"

    assert _decision(manifest, traces, evaluation) == "defer"


def test_skill_output_failure_rejects() -> None:
    manifest, traces, evaluation = _records()
    traces[0].update(status="failed", failure_class="skill_output")

    assert _decision(manifest, traces, evaluation) == "reject"


def test_unclassified_failure_defers() -> None:
    manifest, traces, evaluation = _records()
    traces[0].update(status="failed", failure_class="")

    assert _decision(manifest, traces, evaluation) == "defer"


@pytest.mark.parametrize("failure_class", ["credential", "network", "dependency", "timeout"])
def test_environment_failures_defer_without_repair_signal(failure_class: str) -> None:
    manifest, traces, evaluation = _records()
    traces[0].update(status="failed", failure_class=failure_class)

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "defer"
    assert result.repair_allowed is False


@pytest.mark.parametrize(
    "change",
    [
        lambda traces, evaluation: traces.clear(),
        lambda traces, evaluation: traces[0].update(status="unknown", unknown_reason="not run"),
        lambda traces, evaluation: traces[0].update(evidence_path=""),
        lambda traces, evaluation: evaluation.update(trace_ids=[]),
    ],
)
def test_missing_or_unknown_evidence_defers(change) -> None:
    manifest, traces, evaluation = _records()
    change(traces, evaluation)

    assert _decision(manifest, traces, evaluation) == "defer"


def test_rollback_target_requires_revalidation() -> None:
    manifest, traces, evaluation = _records()
    evaluation["rollback_target"] = "tag/old"

    assert _decision(manifest, traces, evaluation) == "defer"

    evaluation["revalidated"] = True
    assert _decision(manifest, traces, evaluation) == "accept"


def test_license_uncertainty_defers() -> None:
    manifest, traces, evaluation = _records()
    manifest["license_uncertain"] = True

    assert _decision(manifest, traces, evaluation) == "defer"


def test_missing_evidence_spans_defer() -> None:
    manifest, traces, evaluation = _records()
    traces[0].pop("evidence_spans")

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "defer"
    assert "evidence spans" in result.reason


def test_explicit_warning_never_accepts() -> None:
    manifest, traces, evaluation = _records()
    evaluation["warning_codes"] = ["evidence_spans_not_recorded"]

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "defer"
    assert "warning" in result.reason


def test_pass_with_warnings_never_accepts() -> None:
    manifest, traces, evaluation = _records()
    evaluation["review_conclusion"] = "pass_with_warnings"

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "defer"
    assert "warnings" in result.reason


def test_unbound_parent_lineage_defers() -> None:
    manifest, traces, evaluation = _records()
    manifest["parent_hash"] = "stale-parent"

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "defer"
    assert "parent lineage" in result.reason


def test_ordinary_ci_and_full_flow_share_the_same_inputs() -> None:
    manifest, traces, evaluation = _records()
    ci_records = (deepcopy(manifest), deepcopy(traces), deepcopy(evaluation))
    full_records = (deepcopy(manifest), deepcopy(traces), deepcopy(evaluation))

    ci_result = decide_evidence_gate(*ci_records)
    full_result = decide_evidence_gate(*full_records)

    assert ci_result == full_result


def test_opt_in_full_qualification_requires_every_declared_pair() -> None:
    manifest, traces, evaluation = _records()
    manifest["required_U"] = [["task-1", "fixture-1"], ["task-2", "fixture-2"]]
    evaluation["applicability"]["source_snapshot"] = "snapshot-1"
    traces[0]["source_snapshot"] = "snapshot-1"

    result = decide_evidence_gate(manifest, traces, evaluation)

    assert result.decision == "defer"
    assert "complete required qualification U" in result.reason
