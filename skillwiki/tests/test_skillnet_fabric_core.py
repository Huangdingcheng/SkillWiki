from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException

from skillwiki.layers.skill_runtime import skillnet_fabric_adapter as fabric_adapter
from skillwiki.layers.skill_runtime.skillnet_fabric_adapter import retrieve_with_fabric
from skillwiki.api.routes.execution import _persist_retrieval_trace, get_retrieval_trace
from skillwiki.api.schemas import RetrievalTrace
from skillwiki.api.schemas import ExecutePlanRequest
from skillwiki.api.routes import execution
from skillwiki.models.skill_model import Skill, SkillInterface, SkillImplementation, SkillState
from skillwiki.layers.skill_runtime.planner import ExecutionPlan, PlanStep, StepStatus


def _skill(skill_id: str, name: str, description: str) -> SimpleNamespace:
    return SimpleNamespace(
        skill_id=skill_id,
        name=name,
        description=description,
        tags=[],
        implementation=None,
    )


def test_fabric_core_retrieval_is_pinned_and_deterministic() -> None:
    skills = [
        _skill("skill-a", "parse_pdf", "parse PDF documents and extract text"),
        _skill("skill-b", "send_email", "send an email message"),
    ]
    first = retrieve_with_fabric("parse PDF", skills, limit=2)
    second = retrieve_with_fabric("parse PDF", skills, limit=2)
    assert first.source_commit == "e0bc52856e8244ef6eecc0fe8c1b5f481b4190a3"
    assert first.embedding_mode == "deterministic_local_fixture"
    assert first.candidates == second.candidates
    assert first.candidates[0]["skill_id"] == "skill-a"


