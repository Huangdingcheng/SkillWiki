"""Read-only registry for fresh, receipt-bound BenchFlow task runs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .benchflow_evidence import read_benchflow_evidence

_WORKSPACE_ROOT = Path(__file__).resolve().parents[5]
_REGISTRY_ROOT = _WORKSPACE_ROOT / "artifacts" / "20261024-m2-m5-final" / "registrations"
_ARTIFACT_ROOT = _WORKSPACE_ROOT / "artifacts" / "20261024-m2-m5-final"
_RECORD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_FORMAL_BATCH = "m2-m5-recovery-20261024"
_FORMAL_CONDITIONS = (
    ("R1", "no-skill"),
    ("R2", "legacy-old-direct"),
    ("R3", "new-upstream"),
    ("R4", "new-upstream-plus-skillwiki"),
    ("R5", "skillopt-direct"),
    ("R6", "skillopt-managed"),
)
_RAW_FILES = (
    "result.json",
    "config.json",
    "timing.json",
    "summary.json",
    "results.jsonl",
    "rewards.jsonl",
    "verifiers.jsonl",
    "trajectory/llm_trajectory.jsonl",
    "trajectory/acp_trajectory.jsonl",
    "agent/acp_trajectory.jsonl",
    "verifier/reward.txt",
    "verifier/pytest_output.txt",
    "verifier/test-stdout.txt",
    "verifier/ctrf.json",
    "trainer/verifiers.jsonl",
    "budget-gate.log",
    "runtime-evidence.json",
    "runtime-source-evidence.json",
    "runtime-source-measurement.json",
    "task-source-evidence.json",
    "trajectory/acp_trajectory.abandoned.jsonl",
)


def list_current_benchflow_runs() -> list[dict[str, Any]]:
    if not _REGISTRY_ROOT.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(_REGISTRY_ROOT.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        record = _read_registration(path)
        if record is not None:
            records.append(record)
    return records


def get_current_benchflow_run(record_id: str) -> dict[str, Any] | None:
    if not _RECORD_ID.fullmatch(record_id):
        return None
    path = _REGISTRY_ROOT / f"{record_id}.json"
    if path.is_symlink() or not path.is_file():
        return None
    return _read_registration(path)


def formal_benchflow_matrix(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Six fixed conditions; attempts remain separate and never fill other slots."""
    rows = []
    for slot, condition in _FORMAL_CONDITIONS:
        attempts = [record for record in records
                    if record.get("batch_id") == _FORMAL_BATCH
                    and record.get("formal_slot") == slot
                    and record.get("condition") == condition]
        def started(record: dict[str, Any]) -> tuple[str, str]:
            try:
                config = json.loads((_resolve_run_dir(record.get("run_path")) / "config.json").read_text(encoding="utf-8"))
                return str(config.get("started_at", "")), str(record["run_id"])
            except (OSError, ValueError, json.JSONDecodeError):
                return "", str(record.get("run_id", ""))
        selected = max(attempts, key=started) if attempts else None
        rows.append({
            "formal_slot": slot, "condition": condition,
            "status": selected.get("status", "blocked") if selected else "not_run",
            "run_id": selected.get("run_id") if selected else None,
            "attempt_count": len(attempts),
            "observed_reward": selected.get("observed_reward") if selected else None,
            "verifier_ran": bool(selected and selected.get("verifier_ran")),
            "evidence_integrity": selected.get("evidence_integrity") if selected else None,
            "model_result_eligible": False, "promotion_allowed": False,
            "reason": "Latest registered attempt for this exact formal condition; earlier attempts remain listed separately." if selected else "No registered attempt for this formal condition.",
        })
    return rows


