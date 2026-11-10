from __future__ import annotations

import pytest

from skillwiki.layers.skill_governance import (
    ApplicabilityAdapter,
    CommonInputEnvelope,
    EvidenceApplicabilityRecord,
    ImpactEdge,
    LEDGER_MODEL,
    PlannerBudget,
    StatusLedger,
    plan_applicability,
)


def _ref(name: str) -> str:
    return f"{name}#sha256:" + "a" * 64


COMMON = CommonInputEnvelope(
    fixture_manifest_digest="sha256:" + "0" * 64,
    task_digest="sha256:" + "1" * 64,
    task_family="comp01-maintenance-scheduling-v1",
    prompt_digest="sha256:" + "2" * 64,
    model="deepseek/deepseek-flash",
    provider="deepseek",
    seeds=(101, 202, 303),
    retry_policy="zero retries; a retry is a new failed slot",
    temperature=0.0,
    max_tokens=8192,
    environment_image_digest="sha256:" + "3" * 64,
    dependency_manifest_digest="sha256:" + "4" * 64,
    oracle_digest="sha256:" + "5" * 64,
    verifier_digest="sha256:" + "6" * 64,
    holdout_digest="sha256:" + "7" * 64,
)


def _record(record_id: str, *, status: str = "applicable", reason: str | None = None, edges=(), drift_fields=(), planner_action="defer"):
    return EvidenceApplicabilityRecord(
        record_id=record_id,
        receipt_ref=_ref(f"receipt://{record_id}"),
        source_identity={
            "repository": "https://example.invalid/source",
            "commit": "abc123",
            "version": "1.0.0",
            "license": "MIT",
        },
        package_digest="sha256:" + "1" * 64,
        environment_fingerprint="env-v1",
        dependency_manifest_digest="sha256:" + "2" * 64,
        validator_identity={"id": "validator", "version_or_hash": "v1"},
        task_families=("maintenance",),
        skill_ids=(record_id,),
        impact_edges=tuple(edges),
        applicability_status=status,
        unknown_reason=reason,
        evidence_refs=(_ref(f"raw/{record_id}.json"),),
        trace_refs=(_ref(f"trace://{record_id}"),),
        created_at="2026-10-01T00:00:00Z",
        common_input_envelope=COMMON,
        drift_fields=drift_fields,
        planner_action=planner_action,
    )


def test_sidecar_round_trip_contains_required_provenance_fields() -> None:
    record = _record(
        "s01",
        edges=(ImpactEdge("s01", "depends_on", "s02", _ref("raw/edge-s01-s02.json")),),
    )
    payload = record.to_dict()
    assert payload["source_identity"]["license"] == "MIT"
    assert payload["environment_dependency"]["dependency_manifest_digest"].startswith("sha256:")
    assert payload["scope"]["task_families"] == ["maintenance"]
    assert payload["impact_edges"][0]["evidence_ref"] == _ref("raw/edge-s01-s02.json")
    assert payload["trace_refs"] == [_ref("trace://s01")]
    assert payload["common_input_envelope"]["task_family"] == COMMON.task_family
    assert payload["ledger_model"] == LEDGER_MODEL
    assert record.digest().startswith("sha256:")


@pytest.mark.parametrize("status", ["unknown", "stale", "invalid"])
def test_uncertain_status_requires_reason(status: str) -> None:
    with pytest.raises(ValueError, match="unknown/stale/invalid"):
        _record("s01", status=status)


def test_impact_edges_require_external_evidence_reference() -> None:
    with pytest.raises(ValueError, match="evidence_ref"):
        ImpactEdge("s01", "depends_on", "s02", "")


