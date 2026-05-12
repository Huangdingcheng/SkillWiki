from __future__ import annotations

import asyncio
import json

import pytest

from skillos.layers.skill_runtime.composition import CompositionAgent
from skillos.layers.skill_runtime.executor import SkillExecutor
from skillos.layers.skill_runtime.planner import ExecutionPlan, PlanStep, StepStatus
from skillos.layers.skill_runtime.retriever import SkillGroup
from skillos.models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState


def make_skill(
    name: str,
    code: str,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> Skill:
    return Skill(
        name=name,
        description=f"{name} test skill",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {key: {"type": "string"} for key in (inputs or [])},
            },
            output_schema={
                "type": "object",
                "properties": {key: {"type": "string"} for key in (outputs or [])},
            },
        ),
        implementation=SkillImplementation(code=code),
    )


def make_plan(steps: list[PlanStep]) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan-1",
        task_id="plan-1",
        task_description="run stable plan",
        steps=steps,
    )


def test_composer_builds_dag_from_skill_group():
    prepare = make_skill("prepare", "output['data'] = 'x'")
    run = make_skill("run", "output['result'] = 'ok'")
    check = make_skill("check", "output['ok'] = True")
    avoid = make_skill("avoid", "output['bad'] = True")
    group = SkillGroup(
        anchor_skill_id=run.skill_id,
        support_skill_ids=[prepare.skill_id],
        start_skill_ids=[run.skill_id],
        check_skill_ids=[check.skill_id],
        avoid_skill_ids=[avoid.skill_id],
        rationale="support then run then check",
    )

    graph = CompositionAgent(FakeLLM("{}")).compose(
        [prepare, run, check, avoid],
        "run checked flow",
        skill_group=group,
    )

    assert [node.skill_id for node in graph.nodes] == [
        prepare.skill_id,
        run.skill_id,
        check.skill_id,
    ]
    assert graph.entry_skill_id == run.skill_id
    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == [
        (prepare.skill_id, run.skill_id),
        (run.skill_id, check.skill_id),
    ]
    assert graph.metadata["composition_source"] == "skill_group"


def test_composer_filters_invalid_edges_and_breaks_cycles():
    first = make_skill("first", "output['a'] = 1")
    second = make_skill("second", "output['b'] = 1")
    payload = {
        "entry_skill_id": "missing",
        "edges": [
            {"source_id": first.skill_id, "target_id": second.skill_id, "edge_type": "sequence"},
            {"source_id": second.skill_id, "target_id": first.skill_id, "edge_type": "sequence"},
            {"source_id": first.skill_id, "target_id": "missing", "edge_type": "sequence"},
            {"source_id": first.skill_id, "target_id": first.skill_id, "edge_type": "sequence"},
        ],
    }

    graph = CompositionAgent(FakeLLM(json.dumps(payload))).compose([first, second])

    assert graph.entry_skill_id == first.skill_id
    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == [
        (first.skill_id, second.skill_id)
    ]
    assert graph.execution_order == [first.skill_id, second.skill_id]


def test_composer_uses_schema_fallback_before_sequential_fallback():
    extract = make_skill("extract", "output['data'] = 'x'", outputs=["data"])
    submit = make_skill("submit", "output['ok'] = True", inputs=["data"])

    graph = CompositionAgent(FailingLLM()).compose([extract, submit])

    assert graph.metadata["composition_source"] == "schema_fallback"
    assert [(edge.source_id, edge.target_id, edge.data_mapping) for edge in graph.edges] == [
        (extract.skill_id, submit.skill_id, {"data": "data"})
    ]
    assert graph.parallel_groups == [[extract.skill_id], [submit.skill_id]]


def test_independent_step_can_succeed_after_parallel_step_fails():
    fail_skill = make_skill("fail_skill", "raise RuntimeError('boom')")
    ok_skill = make_skill("ok_skill", "output['ok'] = True")
    failing_step = PlanStep(
        step_index=0,
        skill_id=fail_skill.skill_id,
        skill_name=fail_skill.name,
    )
    success_step = PlanStep(
        step_index=1,
        skill_id=ok_skill.skill_id,
        skill_name=ok_skill.name,
    )
    plan = make_plan([failing_step, success_step])
    events: list[tuple[str, dict]] = []
    executor = SkillExecutor(max_retries=0)
    executor.add_event_callback(lambda event_type, data: events.append((event_type, data)))

    final_state = asyncio.run(
        executor.execute_plan(
            plan,
            {fail_skill.skill_id: fail_skill, ok_skill.skill_id: ok_skill},
            {},
        )
    )

    assert failing_step.status == StepStatus.FAILED
    assert success_step.status == StepStatus.SUCCESS
    assert final_state["ok_skill_executed"] is True
    assert executor.last_runtime_memory is not None
    assert executor.last_runtime_memory.to_summary()["selected_skills"] == [
        fail_skill.skill_id,
        ok_skill.skill_id,
    ]
    assert executor.last_runtime_memory.to_summary()["failure_count"] == 1
    assert _event(events, "plan_completed")["status"] == "partial"


