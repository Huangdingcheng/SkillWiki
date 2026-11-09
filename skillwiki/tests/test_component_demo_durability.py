from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import asyncio
import threading

import pytest

from skillwiki.api.component_demo import ComponentStore, DemoModel, digest, instruction, materialize_candidate, persisted_checks, run_component
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillProvenance, SkillState


class FakeModel:
    _cumulative_budget = object()
    last_budget_receipt = None

    def __init__(self, text="not-json", fail=False):
        self.text, self.fail, self.calls = text, fail, 0
        self.messages = []

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.messages.append(messages)
        self.last_budget_receipt = {"request_id": "fake-receipt", "outcome": "dispatch_unknown" if self.fail else "settled"}
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        return SimpleNamespace(content=self.text, model="synthetic", usage={"total_tokens": 3},
                               raw={"budget_receipt": self.last_budget_receipt})


@pytest.fixture
def demo(tmp_path, monkeypatch):
    repo = tmp_path / "governance"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Demo", "-c", "user.email=demo@local", "commit", "--allow-empty", "-qm", "base"], check=True)
    monkeypatch.setenv("SKILLOS_GOVERNANCE_REPO", str(repo))
    return SimpleNamespace(wiki=MemoryWikiManager(), graph=None, llm=FakeModel(),
        execution_trace_path=tmp_path / "runtime" / "traces.jsonl")


async def source_parent(app, binding=None, provenance=True):
    skill = Skill(name="durability_demo", state=SkillState.RELEASED,
        implementation=SkillImplementation(prompt_template="old instruction"),
        provenance=SkillProvenance(source_type="manual", creation_context={"demo_components": binding or {"source": {"repo_path": "synthetic"}}}) if provenance else None)
    return await app.wiki.create(skill)


def test_atomic_threaded_claim_has_exactly_one_owner(tmp_path):
    store = ComponentStore(tmp_path)
    row = {"run_id": "b" * 64, "skill_id": "s", "created_at": "now", "status": "running"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim(dict(row))[1], range(24)))
    assert sum(claims) == 1
    assert store.get(row["run_id"]) == row


