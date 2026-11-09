"""Behavioral regressions for prompt binding, with no network/model calls."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from skillwiki.layers.skill_runtime import executor as executor_module
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.experience_model import ExecutionStatus
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillProvenance
from skillwiki.utils.llm_client import LLMResponse


class CapturedClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append([message.to_dict() for message in messages])
        return LLMResponse('{"ok": true}', 'offline-test', {}, 'stop')


def prompt_skill(template, context=None):
    return Skill(
        name='binding_test',
        implementation=SkillImplementation(prompt_template=template),
        provenance=SkillProvenance(source_type='manual', creation_context=context or {}),
    )


def contains_json_payload(messages, expected):
    """Accept any message framing, but require one complete structured input."""
    decoder = json.JSONDecoder()
    for message in messages:
        content = message['content']
        for pos, char in enumerate(content):
            if char != '{':
                continue
            try:
                value, _ = decoder.raw_decode(content[pos:])
            except json.JSONDecodeError:
                continue
            if json.dumps(value, sort_keys=True, ensure_ascii=False) == json.dumps(expected, sort_keys=True, ensure_ascii=False):
                return True
    return False


@pytest.mark.asyncio
async def test_actual_c03_distinct_inputs_reach_native_messages_without_mutation():
    path = Path(__file__).resolve().parent / 'fixtures/c03-input-binding-skill.json'
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == 'e72b0d4cb6e7545822df236b11286b064a60e2cd8d092b4b460626fdc77257f2'
    skill = Skill.model_validate_json(original)
    skill_before = skill.model_dump(mode='json')
    inputs = [
        {'pet': {'name': 'ALPHA-PET', 'photoUrls': [], 'category': {'id': 12, 'name': 'feline'}}, 'access_token': 'SIMULATED_TOKEN_ALPHA', 'content_type': 'application/json'},
        {'pet': {'name': 'BETA-PET', 'photoUrls': ['https://example.invalid/beta'], 'tags': [{'id': 7, 'name': 'tag'}]}, 'access_token': 'SIMULATED_TOKEN_BETA', 'content_type': 'application/xml'},
    ]
    before = copy.deepcopy(inputs)
    client = CapturedClient()
    executor = SkillExecutor(llm_client=client, max_retries=0)
    for payload in inputs:
        record = await executor.execute_single(skill, payload)
        assert record.status == ExecutionStatus.SUCCESS
    assert len(client.calls) == 2
    assert client.calls[0] != client.calls[1]
    for payload, messages in zip(inputs, client.calls):
        assert contains_json_payload(messages, payload)
        assert skill.implementation.prompt_template in chr(10).join(x['content'] for x in messages)
    assert inputs == before
    assert skill.model_dump(mode='json') == skill_before
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_plain_prompt_binds_entire_input_and_does_not_emit_token(caplog, capsys):
    token = 'SIMULATED_PUBLIC_LOG_SENTINEL_22'
    payload = {'pet': {'name': 'brace {inside}', 'flags': [True, False, None], 'count': 0}, 'access_token': token}
    client = CapturedClient()
    executor = SkillExecutor(llm_client=client, max_retries=0)
    events = []
    executor.add_event_callback(lambda name, data: events.append((name, data)))
    executor_module.logger.addHandler(caplog.handler)
    try:
        record = await executor.execute_single(prompt_skill('Preserve and validate every input field.'), payload)
    finally:
        executor_module.logger.removeHandler(caplog.handler)
    assert record.status == ExecutionStatus.SUCCESS
    assert contains_json_payload(client.calls[0], payload)
    assert token in json.dumps(client.calls)
    assert token not in caplog.text
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert token not in json.dumps(events)


@pytest.mark.asyncio
async def test_plain_prompt_empty_input_is_explicit():
    client = CapturedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(prompt_skill('Summarize the provided input.'), {})
    assert record.status == ExecutionStatus.SUCCESS
    assert contains_json_payload(client.calls[0], {})


@pytest.mark.asyncio
@pytest.mark.parametrize('template, payload, expected', [
    ('Hello {name}', {'name': 'Ada'}, 'Hello Ada'),
    ('Name {name!r}; count {count:04d}', {'name': 'Ada', 'count': 7}, "Name 'Ada'; count 0007"),
    ('Name {pet[name]}, first {photos[0]}', {'pet': {'name': 'Ada'}, 'photos': ['one']}, 'Name Ada, first one'),
    ('Ratio {value:.{precision}f}', {'value': 1.2345, 'precision': 2}, 'Ratio 1.23'),
    ('Example {{"name": "{name}"}}', {'name': 'Ada'}, 'Example {"name": "Ada"}'),
])
async def test_existing_named_template_formatting_remains(template, payload, expected):
    client = CapturedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(prompt_skill(template), payload)
    assert record.status == ExecutionStatus.SUCCESS
    assert client.calls[0] == [
        {'role': 'system', 'content': '你是 SkillWiki 中的 binding_test Skill，请严格按照任务要求执行。'},
        {'role': 'user', 'content': expected},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('template, payload', [
    ('Hello {required_name}', {'unrelated': 'value'}),
    ('Name {pet[name]}', {'pet': {}}),
])
async def test_missing_template_parameter_fails_before_model(template, payload):
    client = CapturedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(prompt_skill(template), payload)
    assert record.status == ExecutionStatus.FAILED
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('template', [
    'Return JSON {"ok": true, "data": {"name": "example"}}.',
])
async def test_literal_braces_survive_and_structured_input_is_bound(template):
    client = CapturedClient()
    payload = {'name': 'ALPHA', 'nested': {'letter': 'b'}}
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(prompt_skill(template), payload)
    assert record.status == ExecutionStatus.SUCCESS
    assert template in chr(10).join(x['content'] for x in client.calls[0])
    assert contains_json_payload(client.calls[0], payload)


@pytest.mark.asyncio
async def test_mixed_literal_json_and_named_placeholder():
    template = 'For {name}, return JSON {"ok": true}.'
    client = CapturedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(prompt_skill(template), {'name': 'Ada'})
    assert record.status == ExecutionStatus.SUCCESS
    assert 'For Ada, return JSON {"ok": true}.' in [x['content'] for x in client.calls[0]]


@pytest.mark.asyncio
async def test_raw_json_string_named_marker_is_literal():
    template = 'Return JSON {"name": "{name}"} as the example.'
    payload = {'name': 'Ada'}
    client = CapturedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(prompt_skill(template), payload)
    assert record.status == ExecutionStatus.SUCCESS
    assert template in [x['content'] for x in client.calls[0]]
    assert contains_json_payload(client.calls[0], payload)


@pytest.mark.asyncio
async def test_escaped_only_braces_keep_legacy_rendering_and_explicit_input():
    client = CapturedClient()
    payload = {'name': 'Ada'}
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(prompt_skill('Use {{literal}} exactly.'), payload)
    assert record.status == ExecutionStatus.SUCCESS
    assert 'Use {literal} exactly.' in [x['content'] for x in client.calls[0]]
    assert contains_json_payload(client.calls[0], payload)


@pytest.mark.asyncio
async def test_instruction_demo_retains_exact_existing_message_contract():
    instruction = 'Return literal JSON {"nodes": [], "edges": []}.'
    task = 'Parse each line into the local graph schema.'
    skill = prompt_skill(instruction, {'demo_components': {'task_instruction': task}})
    client = CapturedClient()
    record = await SkillExecutor(llm_client=client, max_retries=0).execute_single(skill, {'dialogue': 'Alice: Hello'})
    assert record.status == ExecutionStatus.SUCCESS
    assert client.calls == [[{'role': 'system', 'content': instruction}, {'role': 'user', 'content': task + chr(10) + 'Dialogue:' + chr(10) + 'Alice: Hello'}]]


@pytest.mark.asyncio
async def test_composite_prompt_child_gets_original_input_and_code_child_still_runs():
    prompt_child = prompt_skill('Validate all input fields.')
    code_child = Skill(name='binding_code_child', implementation=SkillImplementation(code="output['name'] = input_data['pet']['name']"))
    children = {s.skill_id: s for s in [prompt_child, code_child]}
    class Registry:
        async def get(self, skill_id):
            return children.get(skill_id)
    parent = Skill(name='binding_parent', implementation=SkillImplementation(sub_skill_ids=list(children)))
    client = CapturedClient()
    payload = {'pet': {'name': 'COMPOSITE_INPUT'}, 'access_token': 'SIMULATED_COMPOSITE_TOKEN'}
    record = await SkillExecutor(skill_registry=Registry(), llm_client=client, max_retries=0).execute_single(parent, payload)
    assert record.status == ExecutionStatus.SUCCESS
    assert len(client.calls) == 1
    assert contains_json_payload(client.calls[0], payload)
    assert record.output_data['sub_results'][code_child.skill_id]['name'] == 'COMPOSITE_INPUT'
