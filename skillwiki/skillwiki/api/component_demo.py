"""Small durable bridge from pinned component calls to existing SkillWiki governance."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.skill_model import SkillState, SkillMetrics
from ..utils.llm_client import Message

_LOCK = threading.RLock()
_MODEL_LOCK = threading.Lock()
_EVALUATION_LOCK = threading.Lock()
DIALOGUE_TASK_INSTRUCTION = (
    'Return only a JSON object with "nodes" and "edges" arrays. '
    'Create one node for every nonempty dialogue line, in input order. Each node '
    'must contain "id" ("line-1", "line-2", ...), "speaker" (the text before '
    'the first colon), and "text" (the dialogue text after the first colon, '
    'with surrounding whitespace removed). Create one directed edge for each '
    'consecutive pair of nodes, with "from" and "to" containing their node IDs. '
    'Include every line and consecutive connection; do not add explanation.'
)


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def instruction(skill: Any) -> str:
    return getattr(getattr(skill, "implementation", None), "prompt_template", None) or skill.description


def _creation_context(skill: Any) -> dict:
    return getattr(getattr(skill, "provenance", None), "creation_context", None) or {}


class ComponentStore:
    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def for_app(cls, app: Any) -> "ComponentStore":
        path = getattr(app, "execution_trace_path", None)
        if path is None:
            raise ValueError("durable execution storage is not configured")
        return cls(Path(path).parent / "component-runs")

    def get(self, run_id: str) -> dict:
        if not run_id or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("invalid component run id")
        with _LOCK:
            path = self.root / f"{run_id}.json"
            if not path.exists():
                raise FileNotFoundError(run_id)
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, row: dict) -> None:
        with _LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{row['run_id']}.json"
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, indent=2, default=str))
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)

    def claim(self, row: dict) -> tuple[dict, bool]:
        """Atomically claim one run across threads in this server process."""
        with _LOCK:
            try:
                return self.get(row["run_id"]), False
            except FileNotFoundError:
                self.save(row)
                return row, True

    def list(self, skill_id: str | None = None) -> list[dict]:
        rows = [self.get(p.stem) for p in self.root.glob("*.json")]
        return sorted([r for r in rows if not skill_id or r["skill_id"] == skill_id],
                      key=lambda r: r["created_at"], reverse=True)


class DemoModel:
    """All component text calls use the same budgeted client and stage counter."""
    def __init__(self, client: Any, store: ComponentStore, run_id: str | None = None):
        if getattr(client, "_cumulative_budget", None) is None:
            raise ValueError("component model calls require the reconciled cumulative budget")
        self.client = client
        self.store = store
        self.receipts: list[dict] = []
        self.outputs: list[dict] = []
        self.run_id = run_id

    def _record(self, output: dict) -> None:
        self.receipts.append(output.get("receipt", {}))
        self.outputs.append(output)
        if self.run_id is not None:
            with _LOCK:
                row = self.store.get(self.run_id)
                row["model_receipts"] = list(self.receipts)
                row["model_outputs"] = list(self.outputs)
                self.store.save(row)

    def __call__(self, *, system: str, user: str, max_output_tokens: int = 4096,
                 retries: int = 0, purpose: str = "component") -> dict:
        if retries != 0 or not 0 < max_output_tokens <= 4096:
            raise ValueError("component calls require retries=0 and output <=4096")
        if len((system + user).encode("utf-8")) > 64_000:
            raise ValueError("component input exceeds the bounded Demo size")
        with _MODEL_LOCK:
            with _LOCK:
                counter_path = self.store.root / "text-requests.jsonl"
                attempts = counter_path.read_text(encoding="utf-8").splitlines() if counter_path.exists() else []
                if len(attempts) >= 13:
                    raise ValueError("stage text request limit (13) reached")
                counter_path.parent.mkdir(parents=True, exist_ok=True)
                with counter_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"purpose": purpose, "input_hash": digest(system + user)}) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            if hasattr(self.client, "last_budget_receipt"):
                self.client.last_budget_receipt = None
            try:
                response = self.client.chat([Message.system(system), Message.user(user)],
                                            max_tokens=max_output_tokens, temperature=0,
                                            extra={"thinking": {"type": "disabled"}})
            except BaseException as exc:
                self._record({"purpose": purpose, "input_hash": digest(system + user),
                    "error": f"{type(exc).__name__}: {str(exc)[:600]}",
                    "receipt": getattr(self.client, "last_budget_receipt", None) or {},
                    "outcome": "provider_exception"})
                raise
            output = {"text": response.content, "model": response.model, "usage": response.usage,
                      "receipt": response.raw.get("budget_receipt", {}), "purpose": purpose,
                      "input_hash": digest(system + user), "outcome": "response_received",
                      "finish_reason": getattr(response, "finish_reason", None)}
            self._record(output)
            return output


async def materialize_candidate(app: Any, *, component: str, parent: Any, candidate_text: str,
                                parent_hash: str, parent_version: str, run_id: str,
                                validation: dict | None = None) -> dict:
    """Serialize candidate version allocation and persistence for this app."""
    import asyncio
    lock = getattr(app, "_component_candidate_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app._component_candidate_lock = lock
    async with lock:
        return await _materialize_candidate(app, component=component, parent=parent,
            candidate_text=candidate_text, parent_hash=parent_hash, parent_version=parent_version,
            run_id=run_id, validation=validation)


async def _materialize_candidate(app: Any, *, component: str, parent: Any, candidate_text: str,
                                 parent_hash: str, parent_version: str, run_id: str,
                                 validation: dict | None = None) -> dict:
    """Create a distinct draft and real review branch without changing the release."""
    from .routes.lifecycle import _governance_repo_path
    from ..layers.skill_governance import GitVersionStore, propose_skill_change

    current = await app.wiki.get(parent.skill_id)
    if not current or current.version != parent_version or digest(instruction(current)) != parent_hash:
        raise ValueError("parent ID/version/content changed; candidate blocked")
    if not candidate_text.strip() or candidate_text == instruction(parent):
        raise ValueError("candidate contains no instruction change")
    store = ComponentStore.for_app(app)
    candidate_hash = digest(candidate_text)
    for existing in store.list(parent.skill_id):
        cand = existing.get("candidate", {})
        if existing["component"] == component and existing.get("parent_hash") == parent_hash and cand.get("candidate_hash") == candidate_hash and cand.get("persistence_stage", "complete") == "complete":
            return cand
    history = await app.wiki.get_version_history(parent.name)
    major, minor, patch = max(tuple(map(int, s.version.split("."))) for s in history or [parent])
    version = f"{major}.{minor}.{patch + 1}"
    implementation = parent.implementation.model_copy(deep=True) if parent.implementation else None
    if implementation is None:
        from ..models.skill_model import SkillImplementation
        implementation = SkillImplementation(prompt_template=candidate_text)
    else:
        implementation.prompt_template = candidate_text
    from ..models.skill_model import SkillProvenance
    provenance = parent.provenance.model_copy(deep=True) if parent.provenance else SkillProvenance(source_type="adapt")
    provenance.source_type = "adapt"
    provenance.parent_skill_ids = list(dict.fromkeys([*provenance.parent_skill_ids, parent.skill_id]))
    provenance.creation_context.update({"component_run_id": run_id, "component": component,
        "parent_version": parent_version, "parent_hash": parent_hash,
        "candidate_hash": candidate_hash, "review_required": True})
    evaluation = parent.evaluation.model_copy(deep=True)
    evaluation.harness_validation = {"status": "draft_pending_review", "local_replay": validation or {},
                                      "promotion_allowed": False}
    evaluation.validation_summary = "Component candidate; local replay and human review are separate from release."
    # Build the branch first. A branch conflict must not leave an apparently successful draft.
    import uuid
    draft = parent.model_copy(deep=True, update={"skill_id": str(uuid.uuid4()), "version": version,
        "state": SkillState.DRAFT, "implementation": implementation, "provenance": provenance,
        "evaluation": evaluation, "metrics": SkillMetrics(), "released_at": None})
    repo = _governance_repo_path()
    bundle = propose_skill_change(repo, parent, draft, GitVersionStore(repo))
    candidate = {"draft_skill_id": draft.skill_id, "version": draft.version, "candidate_text": candidate_text,
            "candidate_hash": candidate_hash, "parent_skill_id": parent.skill_id,
            "parent_version": parent_version, "parent_hash": parent_hash,
            "validation_status": "passed" if validation and validation.get("passed") else "not_passed",
            "validation": validation or {}, **bundle.to_dict(), "persistence_stage": "branch_created"}

    def save_stage(stage: str) -> None:
        candidate["persistence_stage"] = stage
        try:
            row = store.get(run_id)
        except FileNotFoundError:
            return  # Direct helper callers can persist the returned candidate themselves.
        row["candidate"] = dict(candidate)
        store.save(row)

    save_stage("branch_created")
    await app.wiki.create(draft)
    save_stage("draft_created")
    if getattr(app, "graph", None):
        await app.graph.sync_skill(draft)
    save_stage("complete")
    return candidate


async def run_component(app: Any, component: str, skill_id: str, refresh: bool = False) -> dict:
    import asyncio
    from ..layers.skill_governance.component_checks import (
        observe_arex_source_refresh, inspect_skillguard_environment, build_arex_refresh_prompt)

    refresh = bool(refresh and component == "arex")

    skill = await app.wiki.get(skill_id)
    if not skill:
        raise FileNotFoundError(skill_id)
    store = ComponentStore.for_app(app)
    seed_text = instruction(skill)
    initial: dict = {}
    observation_error = None
    binding: dict = {}
    try:
        binding = dict(_creation_context(skill).get("demo_components", {}))
        if component == "arex":
            source = binding.get("source", {})
            if source:
                initial = await asyncio.to_thread(observe_arex_source_refresh, **source)
            else:
                initial = {"observed_state": "unknown", "reason": "No source checkout binding for this skill"}
        elif component == "skillguard":
            env = binding.get("environment", {})
            initial = await asyncio.to_thread(inspect_skillguard_environment, **env) if env else {
                "status": "defer", "reason": "No declared environment contract for this skill"}
    except Exception as exc:
        observation_error = f"{type(exc).__name__}: {str(exc)[:600]}"
        initial = {"observed_state": "unknown", "status": "defer",
                   "observation_error": observation_error, "promotion_allowed": False}
    identity = initial.get("revision_key", initial) if component == "arex" else {
        key: initial.get(key) for key in ("status", "observations", "comment_only", "python",
                                         "executable", "reason", "observation_error")}
    key = json.dumps([component, skill_id, skill.version, digest(seed_text), refresh, identity], sort_keys=True, default=str)
    run_id = hashlib.sha256(key.encode()).hexdigest()
    row = {"run_id": run_id, "component": component, "skill_id": skill_id,
           "parent_version": skill.version, "parent_hash": digest(seed_text),
           "status": "running", "result": initial,
           "created_at": datetime.now(timezone.utc).isoformat()}
    row, claimed = store.claim(row)
    if not claimed:
        return row

    def checkpoint(**updates: Any) -> None:
        # Dispatch receipts and candidate persistence stages may have been saved
        # by the worker thread while the coroutine was awaiting it.
        with _LOCK:
            latest = store.get(run_id)
            for field in ("model_receipts", "model_outputs"):
                if field in latest:
                    row[field] = latest[field]
            latest.update(row)
            latest.update(updates)
            row.clear()
            row.update(latest)
            store.save(row)

    model = None
    try:
        if observation_error:
            raise ValueError("source/environment observation failed: " + observation_error)
        if component not in {"arex", "skillguard", "gepa", "skillopt"}:
            raise ValueError("unsupported component")
        if component == "arex":
            row["status"] = "success" if initial.get("observed_state") == "unchanged" else "blocked"
            if initial.get("refresh_required") and refresh:
                model = DemoModel(app.llm, store, run_id)
                prompt = build_arex_refresh_prompt(observation=initial, current_skill_content=seed_text)
                response = await asyncio.to_thread(model, system="Follow the upstream refresh workflow and requested JSON output. Never import or publish.", user=prompt, purpose="arex-refresh")
                import re
                raw = response["text"].strip()
                fence = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL | re.IGNORECASE)
                refreshed = json.loads(fence.group(1) if fence else raw)
                if not isinstance(refreshed, dict) or not isinstance(refreshed.get("candidate_content"), str):
                    raise ValueError("AREX output must be an object with string candidate_content")
                row["result"]["refresh_output"] = refreshed
                checkpoint()
                row["candidate"] = await materialize_candidate(app, component=component, parent=skill,
                    candidate_text=refreshed["candidate_content"], parent_hash=row["parent_hash"], parent_version=skill.version, run_id=run_id)
                row["model_receipts"] = model.receipts
                row["status"] = "success"
        elif component == "skillguard":
            row["status"] = "success" if initial.get("status") == "pass" else "blocked"
        else:
            from ..layers.skill_management.gepa_skillopt_adapter import run_gepa_reflection, run_skillopt_reflection
            from ..layers.skill_management.dialogue_demo import DIAGNOSTIC_CASES, HELDOUT_CASES, DialogueTaskEvaluator
            model = DemoModel(app.llm, store, run_id)
            def evaluate_model(skill_text, dialogue, case_id):
                task_instruction = binding.get("task_instruction") or DIALOGUE_TASK_INSTRUCTION
                user = task_instruction + "\nDialogue:\n" + dialogue
                cache_id = hashlib.sha256(json.dumps([skill_text, user, case_id, "deepseek-flash", 0, 4096], ensure_ascii=False).encode()).hexdigest()
                cache_path = store.root / "evaluations" / f"{cache_id}.json"
                with _EVALUATION_LOCK:
                    if cache_path.exists():
                        return json.loads(cache_path.read_text(encoding="utf-8"))
                    result = model(system=skill_text, user=user, purpose="dialogue-" + case_id)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = cache_path.with_suffix(".tmp")
                    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(cache_path)
                    return result
            evaluator = DialogueTaskEvaluator(evaluate_model)
            checkpoint(task_instruction=binding.get("task_instruction") or DIALOGUE_TASK_INSTRUCTION,
                task_scope="local_dialogue_material_derivative_not_official_skillsbench_verifier")
            def evaluate_cases(text, cases, field):
                evaluations = []
                for case in cases:
                    evaluations.append(evaluator(text, case))
                    checkpoint(**{field: list(evaluations)})
                return evaluations
            diagnostics = await asyncio.to_thread(evaluate_cases, seed_text, DIAGNOSTIC_CASES, "diagnostic_evaluations")
            failures = [{"id": case["id"], "split": "diagnostic", "input": case["input"],
                         "output": ev["output"], "feedback": ev["feedback"]}
                        for case, ev in zip(DIAGNOSTIC_CASES, diagnostics) if ev.get("score", 0) < 1]
            checkpoint(failure_trace=failures)
            if not failures:
                raise ValueError("No reproducible failure in frozen diagnostics; use an explicitly labelled controlled copy")
            kwargs = dict(llm=model, parent_skill_id=skill_id, parent_version=skill.version,
                          parent_skill_hash=digest(seed_text), failure_trace=failures)
            if component == "gepa":
                record = await asyncio.to_thread(run_gepa_reflection, seed_text=seed_text,
                    diagnostic_cases=DIAGNOSTIC_CASES, evaluator=evaluator, **kwargs)
            elif component == "skillopt":
                record = await asyncio.to_thread(run_skillopt_reflection, parent_skill=seed_text, **kwargs)
            else:
                raise ValueError("unsupported component")
            from dataclasses import asdict, is_dataclass
            result = record.to_dict() if hasattr(record, "to_dict") else (asdict(record) if is_dataclass(record) else record)
            if hasattr(getattr(record, "candidate", None), "to_dict"):
                result["candidate"] = record.candidate.to_dict()
            row["result"] = result
            checkpoint()
            candidate_record = result.get("candidate", result)
            for record_part in (result, candidate_record):
                state = str(record_part.get("status", ""))
                model_state = record_part.get("model_status", {}).get("state")
                if state.startswith("blocked") or state in {"failed", "error"} or model_state in {"failed", "blocked"}:
                    row["status"] = "blocked"
                    row["error"] = record_part.get("blocked_reason") or "Upstream candidate generation was blocked or failed"
                    return row
            candidate_text = result.get("candidate_text", "")
            if not candidate_text and isinstance(result.get("candidate"), dict):
                candidate_text = result["candidate"].get("candidate_text", "")
            if not isinstance(candidate_text, str) or not candidate_text.strip() or candidate_text == seed_text:
                raise ValueError("Upstream did not produce a usable candidate")
            replay = await asyncio.to_thread(evaluate_cases, candidate_text,
                [*DIAGNOSTIC_CASES, *HELDOUT_CASES], "replay_evaluations")
            validation = {"passed": all(r.get("score", 0) == 1 for r in replay), "cases": replay,
                          "scope": "local_dialogue_demo_not_official_benchmark"}
            row["candidate"] = await materialize_candidate(app, component=component, parent=skill,
                candidate_text=candidate_text, parent_hash=row["parent_hash"], parent_version=skill.version,
                run_id=run_id, validation=validation)
            row["model_receipts"] = model.receipts
            row["status"] = "success"
    except Exception as exc:
        row["status"] = "failed"
        row["error"] = f"{type(exc).__name__}: {str(exc)[:600]}"
    finally:
        checkpoint(**({"model_receipts": model.receipts, "model_outputs": model.outputs} if model else {}))
    return row


def persisted_checks(app: Any, skill: Any) -> dict:
    binding = _creation_context(skill).get("demo_components")
    if not binding:
        return {"blocked": False, "checks": []}
    rows = ComponentStore.for_app(app).list(skill.skill_id)
    checks = []
    for component in ("arex", "skillguard"):
        latest = next((r for r in rows if r["component"] == component), None)
        passed = bool(latest and latest.get("parent_version") == skill.version and
                      latest.get("parent_hash") == digest(instruction(skill)) and
                      latest.get("status") == "success")
        if component == "arex" and latest:
            passed = passed and latest.get("result", {}).get("observed_state") == "unchanged"
        if passed:
            from ..layers.skill_governance.component_checks import observe_arex_source_refresh, inspect_skillguard_environment
            try:
                if component == "arex":
                    current = observe_arex_source_refresh(**binding["source"])
                    passed = current.get("observed_state") == "unchanged" and current.get("revision_key") == latest["result"].get("revision_key")
                else:
                    current = inspect_skillguard_environment(**binding["environment"])
                    passed = current.get("status") == "pass" and current.get("observations") == latest["result"].get("observations")
            except (ValueError, KeyError, OSError, TypeError, AttributeError):
                passed = False
        checks.append({"component": component, "skill_id": skill.skill_id, "status": "pass" if passed else "defer",
                       "run_id": latest.get("run_id") if latest else None,
                       "evidence": latest.get("result") if latest else "No persisted check"})
    return {"blocked": any(c["status"] != "pass" for c in checks), "checks": checks}
