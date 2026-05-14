from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional

import pytest

from skillos.api.routes import execution
from skillos.api.schemas import ExecutePlanRequest
from skillos.layers.skill_repository.indexing import SearchResult
from skillos.layers.skill_runtime.composition import SkillGraph
from skillos.layers.skill_runtime.executor import SkillExecutor
from skillos.layers.skill_runtime.planner import ExecutionPlan, PlanStep, StepStatus
from skillos.layers.skill_runtime.retriever import RetrievalResult, RetrievalStrategy, SkillGroup
from skillos.models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState


def make_skill(name: str = "fill_form") -> Skill:
    return Skill(
        name=name,
        description=f"{name} test skill",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
        ),
        implementation=SkillImplementation(code="output['ok'] = True"),
    )


@pytest.mark.asyncio
async def test_execute_plan_formats_match_reasons_and_records_metrics():
    skill = make_skill()
    app = FakeAppState(
        skills=[skill],
        search_results=[SearchResult(skill=skill, score=0.9, match_reasons=["exact name match", "state boost"])],
        plan_steps=[PlanStep(step_index=7, skill_id=skill.skill_id, skill_name=skill.name)],
    )

    result = await execution.execute_plan(ExecutePlanRequest(goal="fill form"), app=app)

    assert result.status == "success"
    assert result.retrieved_skills[0].match_reason == "exact name match; state boost"
    assert result.steps[0].step_index == 7
    assert result.steps[0].outputs == result.steps[0].result
    assert skill.metrics.usage_count == 1
    assert skill.metrics.success_count == 1
    assert app.recorded == [(skill.skill_id, True)]


@pytest.mark.asyncio
async def test_execute_plan_uses_runtime_retriever_skill_group():
    support = make_skill("prepare_customer_data")
    start = make_skill("process_order")
    check = make_skill("validate_order")
    skill_group = SkillGroup(
        anchor_skill_id=start.skill_id,
        support_skill_ids=[support.skill_id],
        start_skill_ids=[start.skill_id],
        check_skill_ids=[check.skill_id],
    )
    app = FakeAppState(
        skills=[support, start, check],
        search_results=[],
        plan_steps=[],
        retrieval=RetrievalResult(
            strategy=RetrievalStrategy.COMPOSE,
            skills=[support, start, check],
            confidence=0.92,
            rationale="structured runtime group",
            skill_group=skill_group,
        ),
    )

    result = await execution.execute_plan(ExecutePlanRequest(goal="process order"), app=app)

    assert result.status == "success"
    assert result.composition_source == "skill_group"
    assert [step.skill_id for step in result.steps] == [
        support.skill_id,
        start.skill_id,
        check.skill_id,
    ]
    assert result.steps[1].status == "success"
    assert result.retrieved_skills[0].match_reason == "structured runtime group"
    assert result.execution_graph is not None
    assert result.execution_graph["composition_source"] == "skill_group"
    assert any(node["kind"] == "execution_step" for node in result.execution_graph["nodes"])
    assert any(edge["kind"] == "depends_on" for edge in result.execution_graph["edges"])
    assert app.search.calls == 0


@pytest.mark.asyncio
async def test_execute_plan_no_skills_returns_failed_without_crashing():
    app = FakeAppState(skills=[], search_results=[], plan_steps=[])

    result = await execution.execute_plan(ExecutePlanRequest(goal="unknown task"), app=app)

    assert result.status == "failed"
    assert result.steps == []
    assert result.retrieved_skills == []


@pytest.mark.asyncio
async def test_execute_plan_returns_verification_and_reflection_feedback():
    missing_step = PlanStep(step_index=0, skill_id="missing", skill_name="missing")
    app = FakeAppState(
        skills=[],
        search_results=[],
        plan_steps=[missing_step],
        verifier=FakeVerifier(),
        reflector=FakeReflector(),
    )

    result = await execution.execute_plan(ExecutePlanRequest(goal="missing capability"), app=app)

    assert result.status == "failed"
    assert result.failure_type == "missing_skill"
    assert result.recovery_route == "retrieve_alternative_skill"
    assert result.verification is not None
    assert result.verification["passed"] is False
    assert result.reflection is not None
    assert result.reflection["failed_skill_ids"] == ["missing"]
    assert result.runtime_memory is not None
    assert result.runtime_memory["task_id"] == "plan-1"


@pytest.mark.asyncio
async def test_execution_history_returns_items_in_reverse_order():
    original = list(execution._execution_history)
    execution._execution_history.clear()
    try:
        execution._execution_history.extend([
            {
                "execution_id": "old",
                "goal": "old goal",
                "status": "failed",
                "step_count": 0,
                "success_count": 0,
                "total_latency_ms": 1.0,
                "retrieved_skill_count": 0,
                "created_at": "2026-05-04T00:00:00",
            },
            {
                "execution_id": "new",
                "goal": "new goal",
                "status": "success",
                "step_count": 1,
                "success_count": 1,
                "total_latency_ms": 2.0,
                "retrieved_skill_count": 1,
                "created_at": "2026-05-04T00:01:00",
            },
        ])

        history = await execution.get_execution_history()

        assert [item["execution_id"] for item in history] == ["new", "old"]
    finally:
        execution._execution_history[:] = original


