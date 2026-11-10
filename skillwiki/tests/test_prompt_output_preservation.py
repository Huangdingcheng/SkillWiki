"""Native prompt responses must not discard prose, warnings, or fragments."""
from __future__ import annotations

import copy
import json

import pytest

from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.experience_model import ExecutionStatus
from skillwiki.models.skill_model import Skill, SkillImplementation
from skillwiki.utils.llm_client import LLMResponse


class ResponseClient:
    def __init__(self, content):
        self.content = content
        self.messages = []

    def chat(self, messages):
        self.messages.append([m.to_dict() for m in messages])
        return LLMResponse(self.content, 'offline-preservation-test', {}, 'stop')


async def execute(content, payload=None):
    client = ResponseClient(content)
    skill = Skill(name='response_preservation', implementation=SkillImplementation(prompt_template='Validate all supplied fields and report errors.'))
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(skill, payload or {})
    assert record.status == ExecutionStatus.SUCCESS
    assert len(client.messages) == 1
    return record, client


@pytest.mark.asyncio
async def test_whole_json_object_is_machine_readable_without_type_loss():
    expected = {'accepted': False, 'errors': ['name missing'], 'count': 0, 'optional': None, 'nested': {'ready': True}}
    record, _ = await execute('  ' + json.dumps(expected) + chr(10))
    for key, value in expected.items():
        assert json.dumps(record.output_data[key], sort_keys=True) == json.dumps(value, sort_keys=True)
    assert record.state_after['response_preservation_result'] == expected


@pytest.mark.asyncio
async def test_whole_single_fenced_json_object_is_machine_readable():
    content = '```json' + chr(10) + '{"accepted": false, "errors": ["name missing"]}' + chr(10) + '```'
    record, _ = await execute(content)
    assert record.output_data['accepted'] is False
    assert record.output_data['errors'] == ['name missing']
    assert record.state_after['response_preservation_result'] == {'accepted': False, 'errors': ['name missing']}


@pytest.mark.asyncio
async def test_inline_json_with_warning_retains_entire_narrative():
    content = 'Do not send this request: {"method": "POST", "body": {}}. Required field name is missing.'
    record, _ = await execute(content)
    assert record.output_data['result'] == content
    assert record.state_after['response_preservation_result'] == content


@pytest.mark.asyncio
async def test_multiple_fenced_fragments_retain_all_fragments_and_warning():
    content = chr(10).join(['Request preview:', '```json', '{"method": "POST", "body": {}}', '```', 'Validation errors:', '```json', '{"errors": ["name missing"]}', '```', 'Do not send until corrected.'])
    record, _ = await execute(content)
    assert record.output_data['result'] == content
    assert record.state_after['response_preservation_result'] == content


@pytest.mark.asyncio
async def test_text_before_and_after_single_fence_retains_full_response():
    content = chr(10).join(['Example only, not a valid final request.', '```json', '{"method": "POST", "url": "/pet"}', '```', 'The access token and required Pet.name are missing.'])
    record, _ = await execute(content)
    assert record.output_data['result'] == content
    assert record.state_after['response_preservation_result'] == content


@pytest.mark.asyncio
async def test_malformed_json_is_preserved_without_guessing_repairs():
    content = '{"accepted": false, "errors": ["name missing",]}'
    record, _ = await execute(content)
    assert record.output_data['result'] == content
    assert record.state_after['response_preservation_result'] == content


@pytest.mark.asyncio
async def test_top_level_json_array_is_preserved_as_raw_response():
    content = '[{"field": "name", "error": "required"}, {"warning": "do not send"}]'
    record, _ = await execute(content)
    assert record.output_data['result'] == content
    assert record.state_after['response_preservation_result'] == content


@pytest.mark.asyncio
async def test_native_input_remains_complete_while_mixed_output_is_preserved():
    payload = {'pet': {'photoUrls': []}, 'access_token': 'SIMULATED_OUTPUT_TEST_TOKEN'}
    before = copy.deepcopy(payload)
    content = chr(10).join(['Cannot submit: name missing.', '```json', '{"pet": {"photoUrls": []}}', '```', 'Please supply Pet.name.'])
    record, client = await execute(content, payload)
    assert payload == before
    assert record.input_data == before
    assert record.output_data['result'] == content
    assert record.state_after['response_preservation_result'] == content
    message = client.messages[0][-1]['content']
    assert json.loads(message[message.index('{'):]) == before
