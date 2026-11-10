"""Minimal official GEPA adapter for maintainer candidate search.

This module only exercises the upstream SDK contract. It does not create a
skills_dir package or claim verifier quality for a text candidate.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence


GEPA_IDENTITY = {
    "repository": "https://github.com/gepa-ai/gepa",
    "commit": "d771eb21b5dd3228bc3f567293d2ccfc423fc900",
    "version": "0.1.4",
    "license": "MIT",
    "entrypoint": "gepa.optimize",
    "adapter_protocol": "gepa.core.adapter.GEPAAdapter",
}


@dataclass
class GepaCandidateRecord:
    candidate_text: str
    candidate_component: str
    input_failure_trace: List[Dict[str, Any]]
    budget: Dict[str, Any]
    lineage: Dict[str, Any]
    task_contract: Dict[str, Any]
    environment_identity: Dict[str, Any]
    sdk_identity: Dict[str, Any] = field(default_factory=lambda: dict(GEPA_IDENTITY))
    status: str = "candidate_recorded"
    blocked_reason: str = ""
    sdk_result: Dict[str, Any] = field(default_factory=dict)

    @property
    def component_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.candidate_text.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_text": self.candidate_text,
            "candidate_component": self.candidate_component,
            "candidate_hash": self.component_hash,
            "input_failure_trace": self.input_failure_trace,
            "budget": self.budget,
            "lineage": self.lineage,
            "task_contract": self.task_contract,
            "environment_identity": self.environment_identity,
            "sdk_identity": self.sdk_identity,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "sdk_result": self.sdk_result,
        }


def candidate_admission(candidate: Mapping[str, Any]) -> tuple[str, str]:
    """Require a complete executable package before any success is reported."""
    required = ("implementation", "entrypoint", "manifest", "modes", "license_scope")
    missing = [key for key in required if not candidate.get(key)]
    if missing:
        return "blocked_candidate_not_executable", "missing executable candidate fields: " + ", ".join(missing)
    return "admissible", ""


def candidate_package_admission(package_root: str | Path) -> tuple[str, str, Dict[str, Any]]:
    """Admit only a real, hash-bound implementation package."""
    root = Path(package_root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return "blocked_candidate_not_executable", "manifest.json is missing", {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "blocked_candidate_not_executable", f"invalid manifest: {exc}", {}
    required = ("implementation", "entrypoint", "modes", "license_scope", "lineage", "files")
    missing = [key for key in required if not manifest.get(key)]
    if missing:
        return "blocked_candidate_not_executable", "missing package fields: " + ", ".join(missing), manifest
    license_scope = manifest["license_scope"]
    if not isinstance(license_scope, Mapping) or license_scope.get("status") != "verified":
        return "blocked_candidate_license_scope_unresolved", "candidate implementation license scope is not verified", manifest
    implementation = root / str(manifest["implementation"])
    if not implementation.is_file():
        return "blocked_candidate_not_executable", "implementation file is missing", manifest
    entrypoint = str(manifest["entrypoint"])
    if ":" not in entrypoint:
        return "blocked_candidate_not_executable", "entrypoint must use module:function form", manifest
    observed = []
    for item in manifest["files"]:
        path = root / str(item["path"])
        if not path.is_file():
            return "blocked_candidate_not_executable", f"manifest file is missing: {item['path']}", manifest
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item.get("sha256"):
            return "blocked_candidate_not_executable", f"content hash mismatch: {item['path']}", manifest
        observed.append(str(item["path"]))
    return "admissible", "", {**manifest, "observed_files": observed}


def run_gepa_candidate_search(
    *,
    seed_text: str,
    proposed_text: str,
    failure_trace: Sequence[Mapping[str, Any]],
    budget: Mapping[str, Any],
    lineage: Mapping[str, Any],
    task_contract: Mapping[str, Any],
    environment_identity: Mapping[str, Any],
) -> GepaCandidateRecord:
    """Run the official SDK with a deterministic, no-model adapter."""
    import gepa
    from gepa.core.adapter import EvaluationBatch

    class DeterministicAdapter:
        def evaluate(self, batch, candidate, capture_traces=False):
            outputs, scores, traces = [], [], []
            for item in batch:
                score = float(candidate["skill_text"] == proposed_text)
                output = {"id": item["id"], "score": score}
                trace = {"id": item["id"], "score": score, "candidate": dict(candidate)}
                outputs.append(output)
                scores.append(score)
                traces.append(trace)
            return EvaluationBatch(
                outputs=outputs,
                scores=scores,
                trajectories=traces if capture_traces else None,
                num_metric_calls=len(batch),
            )

        def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
            rows = [
                {"Inputs": {"id": row["id"]}, "Generated Outputs": row, "Feedback": "deterministic contract probe"}
                for row in (eval_batch.trajectories or [])
            ]
            return {component: rows for component in components_to_update}

        def propose_new_texts(self, candidate, reflective_dataset, components_to_update, **kwargs):
            return {component: proposed_text for component in components_to_update}

    max_metric_calls = int(budget.get("max_metric_calls", 4))
    trainset = [{"id": "maintainer-train-1"}]
    valset = [{"id": "maintainer-val-1"}]
    kwargs: Dict[str, Any] = {
        "seed_candidate": {"skill_text": seed_text},
        "trainset": trainset,
        "valset": valset,
        "adapter": DeterministicAdapter(),
        "reflection_minibatch_size": 1,
        "max_metric_calls": max_metric_calls,
        "display_progress_bar": False,
        "seed": int(budget.get("seed", 0)),
        "skip_perfect_score": False,
    }
    if "write_agent_state" in inspect.signature(gepa.optimize).parameters:
        kwargs["write_agent_state"] = False
    result = gepa.optimize(**kwargs)
    candidate_text = str(result.best_candidate.get("skill_text") or "")
    status, reason = candidate_admission({"skill_text": candidate_text})
    return GepaCandidateRecord(
        candidate_text=candidate_text,
        candidate_component="skill_text",
        input_failure_trace=[dict(item) for item in failure_trace],
        budget=dict(budget),
        lineage=dict(lineage),
        task_contract=dict(task_contract),
        environment_identity=dict(environment_identity),
        status=status,
        blocked_reason=reason,
        sdk_result={
            "gepa_version": importlib.metadata.version("gepa"),
            "best_candidate": result.best_candidate,
            "best_idx": result.best_idx,
            "num_candidates": result.num_candidates,
            "total_metric_calls": result.total_metric_calls,
            "val_aggregate_scores": result.val_aggregate_scores,
            "model_call": False,
        },
    )
