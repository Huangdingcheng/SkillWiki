from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from skillos.api.routes import execution
from skillos.api.schemas import ExecutePlanRequest
from skillos.layers.skill_repository.indexing import SearchResult
from skillos.layers.skill_runtime.executor import SkillExecutor
from skillos.layers.skill_runtime.planner import ExecutionPlan, PlanStep, StepStatus
from skillos.models.skill_model import (
    Skill,
    SkillEvaluation,
    SkillImplementation,
    SkillInterface,
    SkillState,
)


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
    assert result.experience_recorded is True
    assert result.experience_unit is not None
    assert result.experience_unit.source_type == "agent_execution"
    assert result.experience_unit.source_execution_id == result.plan_id
    assert result.experience_unit.metadata["paper_backlog_task"] == "C-P1-2"
    assert result.experience_unit.metadata["paper_method"] == "XSkill action-level experience stream"
    assert result.experience_unit.normalized_actions[0]["skill_id"] == skill.skill_id


@pytest.mark.asyncio
async def test_execute_plan_no_skills_returns_failed_without_crashing():
    app = FakeAppState(skills=[], search_results=[], plan_steps=[])

    result = await execution.execute_plan(ExecutePlanRequest(goal="unknown task"), app=app)

    assert result.status == "failed"
    assert result.steps == []
    assert result.retrieved_skills == []
    assert result.verifier_summary is None


@pytest.mark.asyncio
async def test_execute_plan_attaches_deterministic_verifier_summary():
    skill = make_skill()
    skill.evaluation = SkillEvaluation(
        verifier_specs=[{"type": "json_equals", "path": "output.ok", "value": True}]
    )
    app = FakeAppState(
        skills=[skill],
        search_results=[SearchResult(skill=skill, score=0.9, match_reasons=["name match"])],
        plan_steps=[PlanStep(step_index=0, skill_id=skill.skill_id, skill_name=skill.name)],
    )

    result = await execution.execute_plan(ExecutePlanRequest(goal="fill form"), app=app)

    assert result.verifier_passed is True
    assert result.verifier_summary is not None
    assert result.verifier_summary["mode"] == "deterministic"
    assert result.verifier_summary["checked_skills"] == 1
    assert result.verifier_summary["results"][0]["skill_id"] == skill.skill_id


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
async def test_execution_history_returns_full_experience_unit_for_plan():
    original = list(execution._execution_history)
    execution._execution_history.clear()
    try:
        skill = make_skill()
        app = FakeAppState(
            skills=[skill],
            search_results=[SearchResult(skill=skill, score=0.9, match_reasons=["name match"])],
            plan_steps=[PlanStep(
                step_index=0,
                skill_id=skill.skill_id,
                skill_name=skill.name,
                input_mapping={"field": "email"},
            )],
        )

        result = await execution.execute_plan(ExecutePlanRequest(goal="fill login form"), app=app)
        history = await execution.get_execution_history()
        unit = await execution.get_execution_experience(result.plan_id)

        assert history[0]["execution_id"] == result.plan_id
        assert history[0]["experience_unit_id"] == unit.unit_id
        assert history[0]["experience_source_type"] == "agent_execution"
        assert unit.source_execution_id == result.plan_id
        assert unit.source_type == "agent_execution"
        assert unit.normalized_actions[0]["input_mapping"] == {"field": "email"}
        assert unit.proposed_skill_name == "skill_from_fill_login_form"
        assert unit.metadata["paper_backlog_task"] == "C-P1-2"
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
    def __init__(self, skills: list[Skill], search_results: list[SearchResult], plan_steps: list[PlanStep]) -> None:
        self.state_tracker = FakeStateTracker()
        self.wiki = FakeWiki(skills)
        self.search = FakeSearch(search_results)
        self.planner = FakePlanner(plan_steps)
        self.executor = FakeExecutor()
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

    async def search(self, query: object) -> list[SearchResult]:
        return self.results


class FakePlanner:
    def __init__(self, steps: list[PlanStep]) -> None:
        self.steps = steps

    async def plan(self, task_description: str, available_skills: list[Skill], current_state: dict) -> ExecutionPlan:
        return ExecutionPlan(plan_id="plan-1", task_id="plan-1", task_description=task_description, steps=self.steps)


class FakeExecutor:
    async def execute_plan(self, plan: ExecutionPlan, skill_map: dict[str, Skill], initial_state: dict) -> dict:
        for step in plan.steps:
            if step.skill_id in skill_map:
                step.status = StepStatus.SUCCESS
                step.result = {"ok": True}
            else:
                step.status = StepStatus.FAILED
                step.error = "missing skill"
        return {"done": True}
