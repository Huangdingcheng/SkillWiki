"""Real archived candidates: domain evidence must reach the native model boundary."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from skillwiki.layers.input_knowledge.pipeline import (
    _metadata_from_ctx2skill_payload, _enrich_api_candidate_schema,
    _remove_unsupported_additional_constraints,
)
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.skill_model import Skill

ROOT = Path(__file__).resolve().parents[2]
C03 = ROOT / 'research/stage2-q1-c03-utility-20261027'
P = ROOT / 'research/stage2-demo-api-pilot-20261029'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def source_p():
    return (P / 'generation-inputs/API-P.txt').read_text(encoding='utf-8')

def candidate(data):
    return {'name': data['name'], 'description': data['description'],
            'input_schema': deepcopy(data['interface']['input_schema']),
            'output_schema': deepcopy(data['interface']['output_schema']),
            'prompt_template': data['implementation']['prompt_template'],
            'prompt_format': 'literal'}

class Capture:
    def __init__(self):
        self.messages = []
    def chat(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(content='{"validation_errors":["fixed transport sentinel"]}')

@pytest.mark.parametrize('case_id', ['Q12', 'Q13'])
@pytest.mark.asyncio
async def test_actual_c03_invalid_domain_reaches_native_model_and_source_rules(case_id):
    data = read(C03 / 'skill.json')
    original = deepcopy(data['interface']['input_schema'])
    c = candidate(data)
    changes = _enrich_api_candidate_schema(c, (C03 / 'source.yaml').read_text(encoding='utf-8'))
    data['interface']['input_schema'] = c['input_schema']
    data['implementation'].update(prompt_template=c['prompt_template'], prompt_format='literal')
    case = next(x for x in read(C03 / 'cases.json')['cases'] if x['id'] == case_id)
    client = Capture()
    result = await SkillExecutor(llm_client=client, max_retries=0).execute_single(Skill.model_validate(data), case['input'])
    assert result.status.value == 'success', result.error_message
    assert c['input_schema'] == original
    assert len(client.messages) == 1
    message = client.messages[0][1].content
    assert '"pattern"' in message and '"integer"' in message and '"tags"' in message
    assert json.loads(client.messages[0][-1].content.split(chr(10), 1)[1]) == case['input']
    assert any(x.get('kind') == 'source_domain_rules_attached' for x in changes)

def test_actual_wrapper_removes_unsupported_ban_without_schema_shape_guess():
    data = read(P / 'generation-receipts/API-P/attempt-001/skill.json')
    c = candidate(data); before = deepcopy(c['input_schema'])
    changes = _remove_unsupported_additional_constraints(c, source_p())
    assert 'no additional properties' not in c['prompt_template'].lower()
    assert c['input_schema'] == before
    assert any(x.get('kind') == 'removed_unsupported_constraint' for x in changes)

def test_actual_generation_postprocessing_delivers_rules_and_keeps_envelope():
    data = read(P / 'generation-receipts/API-P/attempt-001/skill.json')
    c = candidate(data)
    result = _metadata_from_ctx2skill_payload({'candidates':[c]}, raw_content=source_p(), source_type='api_doc', known_skills=[])
    assert result['candidate_interface']['input_schema'] == c['input_schema']
    assert '"category"' in result['candidate_implementation']['prompt_template']
    assert 'no additional properties' not in result['candidate_implementation']['prompt_template'].lower()

def test_source_explicit_ban_preserves_prompt_and_interface():
    source = json.loads(source_p().split('Frozen operation source:' + chr(10), 1)[1])
    source['components']['schemas']['Pet']['additionalProperties'] = False
    c = candidate(read(P / 'generation-receipts/API-P/attempt-001/skill.json')); original = deepcopy(c)
    _remove_unsupported_additional_constraints(c, json.dumps(source))
    assert c == original

def test_unmapped_multiple_operations_are_explicitly_skipped():
    source = json.loads(source_p().split('Frozen operation source:' + chr(10), 1)[1])
    source['paths']['/unrelated'] = {'post': {'requestBody': {'content': {'application/json': {'schema': {'type':'object','additionalProperties':False}}}}}}
    c = candidate(read(P / 'generation-receipts/API-P/attempt-001/skill.json')); original = deepcopy(c)
    changes = _enrich_api_candidate_schema(c, json.dumps(source))
    assert c == original
    assert any(x.get('kind') == 'source_domain_rules_skipped' and 'ambiguous' in x['reason'] for x in changes)

def test_legacy_named_template_is_not_reinterpreted():
    c = {'input_schema':{'type':'object'}, 'prompt_template':'Use {task}', 'prompt_format':'python_format'}
    before=deepcopy(c)
    _enrich_api_candidate_schema(c, source_p())
    _remove_unsupported_additional_constraints(c, source_p())
    assert c == before

@pytest.mark.parametrize('ref', ['#/components/schemas/Missing', 'https://invalid.example/schema', '#/components/schemas/Pet'])
def test_unresolved_external_and_recursive_refs_leave_candidate_untouched(ref):
    source=json.loads(source_p().split('Frozen operation source:' + chr(10),1)[1])
    source['components']['schemas']['Pet']['properties']['cycle']={'$ref':ref}
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json')); before=deepcopy(c)
    changes=_enrich_api_candidate_schema(c,json.dumps(source))
    assert c==before
    assert changes[0]['kind']=='source_domain_rules_skipped'

def test_explicit_operation_survives_normalization_and_selects_correct_source():
    source=json.loads(source_p().split('Frozen operation source:' + chr(10),1)[1])
    source['paths']['/other']={'post':{'requestBody':{'content':{'application/json':{'schema':{'type':'object','properties':{'wrong':{'type':'boolean'}}}}}}}}
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json'))
    c['source_operation']={'path':'/pet','method':'POST'}
    result=_metadata_from_ctx2skill_payload({'candidates':[c]},raw_content=json.dumps(source),source_type='api_doc',known_skills=[])
    prompt=result['candidate_implementation']['prompt_template']
    assert '"category"' in prompt and '"wrong"' not in prompt
    assert result['ctx2skill_evidence']['selected_candidate']['source_operation']==c['source_operation']

def test_request_body_fix_preserves_separate_output_schema_ban():
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json'))
    c['prompt_template'] += ' Output JSON must have no additional properties.'
    _remove_unsupported_additional_constraints(c,source_p())
    assert 'status enum, no additional properties' not in c['prompt_template']
    assert 'Output JSON must have no additional properties.' in c['prompt_template']

@pytest.mark.asyncio
async def test_missing_invocation_envelope_still_rejected_before_model():
    data=read(P/'generation-receipts/API-P/attempt-001/skill.json');c=candidate(data)
    _enrich_api_candidate_schema(c,source_p())
    data['implementation'].update(prompt_template=c['prompt_template'],prompt_format='literal')
    client=Capture()
    result=await SkillExecutor(llm_client=client,max_retries=0).execute_single(Skill.model_validate(data),{'task_data':{},'task_contract':{}})
    assert result.status.value=='failed'
    assert not client.messages
    assert result.state_after is None or result.state_after=={}

def test_nested_explicit_ban_prevents_blanket_prose_removal():
    source=json.loads(source_p().split('Frozen operation source:' + chr(10),1)[1])
    source['components']['schemas']['Category']['additionalProperties']=False
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json'));before=deepcopy(c)
    _remove_unsupported_additional_constraints(c,json.dumps(source))
    assert c==before

def test_source_attachment_idempotent_and_changed_source_is_not_silently_appended():
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json'))
    _enrich_api_candidate_schema(c,source_p());before=deepcopy(c)
    assert _enrich_api_candidate_schema(c,source_p())==[]
    source=json.loads(source_p().split('Frozen operation source:' + chr(10),1)[1])
    source['components']['schemas']['Pet']['required'].append('status')
    changes=_enrich_api_candidate_schema(c,json.dumps(source))
    assert c==before and changes[0]['reason']=='existing_source_rules_conflict'

def test_body_prose_fix_never_rewrites_attached_source_examples():
    source=json.loads(source_p().split('Frozen operation source:' + chr(10),1)[1])
    sentence='Validate JSON body with no additional properties'
    source['components']['schemas']['Pet']['example']={'name':sentence}
    raw=json.dumps(source)
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json'))
    _enrich_api_candidate_schema(c,raw)
    block=c['prompt_template'].split('<skillwiki-source-request-schema>',1)[1]
    _remove_unsupported_additional_constraints(c,raw)
    assert c['prompt_template'].split('<skillwiki-source-request-schema>',1)[1]==block

def test_malformed_domain_schema_is_skipped_without_breaking_generation():
    source=json.loads(source_p().split('Frozen operation source:' + chr(10),1)[1])
    source['components']['schemas']['Pet']['properties']=False
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json'));before=deepcopy(c)
    changes=_remove_unsupported_additional_constraints(c,json.dumps(source))
    assert c==before
    assert changes[0]['kind']=='source_domain_rules_skipped'

def test_request_ban_edit_preserves_response_and_output_body_restrictions():
    c=candidate(read(P/'generation-receipts/API-P/attempt-001/skill.json'))
    suffix=' Validate response body: no additional properties. Validate output body: no additional properties.'
    c['prompt_template']+=suffix
    _remove_unsupported_additional_constraints(c,source_p())
    assert c['prompt_template'].endswith(suffix)
    assert 'status enum, no additional properties' not in c['prompt_template']