def test_provider_exception_receipt_is_durable_before_reraise(tmp_path):
    store = ComponentStore(tmp_path)
    run_id = "c" * 64
    store.save({"run_id": run_id, "status": "running"})
    client = FakeModel(fail=True)
    transport = DemoModel(client, store, run_id)
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        transport(system="s", user="u")
    saved = store.get(run_id)
    assert saved["model_receipts"][0]["request_id"] == "fake-receipt"
    assert saved["model_outputs"][0]["outcome"] == "provider_exception"
    assert client.calls == 1
    assert len((tmp_path / "text-requests.jsonl").read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_arex_bad_json_keeps_raw_output_usage_receipt_and_dedup(demo, monkeypatch):
    import skillwiki.layers.skill_governance.component_checks as checks
    monkeypatch.setattr(checks, "observe_arex_source_refresh", lambda **kwargs: {"observed_state": "updated", "refresh_required": True, "revision_key": "one-revision"})
    monkeypatch.setattr(checks, "build_arex_refresh_prompt", lambda **kwargs: "synthetic prompt")
    parent = await source_parent(demo)
    result = await run_component(demo, "arex", parent.skill_id, True)
    assert result["status"] == "failed"
    assert result["model_outputs"][0]["text"] == "not-json"
    assert result["model_outputs"][0]["usage"] == {"total_tokens": 3}
    assert result["model_receipts"][0]["request_id"] == "fake-receipt"
    assert await run_component(demo, "arex", parent.skill_id, True) == result
    assert demo.llm.calls == 1


@pytest.mark.asyncio
async def test_concurrent_same_run_returns_claim_without_second_dispatch(demo, monkeypatch):
    import skillwiki.layers.skill_governance.component_checks as checks
    monkeypatch.setattr(checks, "observe_arex_source_refresh", lambda **kwargs: {"observed_state":"updated", "refresh_required":True, "revision_key":"concurrent"})
    monkeypatch.setattr(checks, "build_arex_refresh_prompt", lambda **kwargs: "synthetic prompt")
    entered, release = threading.Event(), threading.Event()
    original_chat = demo.llm.chat
    def pending_chat(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_chat(*args, **kwargs)
    demo.llm.chat = pending_chat
    parent = await source_parent(demo)
    first = asyncio.create_task(run_component(demo, "arex", parent.skill_id, True))
    assert await asyncio.to_thread(entered.wait, 10)
    try:
        second = await asyncio.wait_for(run_component(demo, "arex", parent.skill_id, True), 3)
        assert second["status"] == "running"
    finally:
        release.set()
    complete = await first
    assert complete["run_id"] == second["run_id"]
    assert demo.llm.calls == 1


@pytest.mark.asyncio
async def test_source_observation_exception_has_durable_failed_run(demo, monkeypatch):
    import skillwiki.layers.skill_governance.component_checks as checks
    def broken(**kwargs):
        raise ValueError("synthetic pinned asset mismatch")
    monkeypatch.setattr(checks, "observe_arex_source_refresh", broken)
    parent = await source_parent(demo)
    result = await run_component(demo, "arex", parent.skill_id, True)
    assert result["status"] == "failed"
    assert "asset mismatch" in result["result"]["observation_error"]
    assert ComponentStore.for_app(demo).get(result["run_id"]) == result
    assert demo.llm.calls == 0


@pytest.mark.asyncio
async def test_optional_provenance_does_not_break_gate_or_candidate(demo):
    parent = await source_parent(demo, provenance=False)
    assert persisted_checks(demo, parent) == {"blocked": False, "checks": []}
    result = await run_component(demo, "skillguard", parent.skill_id)
    assert result["status"] == "blocked"
    candidate = await materialize_candidate(demo, component="gepa", parent=parent,
        candidate_text="new instruction", parent_hash=digest(instruction(parent)),
        parent_version=parent.version, run_id="d" * 64)
    draft = await demo.wiki.get(candidate["draft_skill_id"])
    assert draft.provenance.parent_skill_ids == [parent.skill_id]
    assert candidate["persistence_stage"] == "complete"


@pytest.mark.asyncio
async def test_graph_failure_preserves_created_draft_and_branch_receipt(demo, monkeypatch):
    import skillwiki.layers.skill_governance.component_checks as checks
    monkeypatch.setattr(checks, "observe_arex_source_refresh", lambda **kwargs: {"observed_state": "updated", "refresh_required": True, "revision_key": "graph-revision"})
    monkeypatch.setattr(checks, "build_arex_refresh_prompt", lambda **kwargs: "synthetic prompt")
    demo.llm = FakeModel(text=json.dumps({"candidate_content": "new instruction"}))
    async def fail_graph(draft):
        raise RuntimeError("synthetic graph failure")
    demo.graph = SimpleNamespace(sync_skill=fail_graph)
    parent = await source_parent(demo)
    result = await run_component(demo, "arex", parent.skill_id, True)
    assert result["status"] == "failed"
    assert result["candidate"]["persistence_stage"] == "draft_created"
    assert result["candidate"]["candidate_text"] == "new instruction"
    assert await demo.wiki.get(result["candidate"]["draft_skill_id"])
    assert result["candidate"]["head_commit"] != result["candidate"]["base_commit"]
    assert result["model_receipts"][0]["request_id"] == "fake-receipt"


@pytest.mark.asyncio
async def test_blocked_reflection_preserves_diagnostics_without_replaying_seed(demo, monkeypatch):
    import skillwiki.layers.skill_management.dialogue_demo as dialogue
    import skillwiki.layers.skill_management.gepa_skillopt_adapter as adapters
    evaluated = []
    class Evaluator:
        def __init__(self, model):
            pass
        def __call__(self, text, case):
            evaluated.append(case["id"])
            return {"score":0, "output":"synthetic diagnostic failure", "feedback":"failure"}
    monkeypatch.setattr(dialogue, "DialogueTaskEvaluator", Evaluator)
    monkeypatch.setattr(adapters, "run_gepa_reflection", lambda **kwargs: {"status":"blocked_controls", "blocked_reason":"synthetic blocked reflection", "candidate_text":"old instruction"})
    parent = await source_parent(demo)
    result = await run_component(demo, "gepa", parent.skill_id)
    assert result["status"] == "blocked"
    assert result["failure_trace"]
    assert len(result["diagnostic_evaluations"]) == len(dialogue.DIAGNOSTIC_CASES)
    assert len(evaluated) == len(dialogue.DIAGNOSTIC_CASES)
    assert "replay_evaluations" not in result
    assert demo.llm.calls == 0


@pytest.mark.asyncio
async def test_diagnostic_requests_include_public_frozen_output_contract(demo, monkeypatch):
    import skillwiki.layers.skill_management.gepa_skillopt_adapter as adapters
    from skillwiki.api.component_demo import DIALOGUE_TASK_INSTRUCTION
    monkeypatch.setattr(adapters, "run_gepa_reflection", lambda **kwargs: {"status":"blocked_controls", "candidate_text":"old instruction"})
    parent = await source_parent(demo)
    result = await run_component(demo, "gepa", parent.skill_id)
    assert result["status"] == "blocked"
    assert result["task_instruction"] == DIALOGUE_TASK_INSTRUCTION
    assert "not_official" in result["task_scope"]
    assert demo.llm.messages
    for messages in demo.llm.messages:
        user = messages[1].content
        assert user.startswith(DIALOGUE_TASK_INSTRUCTION + "\nDialogue:\n")
        assert "expected_graph" not in user
