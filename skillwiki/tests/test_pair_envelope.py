from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from skillwiki.layers.skill_runtime.harness.pair_envelope import (
    PairManifest,
    build_pair_manifest,
    normalize_official_result,
    validate_pair_envelope,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _task() -> dict:
    return {
        "source_id": "benchflow-skillsbench",
        "source_revision": "skillsbench@commit-abc",
        "task_id": "citation-check",
        "task_family": "document",
        "split": "development",
        "task_digest": _digest("task-digest"),
        "input_ref": "tasks/citation-check/input.json",
        "task_input_hash": _digest("input-digest"),
    }


def _config() -> dict:
    return {
        "model": "local-fake-model-v1",
        "harness": "benchflow-fake-sandbox-v1",
        "environment_fingerprint": "sandbox=image@sha256:abc",
        "tool_allowlist": ["python"],
        "seed": 7,
        "temperature": 0.0,
        "top_p": 1.0,
        "timeout_s": 60,
        "max_tokens": 1000,
        "budget": {"max_usd": 1.0, "max_model_calls": 2},
        "retry_policy": {"max_attempts": 1},
        "cache_memory_namespace": "m3-p1-fake",
    }


def _verifier() -> dict:
    return {
        "verifier_ref": "benchflow/verifiers/citation-check@commit-v1",
        "verifier_version": "citation-verifier-v1",
        "verifier_hash": _digest("verifier-digest"),
    }


def _manifest(repeats: int = 1) -> PairManifest:
    return build_pair_manifest(
        task=_task(),
        skill_artifact_hash=_digest("skill-digest"),
        config=_config(),
        verifier=_verifier(),
        pair_id="pair-citation-001",
        repeats=repeats,
    )


def _raw(
    status: str,
    *,
    manifest: PairManifest | None = None,
    arm: str = "with_skill",
    run_id: str = "with-001",
    repeat_index: int = 1,
    evidence_path: str = "",
    score: float | None = None,
    cost: dict | None = None,
) -> dict:
    payload = {
        "scope": "development_fixture",
        "status": status,
        "score": score,
        "cost": (
            {"tool_calls": 1, "model_calls": 1, "input_tokens": 10, "output_tokens": 5, "usd": 0.01}
            if cost is None
            else cost
        ),
        "verifier_spans": [{"path": "output.ok", "actual": status == "pass", "expected": True}],
    }
    if manifest is not None:
        payload.update(
            {
                "pair_id": manifest.pair_id,
                "pair_hash": manifest.pair_hash,
                "task_hash": manifest.task_hash,
                "config_hash": manifest.config_hash,
                "verifier_hash": manifest.verifier_hash,
                "skill_artifact_hash": manifest.skill_artifact_hash if arm == "with_skill" else None,
                "arm": arm,
                "run_id": run_id,
                "repeat_index": repeat_index,
                "evidence_path": evidence_path,
            }
        )
    if status in {"error", "unknown"}:
        payload["failure_class"] = "verifier"
        payload["unknown_reason"] = "fake official verifier unavailable"
    return payload


def _traces(manifest: PairManifest, tmp_path: Path, *, no_skill_status: str = "pass"):
    with_path = tmp_path / "with.json"
    without_path = tmp_path / "without.json"
    with_path.write_text(
        json.dumps(
            _raw(
                "pass",
                manifest=manifest,
                arm="with_skill",
                run_id="with-001",
                evidence_path=str(with_path),
                score=1.0,
            )
        ),
        encoding="utf-8",
    )
    without_path.write_text(
        json.dumps(
            _raw(
                no_skill_status,
                manifest=manifest,
                arm="without_skill",
                run_id="without-001",
                evidence_path=str(without_path),
                score=1.0 if no_skill_status == "pass" else 0.0,
            )
        ),
        encoding="utf-8",
    )
    return [
        normalize_official_result(
            with_path,
            manifest=manifest,
            arm="with_skill",
            run_id="with-001",
            repeat_index=1,
            evidence_path=str(with_path),
        ),
        normalize_official_result(
            without_path,
            manifest=manifest,
            arm="without_skill",
            run_id="without-001",
            repeat_index=1,
            evidence_path=str(without_path),
        ),
    ]


def test_pair_manifest_separates_task_skill_config_and_verifier_hashes():
    manifest = _manifest()

    assert manifest.task_hash != manifest.skill_artifact_hash
    assert manifest.task_hash != manifest.config_hash
    assert manifest.config_hash != manifest.verifier_hash
    assert manifest.pair_hash not in {
        manifest.task_hash,
        manifest.config_hash,
        manifest.verifier_hash,
        manifest.skill_artifact_hash,
    }


def test_pair_manifest_rejects_inconsistent_full_denominator():
    valid = _manifest(repeats=2)
    with pytest.raises(ValueError, match="denominator"):
        PairManifest(
            pair_id=valid.pair_id,
            task=valid.task,
            config=valid.config,
            verifier=valid.verifier,
            task_hash=valid.task_hash,
            skill_artifact_hash=valid.skill_artifact_hash,
            config_hash=valid.config_hash,
            verifier_hash=valid.verifier_hash,
            pair_hash=valid.pair_hash,
            repeats=2,
            denominator=3,
        )


def test_complete_pair_preserves_denominator_and_computes_delta(tmp_path):
    manifest = _manifest()
    result = validate_pair_envelope(manifest, _traces(manifest, tmp_path, no_skill_status="fail"))

    assert result.denominator == 2
    assert result.expected_denominator == 2
    assert result.pair_status == "completed"
    assert result.result == "fail"
    assert result.decision == "accept"
    assert result.score_delta == 1.0
    assert result.counts["with_skill"]["passed"] == 1
    assert result.counts["without_skill"]["failed"] == 1


def test_unknown_or_timeout_is_fail_closed_and_keeps_full_denominator(tmp_path):
    manifest = _manifest()
    result = validate_pair_envelope(manifest, _traces(manifest, tmp_path, no_skill_status="timeout"))

    assert result.denominator == 2
    assert result.pair_status == "unknown"
    assert result.result == "unknown"
    assert result.decision == "defer"
    assert result.score_delta is None
    assert "timeout" in result.reason


def test_mismatched_config_and_copied_no_skill_hash_are_blocked(tmp_path):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    traces[0].config_hash = "f" * 64
    traces[1].skill_artifact_hash = manifest.skill_artifact_hash

    result = validate_pair_envelope(manifest, traces)

    assert result.pair_status == "blocked"
    assert result.decision == "defer"
    assert any("config_hash" in error for error in result.validation_errors)
    assert any("without_skill" in error for error in result.validation_errors)


def test_normalization_maps_official_error_and_missing_cost_without_fabricating_zero(tmp_path):
    manifest = _manifest()
    evidence = tmp_path / "error.json"
    evidence.write_text(
        json.dumps(
            _raw(
                "error",
                manifest=manifest,
                arm="with_skill",
                run_id="with-error",
                evidence_path=str(evidence),
                cost={},
            )
        ),
        encoding="utf-8",
    )

    trace = normalize_official_result(
        evidence,
        manifest=manifest,
        arm="with_skill",
        run_id="with-error",
        repeat_index=1,
        evidence_path=str(evidence),
    )

    assert trace.status == "unknown"
    assert trace.failure_class == "verifier"
    assert trace.unknown_reason
    assert trace.cost.input_tokens is None
    assert trace.cost.usd is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_hash", "0" * 64),
        ("config_hash", "1" * 64),
        ("verifier_hash", "2" * 64),
        ("run_id", "spoofed-run"),
        ("arm", "without_skill"),
        ("repeat_index", 2),
        ("pair_hash", "3" * 64),
    ],
)
def test_persisted_raw_identity_conflicts_are_blocked(tmp_path, field, value):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    path = Path(traces[0].evidence_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")

    result = validate_pair_envelope(manifest, traces)

    assert result.pair_status == "blocked"
    assert result.decision == "defer"
    assert any("raw" in error or "digest" in error for error in result.validation_errors)


def test_missing_evidence_file_is_blocked(tmp_path):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    Path(traces[0].evidence_path).unlink()

    result = validate_pair_envelope(manifest, traces)

    assert result.pair_status == "blocked"
    assert any("evidence" in error for error in result.validation_errors)


@pytest.mark.parametrize("part", ["task", "config", "verifier"])
def test_mutated_manifest_content_is_rehashed_and_blocked(tmp_path, part):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    manifest.__dict__[part]["mutated"] = "content"

    result = validate_pair_envelope(manifest, traces)

    assert result.pair_status == "blocked"
    assert any(f"{part}_hash" in error for error in result.validation_errors)


def test_forged_pair_hash_is_recomputed_and_blocked(tmp_path):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    manifest.pair_hash = "f" * 64

    result = validate_pair_envelope(manifest, traces)

    assert result.pair_status == "blocked"
    assert any("pair_hash" in error for error in result.validation_errors)


def test_duplicate_run_id_or_evidence_path_cannot_inflate_denominator(tmp_path):
    manifest = _manifest(repeats=2)
    traces = _traces(manifest, tmp_path)
    second_with = tmp_path / "with-2.json"
    second_with.write_text(
        json.dumps(
            _raw(
                "pass",
                manifest=manifest,
                arm="with_skill",
                run_id="with-001",
                evidence_path=str(second_with),
                score=1.0,
            )
        ),
        encoding="utf-8",
    )
    traces.extend(
        [
            normalize_official_result(
                second_with,
                manifest=manifest,
                arm="with_skill",
                run_id="with-001",
                repeat_index=2,
                evidence_path=str(second_with),
            ),
            traces[1].model_copy(update={"repeat_index": 2}),
        ]
    )

    result = validate_pair_envelope(manifest, traces)

    assert result.denominator == 4
    assert result.pair_status == "blocked"
    assert any("run_id" in error or "evidence_path" in error for error in result.validation_errors)


def test_empty_or_non_digest_manifest_inputs_are_rejected():
    for task, config, verifier in [({}, _config(), _verifier()), (_task(), {}, _verifier()), (_task(), _config(), {})]:
        with pytest.raises(ValueError):
            build_pair_manifest(
                task=task,
                skill_artifact_hash=_digest("skill"),
                config=config,
                verifier=verifier,
                pair_id="pair-invalid",
            )

    with pytest.raises(ValueError, match="digest"):
        build_pair_manifest(
            task={**_task(), "task_digest": "not-a-digest"},
            skill_artifact_hash=_digest("skill"),
            config=_config(),
            verifier=_verifier(),
            pair_id="pair-invalid",
        )


def test_missing_score_or_reward_cannot_complete_pair(tmp_path):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    path = Path(traces[0].evidence_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("score")
    path.write_text(json.dumps(raw), encoding="utf-8")
    traces[0] = normalize_official_result(
        path,
        manifest=manifest,
        arm="with_skill",
        run_id="with-001",
        repeat_index=1,
        evidence_path=str(path),
    )

    result = validate_pair_envelope(manifest, traces)

    assert result.pair_status == "unknown"
    assert result.decision == "defer"
    assert result.score_delta is None
    assert result.scope == "development_fixture"
    assert "official results" not in result.reason.lower()


@pytest.mark.parametrize(
    "update",
    [
        {"status": "passed", "failure_class": "none", "score": 1.0},
        {"score": 0.9},
        {"latency_ms": 1.0},
        {"verifier_spans": [{"actual": True}]},
        {"unknown_reason": "rewritten"},
    ],
)
def test_normalized_outcome_cannot_diverge_from_persisted_record(tmp_path, update):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path, no_skill_status="fail")
    traces[1] = traces[1].model_copy(update=update)
    result = validate_pair_envelope(manifest, traces)
    assert result.decision == "defer"
    assert result.score_delta is None
    assert any("normalized" in error for error in result.validation_errors)


def test_nested_cost_mutation_is_rejected_without_changing_raw_file(tmp_path):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    traces[0].cost.usd = 0.0
    result = validate_pair_envelope(manifest, traces)
    assert result.decision == "defer"
    assert any("normalized cost" in error for error in result.validation_errors)


def test_reward_fallback_is_consistent_when_score_is_null(tmp_path):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    path = Path(traces[0].evidence_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(score=None, reward=1.0)
    path.write_text(json.dumps(raw), encoding="utf-8")
    traces[0] = normalize_official_result(
        path, manifest=manifest, arm="with_skill", run_id="with-001",
        repeat_index=1, evidence_path=str(path),
    )
    result = validate_pair_envelope(manifest, traces)
    assert result.pair_status == "completed"
    assert result.validation_errors == []


@pytest.mark.parametrize("field", ["latency_ms", "usd"])
def test_nonfinite_measurements_are_rejected(tmp_path, field):
    manifest = _manifest()
    traces = _traces(manifest, tmp_path)
    path = Path(traces[0].evidence_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if field == "usd":
        raw["cost"]["usd"] = float("inf")
    else:
        raw[field] = float("inf")
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        normalize_official_result(
            path, manifest=manifest, arm="with_skill", run_id="with-001",
            repeat_index=1, evidence_path=str(path),
        )
