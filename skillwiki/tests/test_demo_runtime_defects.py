"""Regression tests for the bounded Demo runtime contracts.

These tests use fixed local responses.  They do not claim model quality; they
exercise the executor's business-output, state, and dependency boundaries.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes import execution
from skillwiki.layers.skill_runtime.state_tracker import StateTracker
from skillwiki.layers.skill_runtime.planner import ExecutionPlan, PlanStep
from skillwiki.layers.skill_runtime.verifier import evaluate_verifier_specs

from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.experience_model import ExecutionStatus
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState, SkillType


class FixedLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages):  # noqa: ANN001 - fixed test double for the external client
        return SimpleNamespace(content=self.content)


def prompt_skill(template: str = "Return JSON") -> Skill:
    return Skill(
        name="runtime_contract",
        description="A runtime contract test skill.",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
        ),
        implementation=SkillImplementation(prompt_template=template),
    )


@pytest.mark.asyncio
async def test_prompt_business_fields_are_not_replaced_by_runtime_metadata():
    content = json.dumps({"skill_name": "business-name", "_state_changes": {"business": 7}, "answer": "ok"})
    record = await SkillExecutor(llm_client=FixedLLM(content)).execute_single(
        prompt_skill(), {"question": "read"}, initial_state={"before": True}
    )

    assert record.status is ExecutionStatus.SUCCESS
    assert record.output_data == {"skill_name": "business-name", "_state_changes": {"business": 7}, "answer": "ok"}
    assert record.state_before == {"before": True}
    assert record.state_after["runtime_contract_result"] == record.output_data


@pytest.mark.asyncio
async def test_missing_llm_is_a_failed_execution_and_never_mock_success():
    record = await SkillExecutor(llm_client=None).execute_single(prompt_skill(), {})

    assert record.status is ExecutionStatus.FAILED
    assert record.output_data is None
    assert "LLM" in (record.error_message or "")


@pytest.mark.asyncio
async def test_missing_composite_child_fails_instead_of_silently_skipping():
    parent = Skill(
        name="composite_parent",
        description="A composite contract test skill.",
        state=SkillState.RELEASED,
        interface=SkillInterface(),
        implementation=SkillImplementation(sub_skill_ids=["missing-child"]),
    )

    class EmptyRegistry:
        async def get(self, skill_id: str):
            return None

    record = await SkillExecutor(skill_registry=EmptyRegistry()).execute_single(parent, {})
    assert record.status is ExecutionStatus.FAILED
    assert "missing-child" in (record.error_message or "")


@pytest.mark.asyncio
async def test_tool_only_skill_is_explicitly_unsupported():
    skill = Skill(
        name="tool_only",
        description="A tool only contract test skill.",
        state=SkillState.RELEASED,
        skill_type=SkillType.ATOMIC,
        interface=SkillInterface(),
        implementation=SkillImplementation(tool_calls=["external_tool"]),
    )
    record = await SkillExecutor().execute_single(skill, {})
    assert record.status is ExecutionStatus.FAILED
    assert "tool" in (record.error_message or "").lower()
    assert "unsupported" in (record.error_message or "").lower()


def test_auditor_does_not_treat_tool_only_as_executable():
    skill = Skill(
        name="tool_only",
        description="A tool only contract test skill.",
        state=SkillState.DRAFT,
        skill_type=SkillType.ATOMIC,
        interface=SkillInterface(),
        implementation=SkillImplementation(tool_calls=["external_tool"]),
    )
    result = SkillAuditorAgent().audit(skill)
    assert result.postcondition_ok is False
    assert any("tool" in issue.lower() for issue in result.issues)


def test_prompt_variable_parser_ignores_literal_json_braces():
    skill = prompt_skill('{"example": "{literal}"} then {task}')
    skill.interface.input_schema = {"type": "object", "properties": {"task": {"type": "string"}}}
    result = SkillAuditorAgent().audit(skill)
    assert not any("literal" in issue for issue in result.issues)
    assert not any("task" in issue for issue in result.issues)


def client_for(tmp_path, skill, response):
    wiki = MemoryWikiManager()
    wiki._store[skill.skill_id] = skill
    app = SimpleNamespace(wiki=wiki, executor=SkillExecutor(wiki, FixedLLM(response)),
                          state_tracker=StateTracker("session", {"session": "unchanged"}),
                          verifier=None, execution_trace_path=tmp_path / "traces.jsonl")
    web = FastAPI()
    web.include_router(execution.router)
    web.dependency_overrides[get_app_state] = lambda: app
    return TestClient(web), app


def schema_rule(schema):
    return {"type": "json_schema", "path": "output", "schema": schema, "runtime_output": True}


def test_api_strict_output_is_not_polluted_with_success_or_metadata(tmp_path):
    skill = prompt_skill()
    skill.evaluation.verifier_specs = [schema_rule({"type": "object",
        "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False})]
    client, app = client_for(tmp_path, skill, '{"answer": "ok"}')
    with client:
        result = client.post("/execution/skill", json={"skill_id": skill.skill_id, "inputs": {}}).json()
    assert result["status"] == "success"
    assert result["steps"][0]["result"] == {"answer": "ok"}
    assert result["final_state"] == app.state_tracker.current
    assert result["final_state"]["runtime_contract_result"] == {"answer": "ok"}
    assert json.loads(result["experience_unit"]["raw_content"])["final_state"] == result["final_state"]


def test_api_does_not_invent_missing_required_business_success(tmp_path):
    skill = prompt_skill()
    skill.evaluation.verifier_specs = [schema_rule({"type": "object",
        "properties": {"answer": {"type": "string"}, "success": {"type": "boolean"}},
        "required": ["answer", "success"], "additionalProperties": False})]
    client, app = client_for(tmp_path, skill, '{"answer": "ok"}')
    with client:
        result = client.post("/execution/skill", json={"skill_id": skill.skill_id, "inputs": {},
                                                       "context": {"must_not_commit": True}}).json()
    assert result["status"] == "failed"
    assert result["steps"][0]["result"] == {"answer": "ok"}
    assert app.state_tracker.current == {"session": "unchanged"}


def test_api_verifies_proposed_state_before_commit(tmp_path):
    skill = prompt_skill()
    skill.implementation = SkillImplementation(code="output = {'answer': state['request_value']}")
    skill.evaluation.verifier_specs = [{"type": "json_equals", "path": "final_state.answer", "value": 7}]
    client, app = client_for(tmp_path, skill, "unused")
    with client:
        result = client.post("/execution/skill", json={"skill_id": skill.skill_id, "inputs": {},
                                                       "context": {"request_value": 7}}).json()
    assert result["status"] == "success"
    assert result["verifier_passed"] is True
    assert result["final_state"]["answer"] == 7
    assert app.state_tracker.current["answer"] == 7


def test_api_verifier_failure_does_not_commit_proposed_state(tmp_path):
    skill = prompt_skill()
    skill.implementation = SkillImplementation(code="output = {'answer': 3}")
    skill.evaluation.verifier_specs = [{"type": "json_equals", "path": "output.answer", "value": 7}]
    client, app = client_for(tmp_path, skill, "unused")
    with client:
        result = client.post("/execution/skill", json={"skill_id": skill.skill_id, "inputs": {},
                                                       "context": {"request_value": 7}}).json()
    assert result["status"] == "failed"
    assert result["steps"][0]["result"] == {"answer": 3}
    assert app.state_tracker.current == {"session": "unchanged"}
    assert result["final_state"] == {"session": "unchanged"}


@pytest.mark.parametrize("extra", ["skill_name", "_state_changes"])
def test_verifier_does_not_hide_model_added_undeclared_metadata_keys(extra):
    rule = schema_rule({"type": "object", "properties": {"answer": {"type": "string"}},
                        "required": ["answer"], "additionalProperties": False})
    result = evaluate_verifier_specs([rule], {"output": {"answer": "ok", extra: "model-added"}})
    assert result.passed is False


def test_tool_only_not_eligible_for_runtime_planning():
    skill = prompt_skill()
    skill.implementation = SkillImplementation(tool_calls=["external_tool"])
    assert execution._is_runtime_planning_skill(skill) is False


def test_auditor_preserves_escaped_literals_and_reports_true_missing_parameter():
    skill = prompt_skill("Literal {{example}} and named {task}")
    skill.state = SkillState.DRAFT
    skill.interface.input_schema = {"type": "object", "properties": {}}
    result = SkillAuditorAgent().audit(skill)
    assert not any("example" in issue for issue in result.issues)
    assert any("task" in issue for issue in result.issues)


@pytest.mark.asyncio
async def test_composite_resolves_all_children_before_executing_any():
    class ClientThatMustNotRun:
        def chat(self, messages):
            raise AssertionError("dependency validation must precede any child execution")
    child = prompt_skill()
    parent = Skill(name="composite_parent", implementation={"sub_skill_ids": [child.skill_id, "absent"]})
    class Registry:
        async def get(self, skill_id):
            return child if skill_id == child.skill_id else None
    record = await SkillExecutor(Registry(), ClientThatMustNotRun()).execute_single(parent, {})
    assert record.status is ExecutionStatus.FAILED
    assert record.error_message == "Required sub-skill not found: absent"


def test_plan_api_verifier_failure_keeps_session_state_unchanged(tmp_path):
    skill = prompt_skill()
    skill.implementation = SkillImplementation(code="output = {'answer': 3}")
    skill.evaluation.verifier_specs = [{"type": "json_equals", "path": "output.answer", "value": 7}]
    client, app = client_for(tmp_path, skill, "unused")
    class Search:
        async def search(self, query):
            return [SimpleNamespace(skill=skill, score=1, match_reasons=["fixed test"])]
    class Planner:
        async def plan(self, task_description, available_skills, current_state):
            return ExecutionPlan(task_description=task_description, steps=[
                PlanStep(step_index=0, skill_id=skill.skill_id, skill_name=skill.name)])
    app.search, app.planner = Search(), Planner()
    with client:
        result = client.post("/execution/plan", json={"goal": "read", "context": {"request_value": 7}}).json()
    assert result["status"] == "failed"
    assert app.state_tracker.current == {"session": "unchanged"}
    assert result["final_state"] == {"session": "unchanged"}


def test_plan_api_planning_failure_does_not_commit_request_context(tmp_path):
    skill = prompt_skill()
    client, app = client_for(tmp_path, skill, "unused")
    class Search:
        async def search(self, query):
            return [SimpleNamespace(skill=skill, score=1, match_reasons=[])]
    class Planner:
        async def plan(self, **kwargs):
            raise RuntimeError("controlled planning failure")
    app.search, app.planner = Search(), Planner()
    with client:
        result = client.post("/execution/plan", json={"goal": "read", "context": {"request_value": 7}})
    assert result.status_code == 503
    assert app.state_tracker.current == {"session": "unchanged"}


@pytest.mark.asyncio
async def test_code_business_collision_is_preserved_and_state_is_nested_not_controlled():
    skill = prompt_skill()
    skill.implementation = SkillImplementation(code="output = {'skill_name': 9, '_state_changes': {'business': 3}, 'answer': False}")
    record = await SkillExecutor().execute_single(skill, {})
    assert record.output_data == {"skill_name": 9, "_state_changes": {"business": 3}, "answer": False}
    assert record.state_after["_state_changes"] == {"business": 3}
    assert "business" not in record.state_after


@pytest.mark.asyncio
async def test_single_failure_preserves_initial_state_after_mutating_local_copy():
    skill = prompt_skill()
    skill.implementation = SkillImplementation(code="state['nested']['x'] = 9\noutput = 1 / 0")
    initial = {"nested": {"x": 1}}
    record = await SkillExecutor().execute_single(skill, {}, initial_state=initial)
    assert record.status is ExecutionStatus.FAILED
    assert initial == record.state_before == record.state_after == {"nested": {"x": 1}}


def test_api_explicit_false_business_success_is_never_promoted_to_true(tmp_path):
    skill = prompt_skill()
    skill.evaluation.verifier_specs = [{"type": "json_equals", "path": "output.success", "value": True}]
    client, app = client_for(tmp_path, skill, '{"answer": "no", "success": false}')
    with client:
        result = client.post("/execution/skill", json={"skill_id": skill.skill_id, "inputs": {}}).json()
    assert result["status"] == "failed"
    assert result["steps"][0]["result"] == {"answer": "no", "success": False}
    assert app.state_tracker.current == {"session": "unchanged"}


def test_auditor_sees_nested_named_format_requirements():
    skill = prompt_skill("Measured {value:{width}.2f}")
    skill.state = SkillState.DRAFT
    skill.interface.input_schema = {"type": "object", "properties": {"value": {"type": "number"}}}
    result = SkillAuditorAgent().audit(skill)
    assert any("width" in issue for issue in result.issues)


def test_auditor_treats_component_instruction_like_executor_literal_instruction():
    skill = prompt_skill("Use literal {unbound_example} exactly.")
    skill.state = SkillState.DRAFT
    from skillwiki.models.skill_model import SkillProvenance
    skill.provenance = SkillProvenance(source_type="manual", creation_context={"component_run_id": "controlled"})
    result = SkillAuditorAgent().audit(skill)
    assert not any("unbound_example" in issue for issue in result.issues)


def test_tool_metadata_does_not_disqualify_supported_prompt_implementation():
    skill = prompt_skill()
    skill.state = SkillState.DRAFT
    skill.implementation.tool_calls = ["documentation_only"]
    assert SkillAuditorAgent().audit(skill).postcondition_ok is True
    assert execution._is_runtime_planning_skill(skill) is True


@pytest.mark.parametrize("name", ["skill_name", "_state_changes"])
def test_declared_same_name_business_field_counts_as_business_content(name):
    schema = {"type": "object", "properties": {name: {"type": "integer"}}, "required": [name]}
    result = evaluate_verifier_specs([schema_rule(schema),
        {"type": "json_declared_content", "path": "output", "schema": schema}], {"output": {name: 7}})
    assert result.passed is True


def test_legacy_search_never_reintroduces_tool_only_skill_when_no_executable_match(tmp_path):
    skill = prompt_skill()
    skill.implementation = SkillImplementation(tool_calls=["unsupported_tool"])
    client, app = client_for(tmp_path, skill, "unused")
    class Search:
        async def search(self, query):
            return [SimpleNamespace(skill=skill, score=1, match_reasons=[])]
    class Planner:
        seen = None
        async def plan(self, task_description, available_skills, current_state):
            self.seen = [item.skill_id for item in available_skills]
            return ExecutionPlan(task_description=task_description, steps=[])
    app.search, app.planner = Search(), Planner()
    with client:
        response = client.post("/execution/plan", json={"goal": "read"})
    assert response.status_code == 200
    assert app.planner.seen == []
    assert response.json()["retrieved_skills"] == []