def test_planner_is_deterministic_and_budgeted() -> None:
    records = [
        _record("s02", edges=(ImpactEdge("s01", "depends_on", "s02", _ref("raw/e.json")),)),
        _record("s01", status="unknown", reason="environment not admitted", drift_fields=("environment_digest",)),
        _record("s03"),
    ]
    budget = PlannerBudget(probe_calls=1, retest_calls=1)
    first = plan_applicability(records, drift_fields={record.record_id: record.drift_fields for record in records}, budget=budget)
    second = plan_applicability(list(reversed(records)), drift_fields={record.record_id: record.drift_fields for record in records}, budget=budget)
    assert first.to_dict() == second.to_dict()
    assert first.counts["probe"] == 1
    assert sum(action.estimated_cost for action in first.actions) <= 2
    assert all(action.action in {"probe", "retest", "defer"} for action in first.actions)


def test_planner_does_not_accept_oracle_truth_fields() -> None:
    record = _record("s01")
    # A sidecar cannot carry truth fields; adding one to its serialized view is
    # detected before any plan is emitted.
    original = record.to_dict
    object.__setattr__(record, "to_dict", lambda: {**original(), "truth_affected": ["s01"]})
    with pytest.raises(ValueError, match="forbidden truth/holdout"):
        plan_applicability([record])


def test_missing_task_family_and_trace_are_rejected() -> None:
    with pytest.raises(ValueError, match="task_families"):
        _record("s01").__class__(
            **{**_record("s01").__dict__, "task_families": ()}
        )
    with pytest.raises(ValueError, match="trace_refs"):
        _record("s01").__class__(
            **{**_record("s01").__dict__, "trace_refs": ()}
        )


def test_repair_and_rollback_are_plan_actions_only_and_budgeted() -> None:
    repair = _record("repair", status="applicable").__class__(
        **{**_record("repair", planner_action="repair").__dict__}
    )
    rollback = _record("rollback", status="applicable").__class__(
        **{**_record("rollback", planner_action="rollback").__dict__}
    )
    plan = plan_applicability(
        [repair, rollback],
        drift_fields={"repair": (), "rollback": ()},
        budget=PlannerBudget(repair_calls=1, rollback_calls=0),
        planner_mode="non_primary",
    )
    by_id = {action.record_id: action.action for action in plan.actions}
    assert by_id == {"repair": "repair", "rollback": "defer"}
    assert plan.to_dict()["authority"].startswith("plan_only")


def test_primary_mode_rejects_repair_and_rollback_requests() -> None:
    record = _record("repair", planner_action="repair")
    with pytest.raises(ValueError, match="non_primary"):
        plan_applicability([record])


def test_global_drift_fanout_is_rejected_and_binding_is_per_record() -> None:
    first = _record("s01", drift_fields=("source_commit",))
    second = _record("s02")
    with pytest.raises(TypeError, match="global fan-out"):
        plan_applicability([first, second], drift_fields=("source_commit",))
    plan = plan_applicability([first, second])
    assert {a.record_id: a.action for a in plan.actions} == {"s01": "probe", "s02": "defer"}


def test_recursive_leak_guard_rejects_nested_oracle_and_holdout_keys() -> None:
    record = _record("s01")
    original = record.to_dict
    object.__setattr__(record, "to_dict", lambda: {**original(), "nested": {"oracle_outcome": {"value": 1}}})
    with pytest.raises(ValueError, match="forbidden truth/holdout"):
        plan_applicability([record])


def test_common_input_envelope_binding_and_ref_hash_boundary() -> None:
    record = _record("s01")
    with pytest.raises(ValueError, match="frozen common-input"):
        plan_applicability([record], common_input={**COMMON.to_dict(), "provider": "other"})
    with pytest.raises(ValueError, match="ref#sha256"):
        _record("bad").__class__(**{**record.__dict__, "trace_refs": ("trace://unhashed",)})


