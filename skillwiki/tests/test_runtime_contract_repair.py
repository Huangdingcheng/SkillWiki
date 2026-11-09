"""Offline regression tests for explicit prompt modes and dispatch input contracts."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from skillwiki.layers.skill_governance.artifact_identity import (
    artifact_identity_projection, canonical_skill_artifact_hash,
)
from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.layers.skill_runtime.input_contract import InputContractError, validate_input_schema
from skillwiki.layers.skill_runtime.planner import ExecutionPlan, PlanStep, StepStatus
from skillwiki.models.experience_model import ExecutionStatus
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillInterface, SkillProvenance
from skillwiki.utils.llm_client import LLMResponse


class FixedClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append([message.to_dict() for message in messages])
        return LLMResponse('{"ok": true}', 'offline-contract-test', {}, 'stop')


def make_skill(template='Return the supplied task result.', mode=None, *, schema=None, context=None, name='runtime_contract'):
    return Skill(
        skill_id=name, name=name, description='Execute the supplied structured task without guessing missing inputs.',
        implementation=SkillImplementation(prompt_template=template, prompt_format=mode),
        interface=SkillInterface(
            input_schema=schema if schema is not None else {'type': 'object', 'properties': {}},
            output_schema={'type': 'object', 'properties': {'ok': {'type': 'boolean'}}},
        ),
        provenance=SkillProvenance(source_type='manual', creation_context=context or {}),
    )


def complete_input(messages):
    return json.loads(messages[-1]['content'].split('\n', 1)[1])


@pytest.mark.asyncio
@pytest.mark.parametrize('template', [
    'Example {answer: value, notes: [text]}',
    '集合 {red, green}; URL https://example.invalid/{id}',
    'Preserve double {{braces}} and unmatched { tail.',
    'Raw JSON {"name": "{name}", "nested": {"ok": true}}',
    '中文说明：保持原文，不猜答案。' * 400,
], ids=['pseudo-json', 'set-url', 'doubled-unmatched', 'json', 'long-chinese'])
async def test_literal_mode_preserves_instruction_bytes_and_complete_input(template):
    payload = {'name': 'Ada', 'nested': {'items': [False, None, 0]}, 'dialogue': 'business data'}
    skill = make_skill(template, 'literal')
    before = skill.model_dump(mode='json')
    client = FixedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(skill, payload)
    assert record.status == ExecutionStatus.SUCCESS
    assert client.calls[0][1]['content'] == template
    assert complete_input(client.calls[0]) == payload
    assert skill.model_dump(mode='json') == before


@pytest.mark.asyncio
@pytest.mark.parametrize('mode, expected', [('literal', 'Hello {name}'), ('python_format', 'Hello Ada')])
async def test_explicit_mode_overrides_component_dialogue_heuristic(mode, expected):
    skill = make_skill('Hello {name}', mode, context={'demo_components': {'task_instruction': 'Legacy dialogue task'}})
    client = FixedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(skill, {'name': 'Ada', 'dialogue': 'ignored only by legacy'})
    assert record.status == ExecutionStatus.SUCCESS
    assert client.calls[0][1]['content'] == expected
    assert all('Legacy dialogue task' not in message['content'] for message in client.calls[0])
    if mode == 'literal':
        assert complete_input(client.calls[0]) == record.input_data


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', [None, 'python_format'])
async def test_python_format_and_legacy_preserve_named_rendering_and_missing_failure(mode):
    schema = {'type': 'object', 'properties': {'name': {'type': 'string'}, 'count': {'type': 'integer'}}}
    skill = make_skill('Name {name!r}; count {count:04d}; JSON {"ok": true}', mode, schema=schema)
    client = FixedClient()
    executor = SkillExecutor(llm_client=client, max_retries=0)
    record = await executor.execute_single(skill, {'name': 'Ada', 'count': 7})
    assert record.status == ExecutionStatus.SUCCESS
    assert client.calls[0][1]['content'] == """Name 'Ada'; count 0007; JSON {"ok": true}"""
    missing = await executor.execute_single(skill, {'name': 'Ada'})
    assert missing.status == ExecutionStatus.FAILED
    assert len(client.calls) == 1


def test_unknown_prompt_mode_rejected_instead_of_silently_discarded():
    with pytest.raises(ValidationError):
        SkillImplementation(prompt_template='Return data.', prompt_format='guess')


@pytest.mark.parametrize('mode, context, passed', [
    ('literal', {}, True),
    ('python_format', {}, False),
    ('python_format', {'component_run_id': 'old-component'}, False),
    (None, {'component_run_id': 'old-component'}, True),
    (None, {}, False),
])
def test_auditor_uses_same_prompt_mode_as_executor(mode, context, passed):
    skill = make_skill('URL https://example.invalid/{id}; set {red, green}', mode, context=context)
    result = SkillAuditorAgent().audit(skill)
    assert result.schema_ok is passed
    assert result.passed is passed


def test_legacy_artifact_projection_omits_only_new_none_field():
    skill = make_skill()
    projection = artifact_identity_projection(skill)
    assert projection['implementation'] == {
        'language': 'python', 'code': None, 'prompt_template': 'Return the supplied task result.',
        'tool_calls': [], 'sub_skill_ids': [], 'execution_order': None,
    }
    expected = hashlib.sha256((json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()).hexdigest()
    assert canonical_skill_artifact_hash(skill) == expected
    literal = make_skill(mode='literal')
    python_format = make_skill(mode='python_format')
    assert artifact_identity_projection(literal)['implementation']['prompt_format'] == 'literal'
    assert len({canonical_skill_artifact_hash(item) for item in (skill, literal, python_format)}) == 3


def test_original_c03_fixture_retains_prechange_artifact_hash():
    original = (Path(__file__).parent / 'fixtures/c03-input-binding-skill.json').read_bytes()
    skill = Skill.model_validate_json(original)
    # Measured against the immutable pre-repair baseline/source package.
    assert canonical_skill_artifact_hash(skill) == '1cf67ab929a1291021a5abb9aa36e7dce31f694e184fc7814e395b6f5a35ccee'


def test_shared_validator_reports_complete_violations_without_mutating_input():
    schema = {'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['case_id', 'count']}
    payload = {'count': 'two', 'task_data': {'case_id': 'nested-is-not-top-level'}}
    before = copy.deepcopy(payload)
    with pytest.raises(InputContractError) as caught:
        validate_input_schema(schema, payload)
    assert 'case_id' in str(caught.value) and 'integer' in str(caught.value)
    assert payload == before


@pytest.mark.parametrize('schema, payload', [
    (True, {'anything': None}),
    ({'$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object', 'properties': {'count': {'type': 'integer'}}}, {'count': 2}),
    ({'$defs': {'count': {'type': 'integer'}}, 'properties': {'count': {'$ref': '#/$defs/count'}}}, {'count': 0}),
])
def test_shared_validator_preserves_valid_dialects_and_local_refs(schema, payload):
    validate_input_schema(schema, payload)


@pytest.mark.parametrize('schema, payload', [
    (False, {}),
    (None, {}),
    ({'$schema': 'https://example.invalid/unknown-dialect'}, {}),
    ({'properties': {'count': {'type': 'integer'}}}, {'count': True}),
])
def test_shared_validator_rejects_invalid_contracts(schema, payload):
    with pytest.raises(InputContractError):
        validate_input_schema(schema, payload)


def test_auditor_llm_request_exposes_explicit_prompt_mode():
    client = FixedClient()
    skill = make_skill('Return literal {id} unchanged.', 'literal')
    SkillAuditorAgent(client).audit(skill)
    assert 'prompt_format: literal' in client.calls[0][1]['content']
    assert skill.implementation.prompt_template in client.calls[0][1]['content']


INVALID_INPUTS = [
    ({'type': 'object', 'properties': {'case_id': {'type': 'string'}}, 'required': ['case_id']}, {'task_data': {'case_id': 'nested-does-not-count'}}),
    ({'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count']}, {'count': '2'}),
    ({'type': 'object', 'properties': {'count': {'type': 'unknown'}}}, {'count': 2}),
    ({'type': 'object', 'properties': {}, 'required': 'bad-schema'}, {}),
    ({'type': 'object', 'properties': {'data': {'$ref': 'https://example.invalid/schema.json'}}}, {'data': {}}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('schema, payload', INVALID_INPUTS)
@pytest.mark.parametrize('kind', ['prompt', 'code'])
async def test_invalid_single_input_rejected_before_dispatch_without_state_mutation(schema, payload, kind):
    client = FixedClient()
    skill = make_skill(schema=schema)
    if kind == 'code':
        skill.implementation = SkillImplementation(code="state['bad_dispatch'] = True; output = {'bad_dispatch': True}")
    initial = {'before': {'unchanged': True}}
    initial_before, payload_before = copy.deepcopy(initial), copy.deepcopy(payload)
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(skill, payload, initial_state=initial)
    assert record.status == ExecutionStatus.FAILED
    assert client.calls == []
    assert record.state_before == record.state_after == initial_before
    assert initial == initial_before and payload == payload_before
    assert 'input' in record.error_message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('schema, payload', INVALID_INPUTS)
async def test_invalid_plan_input_blocks_all_dispatch_and_retains_initial_state(schema, payload):
    valid = make_skill(name='valid_first')
    invalid = make_skill(schema=schema, name='invalid_later')
    first = PlanStep(skill_id=valid.skill_id, input_mapping={})
    second = PlanStep(skill_id=invalid.skill_id, input_mapping=payload, depends_on=[first.step_id])
    plan = ExecutionPlan(task_id='offline-plan', steps=[first, second])
    client = FixedClient()
    initial = {'preserved': [1, False]}
    final = await SkillExecutor(llm_client=client, max_retries=0).execute_plan(plan, {s.skill_id: s for s in [valid, invalid]}, initial)
    assert final == initial
    assert client.calls == []
    assert second.status == StepStatus.FAILED
    assert first.status != StepStatus.SUCCESS


@pytest.mark.asyncio
@pytest.mark.parametrize('nested', [False, True])
async def test_composite_prevalidates_every_descendant_before_first_paid_call(nested):
    first = make_skill(name='valid_child')
    invalid = make_skill(name='invalid_child', schema={'type': 'object', 'properties': {'case_id': {'type': 'string'}}, 'required': ['case_id']})
    skills = {s.skill_id: s for s in (first, invalid)}
    last = invalid
    if nested:
        last = Skill(skill_id='middle', name='middle', implementation=SkillImplementation(sub_skill_ids=[invalid.skill_id]))
        skills[last.skill_id] = last
    parent = Skill(name='parent', implementation=SkillImplementation(sub_skill_ids=[first.skill_id, last.skill_id]))
    class Registry:
        async def get(self, skill_id):
            return skills.get(skill_id)
    client = FixedClient()
    initial = {'unchanged': True}
    record = await SkillExecutor(skill_registry=Registry(), llm_client=client, max_retries=0).execute_single(parent, {}, initial_state=initial)
    assert record.status == ExecutionStatus.FAILED
    assert client.calls == []
    assert record.state_after == initial


@pytest.mark.asyncio
@pytest.mark.parametrize('schema, payload', [({}, {'arbitrary': [None, False, 0]}), ({'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count']}, {'count': 0})])
async def test_empty_or_satisfied_schema_allows_execution(schema, payload):
    client = FixedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(make_skill(schema=schema), payload)
    assert record.status == ExecutionStatus.SUCCESS
    assert len(client.calls) == 1