def test_packaged_fabric_files_match_recorded_provenance() -> None:
    import hashlib
    import json

    source = fabric_adapter._fabric_source()
    manifest = json.loads((source / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert manifest["commit"] == fabric_adapter.FABRIC_COMMIT
    assert (source / "LICENSE").is_file()
    for row in manifest["files"] + manifest["package_initializers"]:
        assert hashlib.sha256((source / row["path"]).read_bytes()).hexdigest() == row["packaged_sha256"]


def test_released_prompt_skill_is_runtime_eligible():
    skill = Skill(name="dialogue_instruction", state=SkillState.RELEASED,
        implementation=SkillImplementation(prompt_template="Return a dialogue graph"))
    assert execution._is_runtime_planning_skill(skill)


def test_fabric_core_expands_supported_graph_relation_with_reason() -> None:
    skills = [
        _skill("skill-a", "parse_pdf", "parse PDF documents"),
        _skill("skill-b", "extract_text", "extract text from documents"),
    ]
    edge = SimpleNamespace(
        source_id="skill-a",
        target_id="skill-b",
        edge_type="composes_with",
        confidence=0.9,
        description="PDF parsing composes with text extraction",
    )
    result = retrieve_with_fabric("parse PDF", skills, [edge], limit=2)
    expanded = next(item for item in result.candidates if item["skill_id"] == "skill-b")
    assert expanded["is_seed"] is False
    assert expanded["paths"][0]["steps"][0]["reason"] == edge.description


def test_retrieval_trace_survives_reload_from_jsonl() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = SimpleNamespace(execution_trace_path=Path(temp_dir) / "traces.jsonl")
        trace = RetrievalTrace(
            adapter="skillnet-fabric-core",
            source_commit="e0bc52856e8244ef6eecc0fe8c1b5f481b4190a3",
            embedding_model="skillwiki/local-hash-embedding-v1",
            embedding_mode="deterministic_local_fixture",
            candidates=[{"skill_id": "skill-a", "version": "1.0.0"}],
        )
        _persist_retrieval_trace(
            app=app,
            execution_id="execution-1",
            goal="parse PDF",
            trace=trace.model_dump(mode="json"),
        )
        restored = asyncio.run(get_retrieval_trace("execution-1", app))
        assert restored == trace


def test_unmapped_fabric_candidate_blocks_before_planning() -> None:
    released = Skill(
        name="parse_pdf",
        description="parse PDF documents",
        state=SkillState.RELEASED,
        interface=SkillInterface(input_schema={}, output_schema={}),
        implementation=SkillImplementation(code="return {}"),
    )

    class Wiki:
        async def list(self, **_kwargs):
            return [released]

    class Tracker:
        current = {}

        def update(self, _value):
            return None

    app = SimpleNamespace(
        wiki=Wiki(),
        graph=None,
        state_tracker=Tracker(),
        fabric_retriever=lambda *_args, **_kwargs: SimpleNamespace(
            candidates=[{"skill_id": "not-released", "score": 1.0, "is_seed": True, "graph_depth": 0}],
            alternatives=[],
            source_commit="e0bc52856e8244ef6eecc0fe8c1b5f481b4190a3",
            embedding_model="fixture",
            embedding_mode="deterministic_local_fixture",
        ),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(execution.execute_plan(ExecutePlanRequest(goal="parse PDF"), app=app))
    assert exc.value.status_code == 409


def test_fabric_provider_failure_blocks_before_planning() -> None:
    class Wiki:
        async def list(self, **_kwargs):
            return []

    class Tracker:
        current = {}

        def update(self, _value):
            return None

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    app = SimpleNamespace(
        wiki=Wiki(),
        graph=None,
        state_tracker=Tracker(),
        fabric_retriever=unavailable,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(execution.execute_plan(ExecutePlanRequest(goal="parse PDF"), app=app))
    assert exc.value.status_code == 503


def test_frozen_catalog_rejects_missing_or_draft_before_retrieval(tmp_path) -> None:
    class Wiki:
        async def list(self, **kwargs):
            return []
    app = SimpleNamespace(wiki=Wiki(), state_tracker=SimpleNamespace(current={}, update=lambda x: None),
        execution_trace_path=tmp_path / "trace.jsonl", fabric_retriever=lambda *a, **k: pytest.fail("must not retrieve"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(execution.execute_plan(ExecutePlanRequest(goal="parse", skill_ids=["draft-or-missing"]), app=app))
    assert exc.value.status_code == 409
    assert exc.value.detail["trace_id"]
    assert "unreleased" in (tmp_path / "trace.jsonl").read_text()


def test_api_keeps_fabric_candidate_version_in_result_and_trace() -> None:
    released = Skill(
        name="parse_pdf",
        description="parse PDF documents",
        state=SkillState.RELEASED,
        interface=SkillInterface(input_schema={}, output_schema={}),
        implementation=SkillImplementation(code="return {}"),
    )

    class Wiki:
        async def list(self, **_kwargs):
            return [released]

        async def get_many(self, ids):
            return {skill_id: released if skill_id == released.skill_id else None for skill_id in ids}

        async def record_execution(self, *_args, **_kwargs):
            return None

    class Tracker:
        current = {}

        def update(self, value):
            self.current.update(value)

    class Planner:
        async def plan(self, **_kwargs):
            return ExecutionPlan(
                plan_id="trace-version-plan",
                task_id="trace-version-plan",
                task_description="parse PDF",
                steps=[PlanStep(step_index=0, skill_id=released.skill_id, skill_name=released.name)],
            )

    class Executor:
        async def execute_plan(self, plan, **_kwargs):
            plan.steps[0].status = StepStatus.SUCCESS
            plan.steps[0].result = {"ok": True}
            return {"done": True}

    app = SimpleNamespace(
        wiki=Wiki(),
        graph=None,
        state_tracker=Tracker(),
        planner=Planner(),
        executor=Executor(),
        execution_trace_path=Path(tempfile.mkdtemp()) / "traces.jsonl",
        fabric_retriever=lambda *_args, **_kwargs: SimpleNamespace(
            candidates=[{"skill_id": released.skill_id, "name": released.name, "score": 1.0, "is_seed": True, "graph_depth": 0}],
            alternatives=[],
            source_commit="e0bc52856e8244ef6eecc0fe8c1b5f481b4190a3",
            embedding_model="fixture",
            embedding_mode="deterministic_local_fixture",
        ),
    )
    result = asyncio.run(execution.execute_plan(ExecutePlanRequest(goal="parse PDF"), app=app))
    assert result.retrieved_skills[0].version == released.version
    assert result.retrieval_trace.candidates[0]["version"] == released.version
    restored = asyncio.run(get_retrieval_trace(result.plan_id, app))
    assert restored.candidates[0]["version"] == released.version


def test_remote_mode_reads_hermes_config_without_exposing_secret(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env.paperqa.local"
    env_file.write_text(
        "PAPERQA_EMBEDDING_MODEL=openai/text-embedding-v4\n"
        "PAPERQA_OPENAI_API_BASE=https://example.invalid/v1\n"
        "PAPERQA_OPENAI_API_KEY=secret-value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SKILLWIKI_PAPERQA_ENV_FILE", str(env_file))
    monkeypatch.setenv("SKILLWIKI_FABRIC_EMBEDDING_MODE", "remote")
    monkeypatch.setenv("SKILLWIKI_FABRIC_EMBEDDING_ALLOW_REMOTE", "1")
    monkeypatch.setenv("SKILLWIKI_FABRIC_EMBEDDING_BUDGET_LIMIT_CNY", "1")
    monkeypatch.setenv("SKILLWIKI_FABRIC_EMBEDDING_LEDGER_PATH", str(tmp_path / "budget.jsonl"))
    provider, mode = fabric_adapter._remote_embedding_provider()
    assert provider.model_id == "openai/text-embedding-v4"
    assert provider.api_base == "https://example.invalid/v1"
    assert mode == "configured_remote"


def test_remote_mode_fails_closed_without_budget(monkeypatch) -> None:
    monkeypatch.setenv("SKILLWIKI_FABRIC_EMBEDDING_MODE", "remote")
    monkeypatch.delenv("SKILLWIKI_FABRIC_EMBEDDING_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("SKILLWIKI_FABRIC_EMBEDDING_BUDGET_LIMIT_CNY", raising=False)
    monkeypatch.delenv("SKILLWIKI_FABRIC_EMBEDDING_LEDGER_PATH", raising=False)
    with pytest.raises(RuntimeError, match="remote embedding is disabled"):
        fabric_adapter._embedding_provider()


def test_embedding_budget_ledger_reserves_conservatively_and_blocks_overage(monkeypatch, tmp_path) -> None:
    ledger = tmp_path / "embedding.jsonl"
    monkeypatch.setenv("SKILLWIKI_FABRIC_EMBEDDING_BUDGET_LIMIT_CNY", "0.0001")

    class Provider:
        model_id = "openai/text-embedding-v4"
        dimension = 2

        def embed(self, text):
            return [1.0, 0.0]

    guarded = fabric_adapter._BudgetedEmbedding(Provider(), ledger)
    assert guarded.embed("tiny") == [1.0, 0.0]
    with pytest.raises(RuntimeError, match="budget exceeded"):
        guarded.embed("x" * 200)
    row = __import__("json").loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["reserved_cny"] == pytest.approx(8e-6)
