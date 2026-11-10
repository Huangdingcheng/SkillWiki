from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path

import pytest

from skillwiki.layers.skill_runtime.harness.benchflow_evidence import (
    DEVELOPMENT_SCOPE,
    read_benchflow_evidence,
)
from skillwiki.layers.skill_runtime.harness.benchflow_runtime_evidence import (
    FIXED_WHEEL_SHA256,
    write_runtime_attestation,
)


def _write_fixture(root: Path, *, reward: object = 1.0, error: str | None = None, agent: str = "fake-agent") -> Path:
    rollout = root / "fake-rollout"
    (rollout / "verifier").mkdir(parents=True)
    (rollout / "prompts").mkdir()
    (rollout / "trajectory").mkdir()
    result = {
        "task_name": "fake-task",
        "task_id": "fake-task",
        "rollout_name": "fake-rollout",
        "rewards": {"reward": reward} if reward != "missing" else None,
        "agent": agent,
        "model": "fake-model-v1" if agent != "oracle" else None,
        "skill_mode": "with-skill",
        "sandbox": "docker",
        "benchflow_version": "0.6.3",
        "error": error,
        "error_category": "other" if error else None,
        "verifier_error": None,
        "agent_result": {"total_tokens": 12, "cost_usd": 0.25, "n_tool_calls": 2},
        "task_digest": "sha256:" + "a" * 64,
        "source": {"repo": "fake/bench", "resolved_sha": "b" * 40, "dirty": False},
    }
    config = {
        "task_path": "tasks/fake-task",
        "task_id": "fake-task",
        "agent": agent,
        "model": "fake-model-v1" if agent != "oracle" else None,
        "environment": "docker",
        "sandbox": "docker",
        "skill_mode": "with-skill",
        "benchflow_version": "0.6.3",
        "task_digest": "sha256:" + "a" * 64,
        "source": {"repo": "fake/bench", "resolved_sha": "b" * 40, "dirty": False},
    }
    (rollout / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (rollout / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (rollout / "verifier" / "reward.txt").write_text("1.0\n", encoding="utf-8")
    (rollout / "verifier" / "test-stdout.txt").write_text("fixture verifier\n", encoding="utf-8")
    (rollout / "trainer" ).mkdir()
    (rollout / "trainer" / "verifiers.jsonl").write_text(
        json.dumps({"task_id": "fake-task", "reward": reward if isinstance(reward, (int, float)) else None, "reward_valid": True}) + "\n",
        encoding="utf-8",
    )
    (rollout / "prompts" / "prompt.jsonl").write_text(json.dumps({"prompt": "fixture"}) + "\n", encoding="utf-8")
    (rollout / "trajectory" / "llm_trajectory.jsonl").write_text(json.dumps({"event": "fixture"}) + "\n", encoding="utf-8")
    (rollout / "timing.json").write_text(json.dumps({"duration_ms": 1}), encoding="utf-8")
    return rollout


def test_reader_preserves_observed_identity_and_raw_hashes(tmp_path):
    run_dir = _write_fixture(tmp_path)

    evidence = read_benchflow_evidence(
        run_dir,
        expected_task_digest="sha256:" + "a" * 64,
        expected_skill_mode="with-skill",
        scope=DEVELOPMENT_SCOPE,
    )

    assert evidence.scope == "development_fixture"
    assert evidence.status == "passed"
    assert evidence.score == 1.0
    assert evidence.observed["agent"] == "fake-agent"
    assert evidence.observed["model"] == "fake-model-v1"
    assert evidence.observed["skill_mode"] == "with-skill"
    assert evidence.observed["task_digest"] == "sha256:" + "a" * 64
    assert evidence.is_oracle is False
    assert evidence.model_claimable is False
    assert evidence.model_result_eligible is False
    assert evidence.raw_artifacts["result.json"]["sha256"]
    assert evidence.raw_artifacts["config.json"]["sha256"]
    assert evidence.raw_artifacts["verifier/reward.txt"]["sha256"]


def test_missing_reward_is_unknown_and_cost_stays_null(tmp_path):
    run_dir = _write_fixture(tmp_path, reward="missing")

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "unknown"
    assert evidence.score is None
    assert evidence.cost["usd"] == 0.25
    assert "reward" in evidence.unknown_reason.lower()


def test_verifier_error_and_nonfinite_reward_cannot_pass(tmp_path):
    run_dir = _write_fixture(tmp_path, reward=float("nan"))
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["verifier_error"] = "fixture verifier crashed"
    result_path.write_text(json.dumps(result, allow_nan=True), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "unknown"
    assert evidence.score is None
    assert "verifier" in evidence.unknown_reason.lower()
    assert math.isfinite(evidence.cost["usd"])


def test_expected_identity_mismatch_is_blocked_without_copying_expectation(tmp_path):
    run_dir = _write_fixture(tmp_path)

    evidence = read_benchflow_evidence(
        run_dir,
        expected_task_digest="sha256:" + "c" * 64,
        expected_skill_mode="no-skill",
        scope=DEVELOPMENT_SCOPE,
    )

    assert evidence.status == "blocked"
    assert evidence.observed["task_digest"] == "sha256:" + "a" * 64
    assert evidence.observed["skill_mode"] == "with-skill"
    assert evidence.expected_task_digest == "sha256:" + "c" * 64
    assert any("task_digest" in error for error in evidence.errors)
    assert any("skill_mode" in error for error in evidence.errors)


def test_oracle_is_not_model_claim_and_error_without_sandbox_is_blocked(tmp_path):
    run_dir = _write_fixture(tmp_path, reward="missing", error="locked sandbox path")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update({"agent": "oracle", "model": None, "skill_mode": "no-skill"})
    result_path.write_text(json.dumps(result), encoding="utf-8")
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update({"agent": "oracle", "model": None, "skill_mode": "no-skill"})
    config_path.write_text(json.dumps(config), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "blocked"
    assert evidence.is_oracle is True
    assert evidence.model_claimable is False
    assert evidence.score is None
    assert "sandbox" in evidence.unknown_reason.lower()


@pytest.mark.parametrize("field", ["task_id", "task_digest", "skill_mode", "agent", "model", "sandbox"])
def test_result_config_identity_conflict_is_blocked(tmp_path, field):
    run_dir = _write_fixture(tmp_path)
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config[field] = "conflicting-value"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "blocked"
    assert any(field in error and "conflict" in error for error in evidence.errors)


def test_native_runtime_version_is_observed_not_supported_constant(tmp_path):
    run_dir = _write_fixture(tmp_path)
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.pop("benchflow_version")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.pop("benchflow_version")
    config_path.write_text(json.dumps(config), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")

    assert evidence.reader_supported_version == "0.6.3"
    assert evidence.observed.get("benchflow_version") is None
    assert evidence.status == "unknown"
    assert "version" in evidence.unknown_reason.lower()


def test_unsupported_observed_runtime_version_cannot_pass_as_reader_version(tmp_path):
    run_dir = _write_fixture(tmp_path)
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["benchflow_version"] = "0.7.9"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["benchflow_version"] = "0.7.9"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")

    assert evidence.reader_supported_version == "0.6.3"
    assert evidence.observed["benchflow_version"] == "0.7.9"
    assert evidence.status == "unknown"
    assert "unsupported" in evidence.unknown_reason.lower()
    assert evidence.model_result_eligible is False


def test_legacy_mapping_surface_is_derived_from_dataclass(tmp_path):
    run_dir = _write_fixture(tmp_path, reward="missing")
    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence["observed"]["model"] == "fake-model-v1"
    assert evidence["result"]["status"] == "unknown"
    assert evidence["source"]["reader_supported_version"] == "0.6.3"
    assert evidence["cost"]["total_tokens"] == 12


def test_native_missing_verifier_sidecar_is_unknown(tmp_path):
    run_dir = _write_fixture(tmp_path)
    (run_dir / "trainer" / "verifiers.jsonl").unlink()

    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")

    assert evidence.status == "unknown"
    assert "sidecar" in evidence.unknown_reason.lower()


@pytest.mark.parametrize("mode", ["corrupt", "reward_invalid", "reward_mismatch", "identity_mismatch"])
def test_native_verifier_sidecar_integrity_is_fail_closed(tmp_path, mode):
    run_dir = _write_fixture(tmp_path)
    sidecar = run_dir / "trainer" / "verifiers.jsonl"
    row = json.loads(sidecar.read_text(encoding="utf-8"))
    if mode == "corrupt":
        sidecar.write_text("not-json\n", encoding="utf-8")
    else:
        if mode == "reward_invalid":
            row["reward_valid"] = False
        elif mode == "reward_mismatch":
            row["reward"] = 0.0
        else:
            row["task_id"] = "other-task"
        sidecar.write_text(json.dumps(row) + "\n", encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")

    assert evidence.status in {"unknown", "blocked"}
    assert evidence.model_result_eligible is False


def test_native_sidecar_reads_nested_info_reward_valid(tmp_path, monkeypatch):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    sidecar = run_dir / "trainer" / "verifiers.jsonl"
    sidecar.write_text(
        json.dumps({"info": {"task_id": "fake-task", "reward_valid": True, "reward_metadata": {"reward": 1.0}}}) + "\n",
        encoding="utf-8",
    )
    _write_attestation(run_dir, tmp_path, monkeypatch)
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "passed"
    assert evidence.model_result_eligible is True


def test_model_null_cannot_fallback_to_config_model(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["model"] = None
    result_path.write_text(json.dumps(result), encoding="utf-8")
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "blocked"
    assert evidence.model_result_eligible is False


def test_valid_zero_reward_remains_model_eligible_for_denominator(tmp_path, monkeypatch):
    run_dir = _write_fixture(tmp_path, reward=0.0, agent="test-agent")
    _write_attestation(run_dir, tmp_path, monkeypatch)
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "failed"
    assert evidence.score == 0.0
    assert evidence.model_result_eligible is True


def test_task_name_task_id_conflict_is_blocked(tmp_path):
    run_dir = _write_fixture(tmp_path)
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["task_name"] = "other-task"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)
    assert evidence.status == "blocked"
    assert any("task_name" in error for error in evidence.errors)


def test_declared_fixture_scope_cannot_be_read_as_native(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["scope"] = DEVELOPMENT_SCOPE
    result_path.write_text(json.dumps(result), encoding="utf-8")
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "blocked"
    assert evidence.model_result_eligible is False


def test_fixture_scope_marker_cannot_be_read_as_native(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["fixture_scope"] = DEVELOPMENT_SCOPE
    result_path.write_text(json.dumps(result), encoding="utf-8")
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "blocked"
    assert evidence.model_result_eligible is False


def test_result_config_scope_conflict_is_blocked(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["scope"] = "native_runtime"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["scope"] = DEVELOPMENT_SCOPE
    config_path.write_text(json.dumps(config), encoding="utf-8")
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "blocked"
    assert evidence.model_result_eligible is False


def test_task_name_without_result_task_id_cannot_fallback_to_config(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.pop("task_id")
    result["task_name"] = "different-task"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "blocked"
    assert evidence.model_result_eligible is False


def test_whitespace_model_is_not_a_real_model_identity(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["model"] = "   "
    result_path.write_text(json.dumps(result), encoding="utf-8")
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["model"] = "   "
    config_path.write_text(json.dumps(config), encoding="utf-8")
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.model_result_eligible is False


def test_native_reward_artifact_is_required(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    (run_dir / "verifier" / "reward.txt").unlink()
    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")
    assert evidence.status == "unknown"
    assert "reward artifact" in evidence.unknown_reason
def test_native_source_manifest_summary_is_observed(tmp_path, monkeypatch):
    run_dir = _write_fixture(tmp_path)
    _write_attestation(run_dir, tmp_path, monkeypatch)

    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")

    assert evidence.status == "passed"
    assert evidence.observed["source_manifest"] == {
        "repo": "fake/bench",
        "resolved_sha": "b" * 40,
        "commit": None,
        "dirty": False,
        "task_digest": "sha256:" + "a" * 64,
    }
    assert evidence.model_result_eligible is True


def test_native_missing_source_manifest_is_blocked(tmp_path):
    run_dir = _write_fixture(tmp_path)
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.pop("source")
    config_path.write_text(json.dumps(config), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")

    assert evidence.status == "blocked"
    assert any("source manifest" in error for error in evidence.errors)


def test_native_task_source_sidecar_can_bind_immutable_result_and_config(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    config_path = run_dir / "config.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    result.pop("source", None)
    config.pop("source", None)
    result_path.write_text(json.dumps(result), encoding="utf-8")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    digest = result["task_digest"]
    (run_dir / "task-source-evidence.json").write_text(
        json.dumps({
            "schema": "skillwiki.m2.task-source-evidence.v1",
            "task_id": result["task_name"],
            "repo": "https://github.com/benchflow-ai/skillsbench",
            "resolved_sha": "9a1f4dd5f7659f75707435da3ce854b6e48321d1",
            "dirty": False,
            "task_digest": digest,
            "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        }),
        encoding="utf-8",
    )

    evidence = read_benchflow_evidence(
        run_dir, scope="native_runtime", expected_task_digest=digest
    )

    assert not evidence.errors
    assert evidence.observed["task_source_evidence"]["resolved_sha"] == "9a1f4dd5f7659f75707435da3ce854b6e48321d1"


def test_native_task_source_sidecar_hash_mismatch_is_blocked(tmp_path):
    run_dir = _write_fixture(tmp_path, agent="test-agent")
    result_path = run_dir / "result.json"
    config_path = run_dir / "config.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    result.pop("source", None)
    config.pop("source", None)
    result_path.write_text(json.dumps(result), encoding="utf-8")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (run_dir / "task-source-evidence.json").write_text(
        json.dumps({
            "schema": "skillwiki.m2.task-source-evidence.v1",
            "task_id": result["task_name"],
            "repo": "https://github.com/benchflow-ai/skillsbench",
            "resolved_sha": "9a1f4dd5f7659f75707435da3ce854b6e48321d1",
            "dirty": False,
            "task_digest": result["task_digest"],
            "result_sha256": "0" * 64,
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        }),
        encoding="utf-8",
    )

    evidence = read_benchflow_evidence(run_dir, scope="native_runtime")

    assert evidence.status == "blocked"
    assert any("result.json hash mismatch" in error for error in evidence.errors)


def test_development_source_manifest_conflict_is_blocked(tmp_path):
    run_dir = _write_fixture(tmp_path)
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["source"]["resolved_sha"] = "c" * 40
    config_path.write_text(json.dumps(config), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "blocked"
    assert any("source manifest" in error for error in evidence.errors)


def test_development_missing_prompt_trajectory_timing_is_unknown(tmp_path):
    run_dir = _write_fixture(tmp_path)
    (run_dir / "prompts" / "prompt.jsonl").unlink()
    (run_dir / "trajectory" / "llm_trajectory.jsonl").unlink()
    (run_dir / "timing.json").unlink()

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "unknown"
    assert "prompts" in evidence.unknown_reason
    assert "trajectory" in evidence.unknown_reason
    assert "timing" in evidence.unknown_reason
    assert evidence.evidence_completeness["prompts"]["present"] is False


def test_development_unparseable_trajectory_is_unknown(tmp_path):
    run_dir = _write_fixture(tmp_path)
    (run_dir / "trajectory" / "llm_trajectory.jsonl").write_text("not-json\n", encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "unknown"
    assert "trajectory" in evidence.unknown_reason
    assert evidence.evidence_completeness["trajectory"]["parseable"] is False


def test_nested_result_config_files_are_ambiguous_and_blocked(tmp_path):
    run_dir = _write_fixture(tmp_path)
    nested = run_dir / "nested"
    nested.mkdir()
    (nested / "result.json").write_text("{}", encoding="utf-8")
    (nested / "config.json").write_text("{}", encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir, scope=DEVELOPMENT_SCOPE)

    assert evidence.status == "blocked"
    assert any("ambiguous nested result/config" in error for error in evidence.errors)


def _write_attestation(run_dir: Path, tmp_path: Path, monkeypatch, *, version: str = "0.6.3"):
    wheel = tmp_path / "benchflow-0.6.3-py3-none-any.whl"
    wheel.write_bytes(b"synthetic fixed wheel bytes")

    class Distribution:
        @staticmethod
        def locate_file(_name):
            return tmp_path / "venv" / "site-packages"

    Distribution.version = version

    monkeypatch.setattr(
        "skillwiki.layers.skill_runtime.harness.benchflow_runtime_evidence.importlib_metadata.distribution",
        lambda _package: Distribution(),
    )
    payload = write_runtime_attestation(run_dir, wheel_path=wheel)
    payload["wheel_sha256"] = FIXED_WHEEL_SHA256
    (run_dir / "runtime-evidence.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_native_runtime_attestation_binds_observed_version_and_raw_bytes(tmp_path, monkeypatch):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    payload = _write_attestation(run_dir, tmp_path, monkeypatch)

    evidence = read_benchflow_evidence(run_dir, expected_runtime_version="0.6.3")

    assert evidence.status == "passed"
    assert evidence.runtime_attestation_valid is True
    assert evidence.observed["benchflow_version"] == "0.6.3"
    assert evidence.observed["benchflow_version_origin"] == "runtime_attestation"
    assert evidence.observed["runtime_attestation"]["result_sha256"] == payload["result_sha256"]
    assert evidence.model_result_eligible is True
    assert evidence.raw_artifacts["runtime-evidence.json"]["sha256"]


def test_current_runtime_attestation_binds_current_version_and_raw_bytes(tmp_path, monkeypatch):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    for filename in ("result.json", "config.json"):
        path = run_dir / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["benchflow_version"] = "0.7.9.dev0"
        path.write_text(json.dumps(payload), encoding="utf-8")

    wheel = tmp_path / "benchflow-0.7.9.dev0-py3-none-any.whl"
    wheel.write_bytes(b"synthetic current wheel bytes")

    class Distribution:
        version = "0.7.9.dev0"

        @staticmethod
        def locate_file(_name):
            return tmp_path / "venv" / "site-packages"

    monkeypatch.setattr(
        "skillwiki.layers.skill_runtime.harness.benchflow_runtime_evidence.importlib_metadata.distribution",
        lambda _package: Distribution(),
    )
    write_runtime_attestation(run_dir, wheel_path=wheel)

    evidence = read_benchflow_evidence(run_dir, expected_runtime_version="0.7.9.dev0")

    assert evidence.status == "passed"
    assert evidence.runtime_attestation_valid is True
    assert evidence.observed["benchflow_version"] == "0.7.9.dev0"
    assert evidence.model_result_eligible is True


def test_current_runtime_identity_mismatch_is_not_promoted(tmp_path, monkeypatch):
    """Historical 0.6.3 evidence cannot satisfy a current 0.7.9 contract."""
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    _write_attestation(run_dir, tmp_path, monkeypatch, version="0.6.3")

    evidence = read_benchflow_evidence(run_dir, expected_runtime_version="0.7.9.dev0")

    assert evidence.status == "blocked"
    assert evidence.model_result_eligible is False
    assert evidence.runtime_attestation_valid is True
    assert any("benchflow_version mismatch" in error for error in evidence.errors)


@pytest.mark.parametrize(
    "tamper", ["result", "config", "run_id", "run_path", "schema", "version", "wheel_hash"]
)
def test_native_runtime_attestation_counterexamples_fail_closed(tmp_path, monkeypatch, tamper):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    _write_attestation(run_dir, tmp_path, monkeypatch)
    attestation_path = run_dir / "runtime-evidence.json"
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    if tamper == "result":
        result_path = run_dir / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["rollout_name"] = "tampered"
        result_path.write_text(json.dumps(result), encoding="utf-8")
    elif tamper == "config":
        config_path = run_dir / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["task_path"] = "tasks/other-task"
        config_path.write_text(json.dumps(config), encoding="utf-8")
    elif tamper == "run_id":
        attestation["run_id"] = "other-run"
    elif tamper == "run_path":
        attestation["run_path"] = str(tmp_path / "other-run")
    elif tamper == "schema":
        attestation["schema_version"] = "bad-schema"
    elif tamper == "wheel_hash":
        attestation["wheel_sha256"] = "0" * 64
    else:
        attestation["benchflow_version"] = "0.7.9"
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")

    evidence = read_benchflow_evidence(run_dir)

    assert evidence.status == "blocked"
    assert evidence.model_result_eligible is False
    assert evidence.runtime_attestation_valid is False


def test_runtime_attestation_is_exclusive_and_does_not_overwrite(tmp_path, monkeypatch):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    _write_attestation(run_dir, tmp_path, monkeypatch)
    before = (run_dir / "runtime-evidence.json").read_bytes()

    with pytest.raises(FileExistsError):
        write_runtime_attestation(
            run_dir,
            wheel_path=tmp_path / "benchflow-0.6.3-py3-none-any.whl",
        )

    assert (run_dir / "runtime-evidence.json").read_bytes() == before


def test_native_runtime_version_conflict_cannot_be_overridden_by_attestation(tmp_path, monkeypatch):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    for filename in ("result.json", "config.json"):
        path = run_dir / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["benchflow_version"] = "0.7.9"
        path.write_text(json.dumps(payload), encoding="utf-8")
    _write_attestation(run_dir, tmp_path, monkeypatch)

    evidence = read_benchflow_evidence(run_dir)

    assert evidence.status == "blocked"
    assert evidence.observed["benchflow_version"] is None
    assert any("runtime attestation version" in error for error in evidence.errors)

def _write_upstream_shaped_fixture(root: Path) -> Path:
    """Synthetic test data matching native writer keys, never research evidence."""
    run_dir = _write_fixture(root)
    for filename in ('result.json', 'config.json'):
        path = run_dir / filename
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload.pop('task_id')
        payload.pop('benchflow_version')
        path.write_text(json.dumps(payload), encoding='utf-8')
    (run_dir / 'prompts' / 'prompt.jsonl').unlink()
    (run_dir / 'prompts.json').write_text(json.dumps(['native-shaped synthetic prompt']), encoding='utf-8')
    (run_dir / 'trainer' / 'verifiers.jsonl').write_text(
        json.dumps({'reward': 1.0, 'info': {'task_id': 'fake-task', 'reward_valid': True}}) + '\n',
        encoding='utf-8',
    )
    return run_dir


def test_native_writer_alias_is_cross_checked_but_version_stays_unknown(tmp_path):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    before = {name: (run_dir / name).read_bytes() for name in ('result.json', 'config.json')}
    evidence = read_benchflow_evidence(run_dir, expected_task_id='fake-task', expected_runtime_version='0.6.3')
    assert evidence.observed['task_id'] == 'fake-task'
    assert evidence.observed['task_id_origin'] == 'result.task_name+config.task_path'
    assert evidence.observed['benchflow_version'] is None
    assert evidence.status == 'unknown'
    assert 'runtime version is unverified' in evidence.unknown_reason
    assert evidence.sidecar_valid is True
    assert evidence.model_result_eligible is False
    assert evidence.evidence_completeness['prompts']['paths'] == ['prompts.json']
    assert evidence.evidence_completeness['prompts']['parseable'] is True
    assert before == {name: (run_dir / name).read_bytes() for name in before}


@pytest.mark.parametrize('path_value', ['tasks/wrong-task', None, '../fake-task', 12])
def test_native_task_name_cannot_mask_missing_or_conflicting_path(tmp_path, path_value):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    path = run_dir / 'config.json'
    config = json.loads(path.read_text(encoding='utf-8'))
    config['task_path'] = path_value
    path.write_text(json.dumps(config), encoding='utf-8')
    evidence = read_benchflow_evidence(run_dir, expected_task_id='fake-task')
    assert evidence.status == 'blocked'
    assert evidence.observed['task_id'] is None
    assert evidence.model_result_eligible is False


@pytest.mark.parametrize('label', ['result.json', 'config.json'])
def test_native_alias_does_not_replace_explicit_null_identity(tmp_path, label):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    path = run_dir / label
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['task_id'] = None
    path.write_text(json.dumps(payload), encoding='utf-8')
    evidence = read_benchflow_evidence(run_dir)
    assert evidence.status == 'blocked'
    assert evidence.observed['task_id'] is None


def test_native_sidecar_cannot_hide_conflicting_nested_task_id(tmp_path):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    path = run_dir / 'trainer' / 'verifiers.jsonl'
    path.write_text(json.dumps({'task_id': 'fake-task', 'reward': 1.0, 'info': {
        'task_id': 'forged-other-task', 'reward_valid': True}}) + '\n', encoding='utf-8')
    evidence = read_benchflow_evidence(run_dir)
    assert evidence.sidecar_valid is False
    assert evidence.model_result_eligible is False
    assert 'conflicting task_id fields' in evidence.unknown_reason


def test_native_alias_and_external_runner_sidecar_cannot_supply_provenance(tmp_path):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    for filename in ('result.json', 'config.json'):
        path = run_dir / filename
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload.pop('source')
        path.write_text(json.dumps(payload), encoding='utf-8')
    (run_dir / 'runner-inputs.json').write_text(json.dumps({
        'benchflow_version': '0.6.3', 'source': {'repo': 'fake/bench', 'resolved_sha': 'b' * 40,
        'dirty': False, 'task_digest': 'sha256:' + 'a' * 64}}), encoding='utf-8')
    evidence = read_benchflow_evidence(run_dir, expected_runtime_version='0.6.3')
    assert evidence.status == 'blocked'
    assert evidence.observed['source_manifest'] is None
    assert evidence.observed['benchflow_version'] is None
    assert evidence.model_result_eligible is False
    assert any('source manifest is missing' in error for error in evidence.errors)


def test_native_alias_does_not_relax_canonical_digest_check(tmp_path):
    run_dir = _write_upstream_shaped_fixture(tmp_path)
    evidence = read_benchflow_evidence(run_dir, expected_task_digest='sha256:' + 'c' * 64)
    assert evidence.status == 'blocked'
    assert evidence.observed['task_digest'] == 'sha256:' + 'a' * 64
    assert any('task_digest mismatch' in error for error in evidence.errors)
