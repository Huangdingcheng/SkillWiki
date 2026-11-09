from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from skillwiki.layers.skill_management.gepa_skillopt_adapter import (
    GEPA_PROVENANCE,
    SKILLOPT_PROVENANCE,
    apply_bounded_skillopt_patch,
    build_candidate_record,
    candidate_admission,
    run_gepa_no_call,
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parent_fields(text: str = "parent skill") -> dict[str, str]:
    return {
        "parent_skill_id": "skill-dialogue",
        "parent_version": "1.2.3",
        "parent_skill_hash": _digest(text),
    }


def test_candidate_record_is_deterministic_and_provenance_bound() -> None:
    record = build_candidate_record(
        method="gepa",
        candidate_text="candidate skill",
        parent_skill_id="skill-dialogue",
        parent_version="1.2.3",
        parent_skill_text="parent skill",
        failure_trace=[{"case": "dialogue-1", "failed": True}],
        provenance=GEPA_PROVENANCE,
    )

    payload = record.to_dict()
    assert payload["candidate_hash"] == _digest("candidate skill")
    assert payload["parent_hash"] == _digest("parent skill")
    assert payload["failure_trace"] == [{"case": "dialogue-1", "failed": True}]
    assert payload["provenance"]["commit"] == GEPA_PROVENANCE["commit"]
    assert payload["model_status"] == {"state": "not_called", "model": None, "usage": None}


def test_admission_fails_closed_for_malformed_or_unlicensed_candidate() -> None:
    status, reason = candidate_admission({"candidate_text": "candidate"})
    assert status == "blocked_candidate_incomplete"
    assert "candidate_hash" in reason

    candidate = build_candidate_record(
        method="skillopt",
        candidate_text="candidate",
        parent_skill_id="skill-dialogue",
        parent_version="1.2.3",
        parent_hash=_digest("parent skill"),
        failure_trace=[{"id": "failure-1"}],
        provenance=SKILLOPT_PROVENANCE,
    ).to_dict()
    candidate["candidate_package"] = {
        "implementation": "skill.py",
        "entrypoint": "skill:run",
        "manifest": {"files": ["skill.py"]},
        "modes": ["default"],
        "license_scope": {"status": "unresolved"},
    }
    status, reason = candidate_admission(candidate)
    assert status == "blocked_candidate_license_scope"
    assert "verified" in reason


def test_gepa_uses_real_optimize_contract_without_a_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    class EvaluationBatch:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    calls: list[dict] = []

    def optimize(**kwargs):
        calls.append(kwargs)
        adapter = kwargs["adapter"]
        # Exercise the adapter protocol without any network/model call.
        batch = adapter.evaluate(kwargs["valset"], kwargs["seed_candidate"], capture_traces=True)
        assert batch.num_metric_calls == 1
        return SimpleNamespace(
            best_candidate={"skill_text": "proposed skill"},
            best_idx=1,
            num_candidates=2,
            total_metric_calls=1,
        )

    gepa = types.ModuleType("gepa")
    gepa.optimize = optimize
    core = types.ModuleType("gepa.core")
    adapter_module = types.ModuleType("gepa.core.adapter")
    adapter_module.EvaluationBatch = EvaluationBatch
    core.adapter = adapter_module
    gepa.core = core
    monkeypatch.setitem(sys.modules, "gepa", gepa)
    monkeypatch.setitem(sys.modules, "gepa.core", core)
    monkeypatch.setitem(sys.modules, "gepa.core.adapter", adapter_module)

    fields = _parent_fields()
    record = run_gepa_no_call(
        seed_text="seed skill",
        proposed_text="proposed skill",
        failure_trace=[{"id": "failure-1"}],
        max_metric_calls=4,
        seed=7,
        **fields,
    )

    assert len(calls) == 1
    assert "retries" not in calls[0]
    assert record.candidate_text == "proposed skill"
    assert record.metadata["model_call"] is False
    assert record.provenance["commit"] == GEPA_PROVENANCE["commit"]


def test_gepa_metric_call_bound_fails_closed() -> None:
    record = run_gepa_no_call(
        seed_text="seed skill",
        proposed_text="proposed skill",
        failure_trace=[{"id": "failure-1"}],
        max_metric_calls=5,
        **_parent_fields(),
    )
    assert record.status == "blocked_controls"
    assert "max_metric_calls" in record.blocked_reason


def test_skillopt_applies_pinned_patch_with_parent_and_candidate_hashes() -> None:
    integration_root = Path(__file__).resolve().parents[2]
    source_root = integration_root / "artifacts/20260929-skillopt-c-ref-42/source"
    parent = "# Skill\nold\n"
    result = apply_bounded_skillopt_patch(
        parent_skill=parent,
        patch={"edits": [{"op": "replace", "target": "old", "content": "new"}]},
        failure_trace=[{"id": "failure-1", "error": "old behavior"}],
        model_status={"state": "succeeded", "model": "deepseek-flash"},
        usage={"prompt_tokens": 12, "completion_tokens": 8},
        source_root=source_root,
        **_parent_fields(parent),
    )

    assert result.status == "patched"
    assert result.updated_skill == "# Skill\nnew\n"
    assert result.patch_report[0]["status"] == "applied_replace"
    assert result.candidate.provenance["commit"] == SKILLOPT_PROVENANCE["commit"]
    assert result.candidate.parent_hash == _digest(parent)
    assert result.candidate.candidate_hash == _digest(result.updated_skill)
    assert result.candidate.model_status == {
        "state": "succeeded",
        "model": "deepseek-flash",
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }
    assert result.controls == {"max_output_tokens": 8192, "edit_budget": 4, "retries": 0}


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"max_output_tokens": 8193}, "max_output_tokens"),
        ({"edit_budget": 5}, "edit_budget"),
        ({"retries": 1}, "retries"),
    ],
)
def test_skillopt_bounds_fail_closed_without_loading_or_calling_a_model(kwargs: dict, needle: str) -> None:
    parent = "# Skill\nold\n"
    result = apply_bounded_skillopt_patch(
        parent_skill=parent,
        patch={"edits": [{"op": "replace", "target": "old", "content": "new"}]},
        failure_trace=[],
        **_parent_fields(parent),
        **kwargs,
    )
    assert result.status == "blocked_controls"
    assert needle in result.blocked_reason