def test_dependent_step_is_skipped_when_dependency_fails():
    fail_skill = make_skill("fail_skill", "raise RuntimeError('boom')")
    dependent_skill = make_skill("dependent_skill", "output['ok'] = True")
    failing_step = PlanStep(
        step_index=0,
        skill_id=fail_skill.skill_id,
        skill_name=fail_skill.name,
    )
    dependent_step = PlanStep(
        step_index=1,
        skill_id=dependent_skill.skill_id,
        skill_name=dependent_skill.name,
        depends_on=[failing_step.step_id],
    )
    plan = make_plan([failing_step, dependent_step])
    events: list[tuple[str, dict]] = []
    executor = SkillExecutor(max_retries=0)
    executor.add_event_callback(lambda event_type, data: events.append((event_type, data)))

    asyncio.run(
        executor.execute_plan(
            plan,
            {fail_skill.skill_id: fail_skill, dependent_skill.skill_id: dependent_skill},
            {},
        )
    )

    assert failing_step.status == StepStatus.FAILED
    assert dependent_step.status == StepStatus.SKIPPED
    assert failing_step.step_id in dependent_step.error
    assert executor.last_runtime_memory is not None
    assert executor.last_runtime_memory.to_summary()["failure_count"] == 2
    skipped_event = _event(events, "step_skipped")
    assert skipped_event["step_id"] == dependent_step.step_id
    assert skipped_event["failed_dependency"] == failing_step.step_id
    assert _event(events, "plan_completed")["status"] == "failed"


def test_skip_cascades_through_dependency_chain():
    fail_skill = make_skill("fail_skill", "raise RuntimeError('boom')")
    middle_skill = make_skill("middle_skill", "output['ok'] = True")
    leaf_skill = make_skill("leaf_skill", "output['ok'] = True")
    first = PlanStep(step_index=0, skill_id=fail_skill.skill_id, skill_name=fail_skill.name)
    middle = PlanStep(
        step_index=1,
        skill_id=middle_skill.skill_id,
        skill_name=middle_skill.name,
        depends_on=[first.step_id],
    )
    leaf = PlanStep(
        step_index=2,
        skill_id=leaf_skill.skill_id,
        skill_name=leaf_skill.name,
        depends_on=[middle.step_id],
    )
    plan = make_plan([first, middle, leaf])
    executor = SkillExecutor(max_retries=0)

    asyncio.run(
        executor.execute_plan(
            plan,
            {
                fail_skill.skill_id: fail_skill,
                middle_skill.skill_id: middle_skill,
                leaf_skill.skill_id: leaf_skill,
            },
            {},
        )
    )

    assert first.status == StepStatus.FAILED
    assert middle.status == StepStatus.SKIPPED
    assert leaf.status == StepStatus.SKIPPED
    assert middle.step_id in leaf.error


def test_missing_skill_failure_has_timestamps_and_event_payload():
    step = PlanStep(step_index=0, skill_id="missing", skill_name="missing_skill")
    plan = make_plan([step])
    events: list[tuple[str, dict]] = []
    executor = SkillExecutor(max_retries=0)
    executor.add_event_callback(lambda event_type, data: events.append((event_type, data)))

    asyncio.run(executor.execute_plan(plan, {}, {}))

    assert step.status == StepStatus.FAILED
    assert step.started_at is not None
    assert step.completed_at is not None
    assert step.latency_ms is not None
    failed_event = _event(events, "step_failed")
    assert failed_event["step_index"] == 0
    assert failed_event["skill_id"] == "missing"
    assert failed_event["latency_ms"] is not None


def test_step_timeout_fails_and_rolls_back_state():
    slow_skill = make_skill("slow_skill", "output['ok'] = True")
    step = PlanStep(step_index=0, skill_id=slow_skill.skill_id, skill_name=slow_skill.name)
    plan = make_plan([step])
    executor = SlowExecutor(max_retries=0, step_timeout_s=0.01)

    final_state = asyncio.run(executor.execute_plan(plan, {slow_skill.skill_id: slow_skill}, {"before": True}))

    assert step.status == StepStatus.FAILED
    assert "timed out" in step.error
    assert final_state == {"before": True}


def test_global_timeout_preserves_completed_parallel_steps_and_stops_pending_ones():
    slow_skill = make_skill("slow_skill", "output['ok'] = True")
    fast_skill = make_skill("fast_skill", "output['done'] = True")
    slow_step = PlanStep(step_index=0, skill_id=slow_skill.skill_id, skill_name=slow_skill.name)
    fast_step = PlanStep(step_index=1, skill_id=fast_skill.skill_id, skill_name=fast_skill.name)
    plan = make_plan([slow_step, fast_step])
    events: list[tuple[str, dict]] = []
    executor = GlobalTimeoutExecutor(max_retries=0, step_timeout_s=0.5, global_timeout_s=0.1)
    executor.add_event_callback(lambda event_type, data: events.append((event_type, data)))

    final_state = asyncio.run(
        executor.execute_plan(
            plan,
            {slow_skill.skill_id: slow_skill, fast_skill.skill_id: fast_skill},
            {"before": True},
        )
    )

    assert fast_step.status == StepStatus.SUCCESS
    assert slow_step.status == StepStatus.FAILED
    assert "global timeout" in slow_step.error
    assert _event(events, "plan_timed_out")["plan_id"] == plan.plan_id
    assert _event(events, "plan_completed")["status"] == "partial"
    assert final_state["before"] is True


def _event(events: list[tuple[str, dict]], event_type: str) -> dict:
    matches = [data for event, data in events if event == event_type]
    assert matches, f"missing event {event_type}"
    return matches[-1]


class SlowExecutor(SkillExecutor):
    async def _run_skill(self, skill: Skill, input_data: dict, current_state: dict) -> dict:
        import asyncio

        await asyncio.sleep(0.05)
        return {"ok": True, "_state_changes": {"ok": True}}


class GlobalTimeoutExecutor(SkillExecutor):
    async def _run_skill(self, skill: Skill, input_data: dict, current_state: dict) -> dict:
        import asyncio

        if skill.name == "slow_skill":
            await asyncio.sleep(0.25)
            return {"ok": True, "_state_changes": {"ok": True}}
        return {"done": True, "_state_changes": {"done": True}}


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages: object) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(content=self.content)


class FailingLLM:
    def chat(self, messages: object) -> object:
        raise RuntimeError("llm unavailable")
