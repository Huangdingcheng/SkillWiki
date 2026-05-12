from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from skillos.layers.skill_repository.indexing import SearchResult
from skillos.layers.skill_runtime.planner import SkillPlanner, _PLAN_PROMPT
from skillos.layers.skill_runtime.retriever import (
    RetrievalStrategy,
    SkillGroup,
    SkillRetriever,
    _RETRIEVAL_PROMPT,
)
from skillos.models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState


def make_skill(name: str, description: str | None = None) -> Skill:
    return Skill(
        name=name,
        description=description if description is not None else f"{name} test skill",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
        ),
        implementation=SkillImplementation(code="output['ok'] = True"),
    )


def result(skill: Skill, score: float = 0.8) -> SearchResult:
    return SearchResult(skill=skill, score=score, match_reasons=["test match"])


@pytest.mark.asyncio
async def test_planner_invalid_json_falls_back_to_top_five_skills():
    skills = [make_skill(f"skill_{i}") for i in range(6)]
    planner = SkillPlanner(FakeLLM("not json"))

    plan = await planner.plan("do the task", skills)

    assert plan.metadata["source"] == "fallback"
    assert len(plan.steps) == 5
    assert [step.step_index for step in plan.steps] == [0, 1, 2, 3, 4]
    assert plan.steps[0].depends_on == []
    assert plan.steps[1].depends_on == [plan.steps[0].step_id]


@pytest.mark.asyncio
async def test_planner_normalizes_llm_steps_and_drops_invalid_skill_ids():
    extract = make_skill("extract_data", "")
    submit = make_skill("submit_result")
    payload = {
        "steps": [
            {
                "step_index": 10,
                "skill_id": extract.skill_id,
                "skill_name": "",
                "description": "",
                "input_mapping": ["bad"],
                "depends_on": ["missing"],
            },
            {
                "step_index": 20,
                "skill_id": submit.skill_id,
                "skill_name": submit.name,
                "description": "Submit data",
                "input_mapping": {"data": "${step_0.result}"},
                "depends_on": ["10"],
            },
            {
                "step_index": 30,
                "skill_id": "missing",
                "skill_name": "missing",
                "description": "Should be skipped",
                "input_mapping": {},
                "depends_on": [],
            },
        ],
        "plan_rationale": "Use extract then submit.",
    }
    planner = SkillPlanner(FakeLLM(json.dumps(payload)))

    plan = await planner.plan("extract and submit", [extract, submit])

    assert len(plan.steps) == 2
    assert [step.step_index for step in plan.steps] == [0, 1]
    assert plan.steps[0].skill_name == extract.name
    assert plan.steps[0].description == f"Execute {extract.name}"
    assert plan.steps[0].input_mapping == {}
    assert plan.steps[0].depends_on == []
    assert plan.steps[1].depends_on == [plan.steps[0].step_id]


@pytest.mark.asyncio
async def test_planner_no_available_skills_returns_empty_plan():
    planner = SkillPlanner(FakeLLM("{}"))

    plan = await planner.plan("unknown task", [])

    assert plan.steps == []


@pytest.mark.asyncio
async def test_planner_llm_exception_uses_fallback_plan():
    skill = make_skill("fallback_skill")
    planner = SkillPlanner(FailingLLM())

    plan = await planner.plan("do fallback", [skill])

    assert plan.metadata["source"] == "fallback"
    assert [step.skill_id for step in plan.steps] == [skill.skill_id]


@pytest.mark.asyncio
async def test_retriever_normalizes_strategy_confidence_and_execution_order():
    first = make_skill("fill_form")
    second = make_skill("submit_form")
    llm_payload = {
        "strategy": "unknown",
        "selected_skill_ids": [second.skill_id, "missing"],
        "execution_order": ["missing", second.skill_id],
        "confidence": 2.5,
        "rationale": "Bad strategy should become reuse.",
        "parameter_mapping": ["bad"],
    }
    retriever = SkillRetriever(
        FakeLLM(json.dumps(llm_payload)),
        FakeSearch([result(first, 0.7), result(second, 0.9)]),
    )

    retrieval = await retriever.retrieve("submit form")

    assert retrieval.strategy == RetrievalStrategy.REUSE
    assert [skill.skill_id for skill in retrieval.skills] == [second.skill_id]
    assert retrieval.execution_order == [second.skill_id]
    assert retrieval.confidence == 1.0
    assert retrieval.parameter_mapping == {}
    assert retrieval.skill_group is not None
    assert retrieval.skill_group.start_skill_ids == [second.skill_id]