@pytest.mark.asyncio
async def test_executor_schedules_async_callbacks_and_ignores_callback_errors():
    executor = SkillExecutor()
    received: list[tuple[str, dict]] = []

    async def async_callback(event_type: str, data: dict) -> None:
        received.append((event_type, data))

    def failing_callback(event_type: str, data: dict) -> None:
        raise RuntimeError("callback failed")

    executor.add_event_callback(async_callback)
    executor.add_event_callback(failing_callback)
    executor._emit("plan_completed", {"plan_id": "plan-1"})
    await asyncio.sleep(0)

    assert received == [("plan_completed", {"plan_id": "plan-1"})]


class FakeAppState:
    def __init__(
        self,
        skills: list[Skill],
        search_results: list[SearchResult],
        plan_steps: list[PlanStep],
        retrieval: Optional[RetrievalResult] = None,
        verifier: object = None,
        reflector: object = None,
    ) -> None:
        self.state_tracker = FakeStateTracker()
        self.wiki = FakeWiki(skills)
        self.search = FakeSearch(search_results)
        self.retriever = FakeRetriever(retrieval) if retrieval else None
        self.composer = FakeComposer()
        self.planner = FakePlanner(plan_steps)
        self.executor = FakeExecutor()
        self.verifier = verifier
        self.reflector = reflector
        self.recorded = self.wiki.recorded


class FakeStateTracker:
    def __init__(self) -> None:
        self.current: dict = {}

    def update(self, changes: dict) -> None:
        self.current.update(changes)


class FakeWiki:
    def __init__(self, skills: list[Skill]) -> None:
        self.skills = {skill.skill_id: skill for skill in skills}
        self.recorded: list[tuple[str, bool]] = []

    async def get_many(self, skill_ids: list[str]) -> dict[str, Skill | None]:
        return {skill_id: self.skills.get(skill_id) for skill_id in skill_ids}

    async def record_execution(self, skill_id: str, success: bool, latency_ms: float) -> None:
        self.recorded.append((skill_id, success))
        skill = self.skills.get(skill_id)
        if skill:
            skill.record_execution(success, latency_ms)


class FakeSearch:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.calls = 0

    async def search(self, query: object) -> list[SearchResult]:
        self.calls += 1
        return self.results


class FakeRetriever:
    def __init__(self, retrieval: RetrievalResult) -> None:
        self.retrieval = retrieval

    async def retrieve(
        self,
        task_description: str,
        current_state: Optional[dict] = None,
        domain: Optional[str] = None,
    ) -> RetrievalResult:
        return self.retrieval


class FakeComposer:
    def compose(
        self,
        skills: list[Skill],
        task_description: str = "",
        skill_group: Optional[SkillGroup] = None,
        strategy: object = None,
    ) -> SkillGraph:
        if not skill_group:
            return SkillGraph(graph_id="graph-1", task_description=task_description)
        graph = SkillGraph(
            graph_id="graph-1",
            task_description=task_description,
            nodes=list(skills),
            entry_skill_id=skills[0].skill_id if skills else "",
        )
        if skill_group.support_skill_ids and skill_group.start_skill_ids:
            graph.metadata["composition_source"] = "skill_group"
            graph.edges.append(SimpleNamespace(
                source_id=skill_group.support_skill_ids[0],
                target_id=skill_group.start_skill_ids[0],
                edge_type="sequence",
                data_mapping={},
            ))
            if skill_group.check_skill_ids:
                graph.edges.append(SimpleNamespace(
                    source_id=skill_group.start_skill_ids[0],
                    target_id=skill_group.check_skill_ids[0],
                    edge_type="sequence",
                    data_mapping={},
                ))
        return graph


class FakePlanner:
    def __init__(self, steps: list[PlanStep]) -> None:
        self.steps = steps

    async def plan(self, task_description: str, available_skills: list[Skill], current_state: dict) -> ExecutionPlan:
        return ExecutionPlan(plan_id="plan-1", task_id="plan-1", task_description=task_description, steps=self.steps)


class FakeVerifier:
    def verify(self, goal: str, final_output: dict, trace_summary: str) -> SimpleNamespace:
        return SimpleNamespace(
            passed=False,
            score=0.2,
            issues=["missing skill"],
            suggestions=["retrieve alternative skill"],
            failure_type="missing_skill",
            recovery_route="retrieve_alternative_skill",
        )


class FakeReflector:
    def reflect(
        self,
        task_id: str,
        goal: str,
        trace: dict,
        verification_result: object = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            root_cause="missing runtime skill",
            failure_type="missing_skill",
            recovery_route="retrieve_alternative_skill",
            failed_skill_ids=["missing"],
            improvement_suggestions=["add or retrieve a compatible skill"],
            skill_update_proposals=[],
        )


class FakeExecutor:
    def __init__(self) -> None:
        self.last_runtime_memory: object = None

    async def execute_plan(self, plan: ExecutionPlan, skill_map: dict[str, Skill], initial_state: dict) -> dict:
        for step in plan.steps:
            if step.skill_id in skill_map:
                step.status = StepStatus.SUCCESS
                step.result = {"ok": True}
            else:
                step.status = StepStatus.FAILED
                step.error = "missing skill"
        self.last_runtime_memory = FakeMemory(plan.task_id)
        return {"done": True}


class FakeMemory:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.verification_summary: dict = {}
        self.reflection_summary: dict = {}

    def to_summary(self) -> dict:
        return {
            "task_id": self.task_id,
            "verification": self.verification_summary,
            "reflection": self.reflection_summary,
        }