def register_current_benchflow_run(record: dict[str, Any]) -> dict[str, Any]:
    """Create an immutable receipt after the native run has stopped."""
    record_id = record.get("run_id")
    if not isinstance(record_id, str) or not _RECORD_ID.fullmatch(record_id):
        raise ValueError("invalid BenchFlow receipt run_id")
    run_dir = _resolve_run_dir(record.get("run_path"))
    hashes = _hash_raw_files(run_dir)
    status = record.get("status")
    if not hashes.get("config.json"):
        raise ValueError("native BenchFlow config.json is required")
    if status == "completed" and not hashes.get("result.json"):
        raise ValueError("completed BenchFlow receipt requires result.json")
    payload = {**record, "raw_artifacts": hashes}
    _REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
    target = _REGISTRY_ROOT / f"{record_id}.json"
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return _read_registration(target) or payload


def _read_registration(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("run_id") != path.stem:
        return None
    try:
        run_dir = _resolve_run_dir(payload.get("run_path"))
    except ValueError:
        return {**payload, "evidence_integrity": "mismatch", "integrity_errors": ["run path outside evidence root"]}
    expected = payload.get("raw_artifacts")
    actual = _hash_raw_files(run_dir)
    errors: list[str] = []
    if not isinstance(expected, dict):
        errors.append("receipt raw_artifacts is missing")
    else:
        for name, digest in expected.items():
            if actual.get(name, {}).get("sha256") != digest.get("sha256"):
                errors.append(f"raw evidence hash mismatch: {name}")
        if set(expected) != set(actual):
            errors.append("raw evidence file set changed after receipt")
    observed = read_benchflow_evidence(
        run_dir,
        expected_task_id=payload.get("task_id"),
        expected_task_digest=payload.get("task_digest"),
        expected_runtime_version=payload.get("runtime_version"),
        expected_skill_mode=payload.get("skill_mode"),
    )
    if observed.errors:
        errors.extend(error for error in observed.errors if error not in errors)
    integrity = "verified" if not errors else "mismatch"
    status = payload.get("status")
    if status == "completed" and observed.status not in {"completed", "passed"}:
        status = observed.status
    if errors and status == "completed":
        status = "integrity_mismatch"
    verifier_reward = _verified_terminal_reward(run_dir, observed, actual, errors)
    return {
        **payload,
        "status": status,
        "observed_reward": verifier_reward,
        "observed_model": observed.observed.get("model"),
        "observed_agent": observed.observed.get("agent"),
        "observed_skill_mode": observed.observed.get("skill_mode"),
        "observed_runtime_version": observed.observed.get("benchflow_version"),
        "observed_cost": observed.cost,
        "verifier_ran": verifier_reward is not None,
        "evidence_integrity": integrity,
        "integrity_errors": errors,
        "model_result_eligible": False,
        "promotion_allowed": False,
    }


def _verified_terminal_reward(run_dir: Path, observed: Any, artifacts: dict, errors: list) -> float | int | None:
    """Expose a real verifier outcome without granting model eligibility.

    The research reader suppresses scores after agent errors. The product still
    preserves an actual verifier zero and the separate failed agent outcome.
    """
    if errors or not observed.sidecar_valid:
        return None
    if not all(artifacts.get(name) for name in ("result.json", "verifier/reward.txt", "verifier/ctrf.json")):
        return None
    if observed.score is not None:
        return observed.score
    if not getattr(observed, "runtime_attestation_valid", False) or getattr(observed, "verifier_error", None):
        return None
    rewards = getattr(observed, "rewards", None)
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if type(reward) not in (int, float) or not math.isfinite(reward):
        return None
    try:
        file_reward = float((run_dir / "verifier/reward.txt").read_text(encoding="utf-8").strip())
        report = json.loads((run_dir / "verifier/ctrf.json").read_text(encoding="utf-8"))
        tests = report.get("results", {}).get("tests")
        if file_reward != reward or not isinstance(tests, list) or not tests:
            return None
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return reward


def _resolve_run_dir(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("run_path is required")
    root = _ARTIFACT_ROOT.resolve()
    path = Path(value).resolve()
    if root not in path.parents or path.is_symlink() or not path.is_dir():
        raise ValueError("run_path must be a real directory under the final evidence root")
    return path


def _hash_raw_files(run_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for relative in _RAW_FILES:
        path = run_dir / relative
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        result[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return result
