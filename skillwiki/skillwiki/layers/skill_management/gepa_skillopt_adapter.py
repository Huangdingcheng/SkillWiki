"""Bounded, provenance-first adapters for the fixed GEPA and SkillOpt sources.

The reflective functions execute upstream proposal/analyst code with a caller
supplied, budgeted transport. The deterministic no-call probes remain separate.
Only instruction strings are returned; executable packages are never modified.
"""

from __future__ import annotations

import hashlib
import builtins
import importlib
import importlib.util
import inspect
import json
import math
import sys
import tempfile
import threading
import types
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence


GEPA_PROVENANCE: Dict[str, str] = {
    "repository": "https://github.com/gepa-ai/gepa",
    "commit": "d771eb21b5dd3228bc3f567293d2ccfc423fc900",
    "version": "0.1.4",
    "license": "MIT",
    "entrypoint": "gepa.optimize",
    "adapter_protocol": "gepa.core.adapter.GEPAAdapter",
}

SKILLOPT_PROVENANCE: Dict[str, str] = {
    "repository": "https://github.com/microsoft/SkillOpt",
    "commit": "79124b37e9a6371e13b753f8bcd7adb1e493ade1",
    "version": "0.2.0",
    "license": "MIT",
    "entrypoint": "skillopt.optimizer.skill:apply_patch_with_report",
    "archive_sha256": "700ba4a936af64c967487c314249b721138423b96f86f030e500aace78e317e8",
}

MAX_OUTPUT_TOKENS = 8192
MAX_EDIT_BUDGET = 4


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _copy_trace(trace: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(item) for item in trace]