@pytest.mark.asyncio
async def test_retriever_falls_back_when_selected_ids_are_missing():
    best = make_skill("best_skill")
    llm_payload = {
        "strategy": "reuse",
        "selected_skill_ids": ["missing"],
        "confidence": 0.8,
    }
    retriever = SkillRetriever(
        FakeLLM(json.dumps(llm_payload)),
        FakeSearch([result(best, 0.76)]),
    )

    retrieval = await retriever.retrieve("use best")

    assert retrieval.strategy == RetrievalStrategy.REUSE
    assert retrieval.skills == [best]
    assert retrieval.execution_order == [best.skill_id]
    assert retrieval.confidence == 0.76
    assert "highest-scoring" in retrieval.rationale
    assert retrieval.skill_group is not None
    assert retrieval.skill_group.anchor_skill_id == best.skill_id


@pytest.mark.asyncio
async def test_retriever_llm_exception_falls_back_to_best_search_result():
    best = make_skill("best_skill")
    retriever = SkillRetriever(FailingLLM(), FakeSearch([result(best, 0.88)]))

    retrieval = await retriever.retrieve("use best")

    assert retrieval.strategy == RetrievalStrategy.REUSE
    assert retrieval.skills == [best]
    assert retrieval.confidence == 0.88


@pytest.mark.asyncio
async def test_retriever_no_search_results_requests_generation():
    retriever = SkillRetriever(FakeLLM("{}"), FakeSearch([]))

    retrieval = await retriever.retrieve("new capability")

    assert retrieval.strategy == RetrievalStrategy.GENERATE
    assert retrieval.needs_generation is True
    assert retrieval.generation_hint == "new capability"
    assert retrieval.skill_group is None


@pytest.mark.asyncio
async def test_retriever_normalizes_skill_group_and_filters_avoid_ids():
    prepare = make_skill("prepare")
    run = make_skill("run")
    check = make_skill("check")
    avoid = make_skill("avoid")
    llm_payload = {
        "strategy": "compose",
        "selected_skill_ids": [prepare.skill_id, run.skill_id, check.skill_id, avoid.skill_id],
        "execution_order": [prepare.skill_id, run.skill_id, check.skill_id, avoid.skill_id],
        "confidence": 0.8,
        "skill_group": {
            "anchor_skill_id": run.skill_id,
            "start_skill_ids": [run.skill_id, "missing"],
            "support_skill_ids": [prepare.skill_id],
            "check_skill_ids": [check.skill_id],
            "avoid_skill_ids": [avoid.skill_id, "missing"],
            "rationale": "structured group",
        },
    }
    retriever = SkillRetriever(
        FakeLLM(json.dumps(llm_payload)),
        FakeSearch([result(prepare), result(run), result(check), result(avoid)]),
    )

    retrieval = await retriever.retrieve("prepare run check")

    assert isinstance(retrieval.skill_group, SkillGroup)
    assert retrieval.skill_group.anchor_skill_id == run.skill_id
    assert retrieval.skill_group.support_skill_ids == [prepare.skill_id]
    assert retrieval.skill_group.start_skill_ids == [run.skill_id]
    assert retrieval.skill_group.check_skill_ids == [check.skill_id]
    assert retrieval.skill_group.avoid_skill_ids == [avoid.skill_id]
    assert retrieval.execution_order == [prepare.skill_id, run.skill_id, check.skill_id]
    assert [skill.skill_id for skill in retrieval.skills] == [
        prepare.skill_id,
        run.skill_id,
        check.skill_id,
    ]


@pytest.mark.asyncio
async def test_retrieve_by_id_requires_exact_skill_id_match():
    fuzzy = make_skill("skill_abc")
    target = make_skill("skill_target")
    search = FakeSearch([result(fuzzy, 0.9), result(target, 0.8)])
    retriever = SkillRetriever(FakeLLM("{}"), search)

    found = await retriever.retrieve_by_id(target.skill_id)
    missing = await retriever.retrieve_by_id("not-a-real-id")

    assert found == target
    assert missing is None


def test_runtime_llm_prompts_are_ascii():
    _PLAN_PROMPT.encode("ascii")
    _RETRIEVAL_PROMPT.encode("ascii")


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages: object) -> SimpleNamespace:
        return SimpleNamespace(content=self.content)


class FailingLLM:
    def chat(self, messages: object) -> SimpleNamespace:
        raise RuntimeError("llm unavailable")


class FakeSearch:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results

    async def search(self, query: object) -> list[SearchResult]:
        return self.results