def test_common_input_requires_frozen_temperature_and_max_tokens() -> None:
    payload = COMMON.to_dict()
    payload.pop("temperature")
    with pytest.raises(TypeError):
        CommonInputEnvelope.from_value(payload)
    with pytest.raises(ValueError, match="temperature"):
        CommonInputEnvelope.from_value({**COMMON.to_dict(), "temperature": 0.1})
    with pytest.raises(ValueError, match="max_tokens"):
        CommonInputEnvelope.from_value({**COMMON.to_dict(), "max_tokens": 4096})
    assert COMMON.to_dict()["temperature"] == 0.0
    assert COMMON.to_dict()["max_tokens"] == 8192


def test_status_ledger_matches_record_status_and_denominator() -> None:
    with pytest.raises(ValueError, match="planned=1"):
        StatusLedger(planned=0, failed=1)
    with pytest.raises(ValueError, match="matching status ledger"):
        _record("failed", status="unknown", reason="runtime failure").__class__(
            **{**_record("failed", status="unknown", reason="runtime failure").__dict__,
               "execution_status": "failure", "status_ledger": StatusLedger(untested=1)}
        )
    valid = _record("failed", status="unknown", reason="runtime failure").__class__(
        **{**_record("failed", status="unknown", reason="runtime failure").__dict__,
           "execution_status": "failure", "status_ledger": StatusLedger(planned=1, executed=1, terminal=1, failed=1)}
    )
    assert valid.to_dict()["status_ledger"]["failed"] == 1
    deferred = _record("deferred").__class__(
        **{**_record("deferred").__dict__, "execution_status": "defer", "status_ledger": StatusLedger(planned=1, defer=1)}
    )
    assert deferred.to_dict()["status_ledger"]["defer"] == 1


@pytest.mark.parametrize(
    ("execution_status", "ledger"),
    [
        ("failure", StatusLedger(planned=1, executed=1, terminal=1, failed=1)),
        ("timeout", StatusLedger(planned=1, executed=1, terminal=1, timeout=1)),
        ("reject", StatusLedger(planned=1, executed=1, terminal=1, reject=1)),
        ("defer", StatusLedger(planned=1, defer=1)),
        ("blocked", StatusLedger(planned=1, blocked=1)),
        ("untested", StatusLedger(planned=1, untested=1)),
        ("success", StatusLedger(planned=1, executed=1, terminal=1, scored=1)),
    ],
)
def test_each_record_status_has_one_conserved_slot(execution_status: str, ledger: StatusLedger) -> None:
    record = _record(f"status-{execution_status}").__class__(
        **{**_record(f"status-{execution_status}").__dict__,
           "execution_status": execution_status, "status_ledger": ledger}
    )
    payload = record.to_dict()["status_ledger"]
    assert payload["planned"] == 1
    assert sum(payload[name] for name in ("failed", "timeout", "reject", "defer", "blocked", "untested", "scored")) == 1


def test_ledger_rejects_unclassified_duplicate_and_under_counted_slots() -> None:
    with pytest.raises(ValueError, match="exactly one classified"):
        StatusLedger(planned=1)
    with pytest.raises(ValueError, match="exactly one classified"):
        StatusLedger(planned=1, failed=1, untested=1, executed=1, terminal=1)
    with pytest.raises(ValueError, match="exactly one classified"):
        StatusLedger(planned=1, failed=1, scored=1, executed=2, terminal=2)
    with pytest.raises(ValueError, match="executed and terminal"):
        StatusLedger(planned=1, failed=1)
    with pytest.raises(ValueError, match="executed and terminal"):
        StatusLedger(planned=1, failed=1, executed=1)
    with pytest.raises(ValueError, match="per-record ledger"):
        StatusLedger(planned=2, executed=2, terminal=2, failed=1)


def test_protocol_adapter_binds_common_input_before_planning() -> None:
    records = [_record("s01", drift_fields=("source_commit",)), _record("s02")]
    adapter = ApplicabilityAdapter(COMMON)
    plan = adapter.plan(records)
    assert plan.planner_mode == "primary"
    assert {action.action for action in plan.actions} == {"probe", "defer"}