def _model_status(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Normalize non-secret model accounting without inventing usage."""

    if value is None:
        return {"state": "not_called", "model": None, "usage": None}
    result = dict(value)
    result.setdefault("state", "unknown")
    result.setdefault("model", None)
    result.setdefault("usage", None)
    return result


@dataclass
class CandidateRecord:
    """A candidate and the evidence required to review it.

    ``candidate_recorded`` means only that content was produced and hashed.  A
    candidate is admitted for execution only when :func:`candidate_admission`
    returns ``("admissible", "")``.
    """

    method: str
    candidate_text: str
    parent_skill_id: str
    parent_version: str
    parent_hash: str
    failure_trace: List[Dict[str, Any]]
    model_status: Dict[str, Any] = field(default_factory=lambda: _model_status(None))
    provenance: Dict[str, Any] = field(default_factory=dict)
    status: str = "candidate_recorded"
    blocked_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_hash(self) -> str:
        return _sha256_text(self.candidate_text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "candidate_text": self.candidate_text,
            "candidate_hash": self.candidate_hash,
            "parent_skill_id": self.parent_skill_id,
            "parent_version": self.parent_version,
            "parent_hash": self.parent_hash,
            "failure_trace": list(self.failure_trace),
            "model_status": dict(self.model_status),
            "provenance": dict(self.provenance),
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "metadata": dict(self.metadata),
        }


def build_candidate_record(
    *,
    method: str,
    candidate_text: str,
    parent_skill_id: str,
    parent_version: str,
    parent_skill_text: str | None = None,
    parent_hash: str | None = None,
    failure_trace: Sequence[Mapping[str, Any]] = (),
    model_status: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> CandidateRecord:
    """Build a hash-bound record and fail closed on a mismatched parent hash."""

    observed_parent_hash = parent_hash or (_sha256_text(parent_skill_text) if parent_skill_text is not None else "")
    record = CandidateRecord(
        method=str(method),
        candidate_text=str(candidate_text),
        parent_skill_id=str(parent_skill_id),
        parent_version=str(parent_version),
        parent_hash=observed_parent_hash,
        failure_trace=_copy_trace(failure_trace),
        model_status=_model_status(model_status),
        provenance=dict(provenance or {}),
        metadata=dict(metadata or {}),
    )
    if parent_skill_text is not None and parent_hash and parent_hash != _sha256_text(parent_skill_text):
        record.status = "blocked_parent_hash_mismatch"
        record.blocked_reason = "parent_hash does not match parent_skill_text"
    return record


def candidate_admission(candidate: Mapping[str, Any]) -> tuple[str, str]:
    """Admit only a complete, hash-consistent executable candidate package."""

    required = (
        "candidate_text",
        "candidate_hash",
        "parent_skill_id",
        "parent_version",
        "parent_hash",
        "failure_trace",
        "provenance",
        "model_status",
        "candidate_package",
    )
    missing = [key for key in required if not candidate.get(key)]
    if missing:
        return "blocked_candidate_incomplete", "missing candidate fields: " + ", ".join(missing)

    candidate_text = str(candidate["candidate_text"])
    if candidate.get("candidate_hash") != _sha256_text(candidate_text):
        return "blocked_candidate_hash_mismatch", "candidate_hash does not match candidate_text"
    if not isinstance(candidate.get("failure_trace"), (list, tuple)):
        return "blocked_candidate_invalid_trace", "failure_trace must be a list"
    parent_hash = str(candidate.get("parent_hash", ""))
    if not parent_hash.startswith("sha256:") or len(parent_hash) != len("sha256:") + 64:
        return "blocked_candidate_invalid_parent", "parent_hash must be a sha256 digest"

    model_status = candidate.get("model_status")
    if not isinstance(model_status, Mapping):
        return "blocked_candidate_model_status", "model_status must be an object"
    if str(model_status.get("state", "unknown")) in {"failed", "blocked", "unknown"}:
        return "blocked_candidate_model_status", "model/usage status is not successful"
    if str(model_status.get("state")) == "succeeded" and model_status.get("usage") is None:
        return "blocked_candidate_model_status", "successful model status is missing usage"

    package = candidate.get("candidate_package")
    if not isinstance(package, Mapping):
        return "blocked_candidate_not_executable", "candidate_package must be an object"
    package_required = ("implementation", "entrypoint", "manifest", "modes", "license_scope")
    package_missing = [key for key in package_required if not package.get(key)]
    if package_missing:
        return "blocked_candidate_not_executable", "missing executable package fields: " + ", ".join(package_missing)
    license_scope = package.get("license_scope")
    if not isinstance(license_scope, Mapping) or license_scope.get("status") != "verified":
        return "blocked_candidate_license_scope", "candidate implementation license scope is not verified"
    return "admissible", ""


class _DeterministicGEPAAdapter:
    """No-model adapter implementing the upstream GEPA protocol."""

    def __init__(self, proposed_text: str) -> None:
        self.proposed_text = proposed_text

    def evaluate(self, batch: Sequence[Mapping[str, Any]], candidate: Mapping[str, Any], capture_traces: bool = False):
        from gepa.core.adapter import EvaluationBatch

        outputs: List[Dict[str, Any]] = []
        scores: List[float] = []
        traces: List[Dict[str, Any]] = []
        for item in batch:
            score = float(candidate.get("skill_text") == self.proposed_text)
            row = {"id": item.get("id"), "score": score}
            outputs.append(row)
            scores.append(score)
            traces.append({**row, "candidate": dict(candidate)})
        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=traces if capture_traces else None,
            num_metric_calls=len(batch),
        )

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        rows = [
            {"Inputs": {"id": row.get("id")}, "Generated Outputs": row, "Feedback": "no-call contract probe"}
            for row in (eval_batch.trajectories or [])
        ]
        return {component: rows for component in components_to_update}

    def propose_new_texts(self, candidate, reflective_dataset, components_to_update, **kwargs):
        return {component: self.proposed_text for component in components_to_update}


def run_gepa_no_call(
    *,
    seed_text: str,
    proposed_text: str,
    parent_skill_id: str,
    parent_version: str,
    parent_skill_hash: str,
    failure_trace: Sequence[Mapping[str, Any]],
    max_metric_calls: int = 4,
    seed: int = 0,
) -> CandidateRecord:
    """Run the fixed GEPA SDK contract with a deterministic, no-model adapter."""

    if max_metric_calls < 1 or max_metric_calls > 4:
        record = build_candidate_record(
            method="gepa",
            candidate_text="",
            parent_skill_id=parent_skill_id,
            parent_version=parent_version,
            parent_hash=parent_skill_hash,
            failure_trace=failure_trace,
            provenance=GEPA_PROVENANCE,
            metadata={"max_metric_calls": max_metric_calls},
        )
        record.status = "blocked_controls"
        record.blocked_reason = "max_metric_calls must be between 1 and 4"
        return record
    try:
        gepa = importlib.import_module("gepa")
        from gepa.core.adapter import EvaluationBatch  # noqa: F401 - verifies the pinned protocol exists
    except Exception as exc:  # noqa: BLE001
        record = build_candidate_record(
            method="gepa",
            candidate_text="",
            parent_skill_id=parent_skill_id,
            parent_version=parent_version,
            parent_hash=parent_skill_hash,
            failure_trace=failure_trace,
            provenance=GEPA_PROVENANCE,
            metadata={"max_metric_calls": max_metric_calls},
        )
        record.status = "blocked_upstream_unavailable"
        record.blocked_reason = f"GEPA SDK unavailable: {exc}"
        return record

    trainset = [{"id": "skillwiki-train-1"}]
    valset = [{"id": "skillwiki-val-1"}]
    kwargs: Dict[str, Any] = {
        "seed_candidate": {"skill_text": seed_text},
        "trainset": trainset,
        "valset": valset,
        "adapter": _DeterministicGEPAAdapter(proposed_text),
        "reflection_minibatch_size": 1,
        "max_metric_calls": max_metric_calls,
        "display_progress_bar": False,
        "seed": seed,
        "skip_perfect_score": False,
    }
    if "write_agent_state" in inspect.signature(gepa.optimize).parameters:
        kwargs["write_agent_state"] = False
    try:
        result = gepa.optimize(**kwargs)
    except Exception as exc:  # noqa: BLE001
        record = build_candidate_record(
            method="gepa",
            candidate_text="",
            parent_skill_id=parent_skill_id,
            parent_version=parent_version,
            parent_hash=parent_skill_hash,
            failure_trace=failure_trace,
            provenance=GEPA_PROVENANCE,
            metadata={"max_metric_calls": max_metric_calls},
        )
        record.status = "blocked_gepa_run"
        record.blocked_reason = f"GEPA optimize failed: {exc}"
        return record

    best_candidate = getattr(result, "best_candidate", {}) or {}
    candidate_text = str(best_candidate.get("skill_text") or "")
    record = build_candidate_record(
        method="gepa",
        candidate_text=candidate_text,
        parent_skill_id=parent_skill_id,
        parent_version=parent_version,
        parent_hash=parent_skill_hash,
        failure_trace=failure_trace,
        provenance=GEPA_PROVENANCE,
        metadata={
            "max_metric_calls": max_metric_calls,
            "model_call": False,
            "best_idx": getattr(result, "best_idx", None),
            "num_candidates": getattr(result, "num_candidates", None),
            "total_metric_calls": getattr(result, "total_metric_calls", None),
        },
    )
    if not candidate_text:
        record.status = "blocked_empty_candidate"
        record.blocked_reason = "GEPA returned an empty candidate"
    return record


@dataclass
class SkillOptPatchResult:
    """Result of a bounded, no-model SkillOpt patch application."""

    candidate: CandidateRecord
    updated_skill: str
    patch_report: List[Dict[str, Any]]
    status: str
    blocked_reason: str = ""
    controls: Dict[str, int] = field(default_factory=dict)


def _load_pinned_skillopt_patch(source_root: str | Path | None = None):
    """Load only the pinned patch engine; never install or fetch dependencies."""
    if source_root is None:
        # integration/artifacts/... relative to this adapter's worktree.
        integration_root = Path(__file__).resolve().parents[4]
        source_root = integration_root / "artifacts/20260929-skillopt-c-ref-42/source"
    patch_path = Path(source_root) / "skillopt/optimizer/skill.py"
    if not patch_path.is_file():
        raise FileNotFoundError(f"pinned SkillOpt patch engine not found: {patch_path}")
    spec = importlib.util.spec_from_file_location("_skillwiki_pinned_skillopt_skill", patch_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load pinned SkillOpt patch engine: {patch_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_patch_with_report


def apply_bounded_skillopt_patch(
    *,
    parent_skill: str,
    patch: Mapping[str, Any],
    parent_skill_id: str,
    parent_version: str,
    parent_skill_hash: str,
    failure_trace: Sequence[Mapping[str, Any]],
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    edit_budget: int = MAX_EDIT_BUDGET,
    retries: int = 0,
    model_status: Mapping[str, Any] | None = None,
    usage: Mapping[str, Any] | None = None,
    source_root: str | Path | None = None,
) -> SkillOptPatchResult:
    """Apply a patch through pinned SkillOpt code under hard safety bounds.

    This function intentionally does not call SkillOpt's reflection/LLM code.
    ``model_status`` and ``usage`` are supplied by the caller so accounting
    cannot be fabricated by this no-network adapter.
    """

    controls = {
        "max_output_tokens": int(max_output_tokens),
        "edit_budget": int(edit_budget),
        "retries": int(retries),
    }
    recorded_model_status = _model_status(model_status)
    if usage is not None:
        recorded_model_status["usage"] = dict(usage)
    candidate = build_candidate_record(
        method="skillopt",
        candidate_text=parent_skill,
        parent_skill_id=parent_skill_id,
        parent_version=parent_version,
        parent_skill_text=parent_skill,
        parent_hash=parent_skill_hash,
        failure_trace=failure_trace,
        model_status=recorded_model_status,
        provenance=SKILLOPT_PROVENANCE,
        metadata={"controls": controls, "usage": dict(usage or {}) if usage is not None else None},
    )

    def blocked(reason: str, status: str = "blocked_controls") -> SkillOptPatchResult:
        candidate.status = status
        candidate.blocked_reason = reason
        return SkillOptPatchResult(candidate, parent_skill, [], status, reason, controls)

    if candidate.status.startswith("blocked_"):
        return blocked(candidate.blocked_reason, candidate.status)
    if str(recorded_model_status.get("state", "unknown")) in {"failed", "blocked", "unknown"}:
        return blocked("model/usage status is not successful", "blocked_model_status")
    if retries != 0:
        return blocked("retries must be exactly 0")
    if max_output_tokens < 1 or max_output_tokens > MAX_OUTPUT_TOKENS:
        return blocked(f"max_output_tokens must be between 1 and {MAX_OUTPUT_TOKENS}")
    if edit_budget < 1 or edit_budget > MAX_EDIT_BUDGET:
        return blocked(f"edit_budget must be between 1 and {MAX_EDIT_BUDGET}")
    if not isinstance(patch, Mapping) or not isinstance(patch.get("edits"), list):
        return blocked("patch must contain an edits list", "blocked_malformed_patch")
    edits = patch.get("edits", [])
    if len(edits) > edit_budget:
        return blocked(f"patch has {len(edits)} edits; edit_budget is {edit_budget}")

    try:
        apply_patch_with_report = _load_pinned_skillopt_patch(source_root)
        updated_skill, report = apply_patch_with_report(parent_skill, patch)
    except Exception as exc:  # noqa: BLE001
        return blocked(f"pinned SkillOpt patch engine unavailable: {exc}", "blocked_dependency")
    reports = [dict(item) for item in report]
    if updated_skill == parent_skill:
        return blocked("patch produced no content change", "blocked_noop_patch")
    if any(str(item.get("status", "")).startswith(("error", "skipped")) for item in reports):
        return blocked("patch report contains an error or skipped edit", "blocked_patch_report")

    candidate.candidate_text = updated_skill
    result = SkillOptPatchResult(candidate, updated_skill, reports, "patched", "", controls)
    return result


DEMO_OUTPUT_CAP = 4096
_GEPA_IMPORT_LOCK = threading.Lock()
_GEPA_ARCHIVE_SHA256 = "0cd53ff47c53d5ed2f4e41b738848c0bf0b3e493b7fe37e081f13babc8eccb5c"


def _integration_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _controls_error(max_output_tokens: int, retries: int, edit_budget: int = 4) -> str:
    if type(retries) is not int or retries != 0:
        return "retries must be exactly 0"
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= DEMO_OUTPUT_CAP:
        return f"max_output_tokens must be between 1 and {DEMO_OUTPUT_CAP}"
    if type(edit_budget) is not int or not 1 <= edit_budget <= MAX_EDIT_BUDGET:
        return "edit_budget must be between 1 and 4"
    return ""


def _invoke_transport(llm, *, system, user, max_output_tokens, purpose, observe=None):
    """One transport attempt; no fallback route and no implicit retries."""
    response = llm(system=system, user=user, max_output_tokens=max_output_tokens,
                   retries=0, purpose=purpose)
    if isinstance(response, Mapping) and observe is not None:
        observe(response)
    if not isinstance(response, Mapping) or not isinstance(response.get("text"), str):
        raise ValueError("transport must return text/model/usage/receipt mapping")
    if not response["text"].strip():
        raise ValueError("transport returned empty text")
    if response.get("finish_reason") in {"length", "max_tokens"}:
        raise ValueError("transport completion is truncated")
    if not isinstance(response.get("usage"), Mapping) or not response.get("model"):
        raise ValueError("transport response is missing model or usage accounting")
    completion_tokens = response["usage"].get("completion_tokens", response["usage"].get("output_tokens"))
    if type(completion_tokens) is not int or not 0 <= completion_tokens <= max_output_tokens:
        raise ValueError("transport response has invalid or over-cap output usage")
    return dict(response)


def _load_verified_gepa(source_root=None):
    """Import a single stable SDK identity, never swap live global modules."""
    root = Path(source_root or (_integration_root() / "third_party/gepa" / GEPA_PROVENANCE["commit"]))
    receipt = json.loads((root / "skillwiki-source-receipt.json").read_text(encoding="utf-8"))
    if receipt.get("commit") != GEPA_PROVENANCE["commit"] or receipt.get("archive_sha256") != _GEPA_ARCHIVE_SHA256:
        raise ValueError("GEPA source receipt does not match the fixed archive")
    archive_path = root / "source.zip"
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != _GEPA_ARCHIVE_SHA256:
        raise ValueError("GEPA archive hash mismatch")
    files = receipt.get("files", {})
    actual_files = {p.relative_to(root).as_posix() for p in (root / "src/gepa").rglob("*.py")}
    if not files or actual_files != set(files):
        raise ValueError("GEPA source manifest is incomplete")
    with zipfile.ZipFile(archive_path) as archive:
        prefix = "gepa-" + GEPA_PROVENANCE["commit"] + "/"
        for relative, expected in files.items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("GEPA source manifest path escapes source_root")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected or raw != archive.read(prefix + relative):
                raise ValueError(f"GEPA source hash mismatch: {relative}")
    with _GEPA_IMPORT_LOCK:
        existing = sys.modules.get("gepa")
        if existing is not None and Path(existing.__file__).resolve() != (root / "src/gepa/__init__.py").resolve():
            raise ImportError("a different GEPA is already imported; use an isolated worker")
        source = str((root / "src").resolve())
        if source not in sys.path:
            sys.path.insert(0, source)
        gepa = importlib.import_module("gepa")
        adapter = importlib.import_module("gepa.core.adapter")
    return gepa, adapter.EvaluationBatch, {
        **GEPA_PROVENANCE, "source_root": str(root.resolve()),
        "archive_sha256": _GEPA_ARCHIVE_SHA256,
        "loaded_source_sha256": files["src/gepa/api.py"],
    }


def run_gepa_reflection(
    *, seed_text: str, diagnostic_cases: Sequence[Mapping[str, Any]],
    evaluator: Callable, llm: Callable, parent_skill_id: str, parent_version: str,
    parent_skill_hash: str, failure_trace: Sequence[Mapping[str, Any]] = (),
    max_output_tokens: int = DEMO_OUTPUT_CAP, retries: int = 0,
    source_root: str | Path | None = None,
) -> CandidateRecord:
    """One official GEPA reflective proposal scored by the real task callback.

    The callback is ``evaluator(skill_text, case) -> {score, output, feedback}``.
    Only diagnostic examples enter GEPA; independent heldout scoring belongs to
    the caller after this returns. A rejected proposal is retained as evidence,
    with GEPA's selected best candidate reported independently in metadata.
    """
    record = build_candidate_record(method="gepa", candidate_text=seed_text,
        parent_skill_id=parent_skill_id, parent_version=parent_version,
        parent_skill_text=seed_text, parent_hash=parent_skill_hash,
        failure_trace=failure_trace, provenance=GEPA_PROVENANCE,
        metadata={"controls": {"max_output_tokens": max_output_tokens, "retries": retries,
                               "max_candidates": 1}, "evaluation_scope": "diagnostic_only"})

    def blocked(status, reason):
        record.status, record.blocked_reason = status, str(reason)
        return record

    if record.status.startswith("blocked_"):
        return record
    error = _controls_error(max_output_tokens, retries)
    if error:
        return blocked("blocked_controls", error)
    if not diagnostic_cases or any(case.get("split") != "diagnostic" for case in diagnostic_cases):
        return blocked("blocked_dataset", "only nonempty explicitly diagnostic cases may enter reflection")
    attempts, proposals, evaluations = [], [], []
    record.metadata["transport_attempts"] = attempts
    record.metadata["evaluations"] = evaluations
    try:
        gepa, EvaluationBatch, provenance = _load_verified_gepa(source_root)
        record.provenance = provenance

        class ReflectiveAdapter:
            # None explicitly selects the upstream instruction proposal strategy.
            propose_new_texts = None
            def evaluate(self, batch, candidate, capture_traces=False):
                text = candidate["skill_text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("GEPA returned an empty instruction")
                if text != seed_text and text not in proposals:
                    proposals.append(text)
                if len(proposals) > 1:
                    raise ValueError("GEPA attempted more than one candidate")
                rows = []
                for case in batch:
                    result = dict(evaluator(text, case))
                    score = float(result["score"])
                    if not math.isfinite(score) or not 0 <= score <= 1:
                        raise ValueError("task evaluator score must be finite and in [0, 1]")
                    row = {"id": case["id"], "input": case.get("input", ""),
                           "score": score, "output": result.get("output"),
                           "feedback": result.get("feedback", ""),
                           "candidate_hash": _sha256_text(text)}
                    rows.append(row)
                    evaluations.append(row)
                return EvaluationBatch(outputs=[r["output"] for r in rows],
                    scores=[r["score"] for r in rows],
                    trajectories=rows if capture_traces else None,
                    num_metric_calls=len(rows))

            def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
                rows = [{"Inputs": {"id": r["id"], "input": r["input"]},
                         "Generated Outputs": r["output"], "Feedback": r["feedback"]}
                        for r in (eval_batch.trajectories or [])]
                return {component: rows for component in components_to_update}

        def reflection_lm(prompt):
            if attempts:
                raise ValueError("GEPA reflection is limited to one transport attempt")
            attempts.append({"state": "started", "purpose": "gepa_reflection"})
            user = prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False)
            try:
                response = _invoke_transport(llm, system="Improve only the SKILL instruction text. Return the complete instruction in a fenced block.",
                    user=user, max_output_tokens=max_output_tokens, purpose="gepa_reflection",
                    observe=lambda r: attempts[0].update({k: r.get(k) for k in ("model", "usage", "receipt")}))
            except Exception as exc:
                attempts[0].update(state="failed", error_type=type(exc).__name__)
                record.model_status = {"state": "failed", "model": attempts[0].get("model"), "usage": attempts[0].get("usage")}
                raise
            attempts[0].update({k: response.get(k) for k in ("model", "usage", "receipt")})
            attempts[0]["state"] = "succeeded"
            record.model_status = {"state": "succeeded", "model": response["model"], "usage": response["usage"]}
            return response["text"]

        cases = [dict(c) for c in diagnostic_cases]
        result = gepa.optimize(seed_candidate={"skill_text": seed_text},
            trainset=cases, valset=cases, adapter=ReflectiveAdapter(),
            reflection_lm=reflection_lm, reflection_minibatch_size=len(cases),
            max_metric_calls=4 * len(cases), stop_callbacks=lambda state: bool(attempts),
            skip_perfect_score=False, display_progress_bar=False, write_agent_state=False,
            use_merge=False, cache_evaluation=False, seed=0)
        record.metadata.update({"best_candidate": result.best_candidate,
            "best_idx": result.best_idx, "num_candidates": result.num_candidates,
            "total_metric_calls": result.total_metric_calls,
            "val_aggregate_scores": result.val_aggregate_scores,
            "proposal_count": len(proposals), "model_call": bool(attempts)})
        if not proposals:
            return blocked("blocked_no_candidate", "GEPA produced no changed instruction candidate")
        record.candidate_text = proposals[0]
        record.status = "candidate_generated"
        return record
    except Exception as exc:
        if attempts and attempts[-1]["state"] == "started":
            attempts[-1]["state"] = "failed"
        if attempts and record.model_status["state"] != "succeeded":
            record.model_status = {"state": "failed", "model": attempts[-1].get("model"), "usage": attempts[-1].get("usage")}
        return blocked("blocked_gepa_reflection", f"{type(exc).__name__}: {exc}")


def _load_isolated_skillopt(source_root, bounded_chat):
    """Fresh module graph per call, with only model transport replaced.

    Each module has its own __import__; no sys.modules replacement, global
    monkeypatch, upstream-file edit, or optimizer retry default can leak across
    concurrent sessions. Every executed source byte is verified against the
    frozen archive before execution.
    """
    root = Path(source_root or (_integration_root() / "artifacts/20260929-skillopt-c-ref-42/source"))
    archive_path = _integration_root() / "artifacts/20260929-skillopt-c-ref-42" / ("SkillOpt-" + SKILLOPT_PROVENANCE["commit"] + ".zip")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != SKILLOPT_PROVENANCE["archive_sha256"]:
        raise ValueError("SkillOpt archive hash mismatch")
    archive = zipfile.ZipFile(archive_path)
    prefix = "SkillOpt-" + SKILLOPT_PROVENANCE["commit"] + "/"
    modules, hashes = {}, {}
    transport_module = types.ModuleType("skillopt.model")
    transport_module.chat_optimizer = bounded_chat
    modules["skillopt.model"] = transport_module

    def load(name):
        if name in modules:
            return modules[name]
        relative = name.replace(".", "/")
        path = root / (relative + ".py")
        if not path.is_file():
            path = root / relative / "__init__.py"
        # Empty namespace parents intentionally skip unrelated package exports.
        if name in {"skillopt", "skillopt.optimizer", "skillopt.gradient"}:
            module = types.ModuleType(name)
            modules[name] = module
            return module
        raw = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        if raw != archive.read(prefix + rel):
            raise ValueError(f"SkillOpt source differs from pinned archive: {rel}")
        hashes[rel] = hashlib.sha256(raw).hexdigest()
        module = types.ModuleType(name)
        module.__file__ = str(path)
        module.__package__ = name if path.name == "__init__.py" else name.rpartition(".")[0]
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": isolated_import}
        modules[name] = module
        exec(compile(raw, str(path), "exec"), module.__dict__)
        return module

    def isolated_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            name = importlib.util.resolve_name("." * level + name, globals["__package__"])
        if name == "skillopt" or name.startswith("skillopt."):
            module = load(name)
            return module if fromlist else load("skillopt")
        return builtins.__import__(name, globals, locals, fromlist, 0)

    reflect = load("skillopt.gradient.reflect")
    patch = load("skillopt.optimizer.skill")
    # Prompts are read at runtime by the unchanged official loader.
    prompt = root / "skillopt/prompts/analyst_error.md"
    raw = prompt.read_bytes()
    if raw != archive.read(prefix + "skillopt/prompts/analyst_error.md"):
        raise ValueError("SkillOpt analyst prompt differs from pinned archive")
    hashes["skillopt/prompts/analyst_error.md"] = hashlib.sha256(raw).hexdigest()
    archive.close()
    return reflect, patch, {**SKILLOPT_PROVENANCE, "source_root": str(root.resolve()),
        "reflection_entrypoint": "skillopt.gradient.reflect:run_error_analyst_minibatch",
        "source_sha256": hashes, "transport_adaptation": "isolated module import binding only; forced retries=0 and output cap"}


def run_skillopt_reflection(
    *, parent_skill: str, failure_trace: Sequence[Mapping[str, Any]], llm: Callable,
    parent_skill_id: str, parent_version: str, parent_skill_hash: str,
    max_output_tokens: int = DEMO_OUTPUT_CAP, edit_budget: int = MAX_EDIT_BUDGET,
    retries: int = 0, source_root: str | Path | None = None,
) -> SkillOptPatchResult:
    """Run the pinned failure analyst once and atomically validate its patch.

    Failure rows must contain id/input/output/feedback and split=diagnostic.
    Independent heldout results must never be passed here. Upstream's retry=3
    and output=16384 arguments are overridden at the isolated transport binding.
    """
    controls = {"max_output_tokens": max_output_tokens, "edit_budget": edit_budget, "retries": retries}
    candidate = build_candidate_record(method="skillopt", candidate_text=parent_skill,
        parent_skill_id=parent_skill_id, parent_version=parent_version,
        parent_skill_text=parent_skill, parent_hash=parent_skill_hash,
        failure_trace=failure_trace, provenance=SKILLOPT_PROVENANCE,
        metadata={"controls": controls, "evaluation_scope": "diagnostic_only"})
    attempts, raw_patches = [], []
    candidate.metadata["transport_attempts"] = attempts

    def blocked(status, reason, report=()):
        candidate.status, candidate.blocked_reason = status, str(reason)
        return SkillOptPatchResult(candidate, parent_skill, list(report), status, str(reason), controls)

    if candidate.status.startswith("blocked_"):
        return blocked(candidate.status, candidate.blocked_reason)
    error = _controls_error(max_output_tokens, retries, edit_budget)
    if error:
        return blocked("blocked_controls", error)
    if not failure_trace or any(row.get("split") != "diagnostic" for row in failure_trace):
        return blocked("blocked_dataset", "nonempty diagnostic failure traces are required")

    def bounded_chat(**kwargs):
        if attempts:
            raise ValueError("SkillOpt is limited to one transport attempt")
        attempts.append({"state": "started", "upstream_retries": kwargs.get("retries"),
                         "upstream_output_tokens": kwargs.get("max_completion_tokens")})
        response = _invoke_transport(llm, system=kwargs["system"], user=kwargs["user"],
            max_output_tokens=max_output_tokens, purpose="skillopt_reflection",
            observe=lambda r: attempts[0].update({k: r.get(k) for k in ("model", "usage", "receipt")}))
        attempts[0].update({k: response.get(k) for k in ("model", "usage", "receipt")})
        attempts[0]["state"] = "succeeded"
        candidate.model_status = {"state": "succeeded", "model": response["model"], "usage": response["usage"]}
        # Inspect the raw patch before upstream truncate_payload can hide excess edits.
        raw_patches.append(response["text"])
        return response["text"], response["usage"]

    try:
        reflect, patcher, provenance = _load_isolated_skillopt(source_root, bounded_chat)
        candidate.provenance = provenance
        with tempfile.TemporaryDirectory(prefix="skillwiki-skillopt-") as directory:
            items = []
            for index, row in enumerate(failure_trace):
                task_id = f"diagnostic-{index}"  # Never use caller IDs as filesystem paths.
                path = Path(directory) / task_id
                path.mkdir()
                conversation = [{"role": "user", "content": str(row.get("input", ""))},
                    {"role": "assistant", "content": json.dumps(row.get("output"), ensure_ascii=False)},
                    {"role": "system", "content": str(row.get("feedback", ""))}]
                (path / "conversation.json").write_text(json.dumps(conversation, ensure_ascii=True), encoding="ascii")
                items.append({"id": task_id, "hard": 0, "task_description": row.get("input", ""),
                              "fail_reason": row.get("feedback", ""), "n_turns": 1})
            reflection = reflect.run_error_analyst_minibatch(parent_skill, items, directory,
                edit_budget=edit_budget, update_mode="patch")
        if not reflection or not isinstance(reflection.get("patch"), Mapping):
            return blocked("blocked_reflection", "SkillOpt failure analyst did not return a valid patch")
        # Same official JSON extractor, before its mutable truncation step.
        raw = reflect.extract_json(raw_patches[0])
        raw_patch = raw.get("patch", {}) if isinstance(raw, Mapping) else {}
        if not isinstance(raw_patch.get("edits"), list) or not 1 <= len(raw_patch["edits"]) <= edit_budget:
            return blocked("blocked_controls", "raw patch must contain 1 to edit_budget edits; truncation is not accepted")
        patch = reflection["patch"]
        candidate.metadata["reflection"] = reflection
        updated, report = patcher.apply_patch_with_report(parent_skill, patch)
        if len(report) != len(patch["edits"]) or any(not str(row.get("status", "")).startswith("applied_") or "fallback" in str(row.get("status")) for row in report):
            return blocked("blocked_patch_report", "patch contains skipped, failed, or fallback edits; candidate rolled back", report)
        if updated == parent_skill:
            return blocked("blocked_noop_patch", "patch produced no content change", report)
        candidate.candidate_text = updated
        candidate.status = "candidate_generated"
        return SkillOptPatchResult(candidate, updated, report, "patched", "", controls)
    except Exception as exc:
        return blocked("blocked_skillopt_reflection", f"{type(exc).__name__}: {exc}")
    finally:
        if attempts and attempts[-1]["state"] == "started":
            attempts[-1]["state"] = "failed"
            candidate.model_status = {"state": "failed", "model": attempts[-1].get("model"), "usage": attempts[-1].get("usage")}


__all__ = [
    "CandidateRecord",
    "GEPA_PROVENANCE",
    "MAX_EDIT_BUDGET",
    "MAX_OUTPUT_TOKENS",
    "SKILLOPT_PROVENANCE",
    "SkillOptPatchResult",
    "apply_bounded_skillopt_patch",
    "build_candidate_record",
    "candidate_admission",
    "run_gepa_no_call",
    "run_gepa_reflection",
    "run_skillopt_reflection",
]
