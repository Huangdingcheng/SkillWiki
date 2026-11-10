"""Read-only registry for the bounded historical BenchFlow pilot runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .benchflow_evidence import read_benchflow_evidence


_WORKSPACE_ROOT = Path(__file__).resolve().parents[5]
_ARTIFACT_ROOT = _WORKSPACE_ROOT / "benchflow-c-int-023-b-20261021" / "artifacts"
_TASK_ID = "dialogue-parser"
_TASK_DIGEST = "sha256:e9ec9d22f9d2b9252915e2a18089a58184b1e3f9e6ff6399577b3fb28539970f"
_RUNTIME_VERSION = "0.7.9.dev0"
_ALLOWED_ARTIFACTS = {
    "result.json",
    "config.json",
    "timing.json",
    "verifier/reward.txt",
    "verifier/pytest_output.txt",
    "verifier/test-stdout.txt",
    "verifier/ctrf.json",
    "trainer/verifiers.jsonl",
}
_PILOTS = (
    {
        "run_id": "pilot-03/2026-10-22__08-14-37/dialogue-parser__49a72df3",
        "label": "No-skill baseline",
    },
    {
        "run_id": "pilot-06/2026-10-22__08-47-15/dialogue-parser__5cd47c6c",
        "label": "Original dialogue-graph skill",
    },
    {
        "run_id": "pilot-07/2026-10-22__09-01-39/dialogue-parser__831f0756",
        "label": "SkillWiki managed source-bound package",
    },
)


def list_historical_benchflow_runs() -> list[dict[str, Any]]:
    return [_build_record(item) for item in _PILOTS if _run_path(item["run_id"]).is_dir()]


def get_historical_benchflow_run(run_id: str) -> dict[str, Any] | None:
    for item in _PILOTS:
        if item["run_id"] == run_id and _run_path(run_id).is_dir():
            return _build_record(item)
    return None


def _run_path(run_id: str) -> Path:
    path = (_ARTIFACT_ROOT / Path(*run_id.split("/"))).resolve()
    if _ARTIFACT_ROOT.resolve() not in path.parents:
        raise ValueError("historical BenchFlow run escaped the bounded artifact root")
    return path


def _build_record(item: dict[str, str]) -> dict[str, Any]:
    run_id = item["run_id"]
    root = _run_path(run_id)
    evidence = read_benchflow_evidence(
        root,
        expected_task_id=_TASK_ID,
        expected_task_digest=_TASK_DIGEST,
        expected_runtime_version=_RUNTIME_VERSION,
    )
    observed = evidence.observed
    reward = evidence.rewards.get("reward") if isinstance(evidence.rewards, dict) else None
    limitations = [
        "Historical run is retained for observation; it is not a current promotion receipt.",
        "result.json and config.json do not contain the required source manifest.",
        "No bound SkillWiki skill_id/version is recorded for this run.",
        "BenchFlow runtime attestation is absent, so the exact runtime is not proven by the run files.",
        "Model result eligibility and promotion are disabled until a fresh attested run is recorded.",
    ]
    if evidence.errors:
        limitations.extend(error for error in evidence.errors if error not in limitations)
    return {
        "run_id": run_id,
        "label": item["label"],
        "evidence_kind": "historical_real_run",
        "task_id": observed.get("task_id") or _TASK_ID,
        "task_digest": observed.get("task_digest") or _TASK_DIGEST,
        "skill_mode": observed.get("skill_mode") or "unknown",
        "agent": observed.get("agent") or "unknown",
        "requested_model": observed.get("model") or "unknown",
        "sandbox": observed.get("sandbox") or "docker",
        "observed_reward": reward if isinstance(reward, (int, float)) and not isinstance(reward, bool) else None,
        "admission_status": "blocked" if evidence.status == "blocked" else evidence.status,
        "admission_reason": evidence.unknown_reason or "Historical evidence has not been admitted for current use.",
        "evidence_integrity": "missing" if not evidence.runtime_attestation_valid else "verified",
        "model_result_eligible": False,
        "promotion_allowed": False,
        "skill_binding": None,
        "cost": evidence.cost,
        "raw_artifacts": _raw_artifact_refs(root),
        "runtime_attestation_valid": evidence.runtime_attestation_valid,
        "source_manifest_present": False,
        "limitations": limitations,
    }


def _raw_artifact_refs(root: Path) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    run_prefix = root.relative_to(_ARTIFACT_ROOT).as_posix()
    for relative in sorted(_ALLOWED_ARTIFACTS):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        import hashlib

        refs.append(
            {
                "path": f"{run_prefix}/{relative}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    return refs