def test_skillopt_rejects_more_than_four_edits_before_patch_engine() -> None:
    parent = "# Skill\nold\n"
    patch = {"edits": [{"op": "append", "content": str(index)} for index in range(5)]}
    result = apply_bounded_skillopt_patch(
        parent_skill=parent,
        patch=patch,
        failure_trace=[],
        **_parent_fields(parent),
    )
    assert result.status == "blocked_controls"
    assert "5 edits" in result.blocked_reason


def test_skillopt_parent_hash_mismatch_blocks_before_patch_engine() -> None:
    parent = "# Skill\nold\n"
    result = apply_bounded_skillopt_patch(
        parent_skill=parent,
        patch={"edits": [{"op": "replace", "target": "old", "content": "new"}]},
        failure_trace=[{"id": "failure-1"}],
        parent_skill_hash=_digest("different parent"),
        parent_skill_id="skill-dialogue",
        parent_version="1.2.3",
    )
    assert result.status == "blocked_parent_hash_mismatch"
    assert result.updated_skill == parent


def test_skillopt_failed_model_status_blocks_candidate() -> None:
    parent = "# Skill\nold\n"
    result = apply_bounded_skillopt_patch(
        parent_skill=parent,
        patch={"edits": [{"op": "replace", "target": "old", "content": "new"}]},
        failure_trace=[{"id": "failure-1"}],
        model_status={"state": "failed", "model": "deepseek-flash"},
        **_parent_fields(parent),
    )
    assert result.status == "blocked_model_status"
    assert result.updated_skill == parent
