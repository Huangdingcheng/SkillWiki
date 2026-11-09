from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from skillwiki.layers.input_knowledge.pipeline import _metadata_from_ctx2skill_payload, _candidate_evaluation_for, _default_input_schema, _default_output_schema
from skillwiki.layers.skill_runtime.verifier import evaluate_verifier_specs
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.skill_model import Skill

FIXTURES = Path(__file__).parent / 'fixtures' / 'source_contract_alignment'

def metadata(cid='c02', payload=None):
    payload = payload or json.loads((FIXTURES / (cid+'.json')).read_text(encoding='utf-8'))
    return _metadata_from_ctx2skill_payload(payload, raw_content='archived fixture', source_type='document' if cid=='c02' else 'api_doc', known_skills=[])

def check(data):
    return evaluate_verifier_specs(metadata()['candidate_evaluation']['verifier_specs'], data)

def valid():
    return {'input':{'pod_name':'pending-1'},'output':{'status':'Pending','failure_reason':'FailedScheduling','insufficient_resources':['CPU'],'evidence':'scheduler event message','conclusion':'CPU resource shortage'}}

def test_custom_contract_accepts_declared_shape_without_default_document_fields():
    assert check(valid()).passed

@pytest.mark.parametrize('mutation', ['missing_pod','wrong_pod','evidence_array','wrong_resource_item','empty','only_flag','only_runtime','unknown_only'])
def test_custom_contract_rejects_specific_invalid_payloads(mutation):
    d=valid()
    if mutation=='missing_pod':d['input'].pop('pod_name')
    elif mutation=='wrong_pod':d['input']['pod_name']=False
    elif mutation=='evidence_array':d['output']['evidence']=[]
    elif mutation=='wrong_resource_item':d['output']['insufficient_resources']=[42]
    elif mutation=='empty':d['output']={}
    elif mutation=='only_flag':d['output']={'verifier':{'passed':True}}
    elif mutation=='only_runtime':d['output']={'skill_name':'x','_state_changes':{'x_result':{'ok':True}}}
    else:d['output']={'arbitrary':'answer'}
    assert not check(d).passed

def test_optional_fields_remain_optional_and_semantics_are_not_invented():
    assert check({'input':{'pod_name':'p'},'output':{'conclusion':'unknown'}}).passed
    assert check({'input':{'pod_name':'p'},'output':{'conclusion':'Every pending pod lacks CPU'}}).passed

@pytest.mark.parametrize('cid',['c02','c03'])
def test_candidate_content_and_interface_are_not_rewritten(cid):
    raw=json.loads((FIXTURES/(cid+'.json')).read_text(encoding='utf-8'));m=metadata(cid);selected=m['ctx2skill_evidence']['selected_candidate'];original=max(raw['candidates'],key=lambda x:x['score'])
    for key in ['input_schema','output_schema']:assert selected[key]==original[key]
    if cid == 'c02':
        assert selected['prompt_template']==original['prompt_template']
    else:
        assert selected['prompt_template'].startswith(original['prompt_template'])
        assert 'report required only when the key is absent' in selected['prompt_template']
    specs=m['candidate_evaluation']['verifier_specs']
    assert any(s['type']=='json_schema' and s['path']=='input' and s['schema']==original['input_schema'] for s in specs)
    assert any(s['type']=='json_schema' and s['path']=='output' and s['schema']==original['output_schema'] for s in specs)


def test_api_source_fills_missing_nested_local_refs_without_rewriting_other_sources():
    raw = json.loads((FIXTURES / 'c03.json').read_text(encoding='utf-8'))
    raw['candidates'][0]['prompt_format'] = 'literal'
    source = """
openapi: 3.0.0
components:
  requestBodies:
    Pet:
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/Pet'
  schemas:
    Pet:
      type: object
      properties:
        category:
          $ref: '#/components/schemas/Category'
        tags:
          type: array
          items:
            $ref: '#/components/schemas/Tag'
    Category:
      type: object
      properties:
        id:
          type: integer
        name:
          type: string
          pattern: '^[a-z]+$'
    Tag:
      type: object
      properties:
        id:
          type: integer
        name:
          type: string
"""
    enriched = _metadata_from_ctx2skill_payload(
        raw, raw_content=source, source_type='api_doc', known_skills=[]
    )
    assert enriched['candidate_interface']['input_schema'] == raw['candidates'][0]['input_schema']
    prompt = enriched['candidate_implementation']['prompt_template']
    domain = json.loads(prompt.split('<skillwiki-source-request-schema>' + chr(10), 1)[1].split(chr(10) + '</skillwiki-source-request-schema>', 1)[0])
    schema = domain['schema']['properties']
    assert schema['category']['properties'] == {
        'id': {'type': 'integer'},
        'name': {'type': 'string', 'pattern': '^[a-z]+$'},
    }
    assert schema['tags']['items']['properties'] == {
        'id': {'type': 'integer'},
        'name': {'type': 'string'},
    }
    assert set(schema) == {'category', 'tags'}
    assert 'title' not in schema['category']
    assert 'xml' not in schema['category']
    paths = {item.get('path') for item in enriched['ctx2skill_evidence']['source_schema_enrichment']}
    assert 'prompt_template' in paths
    assert domain['operation'] == {'request_body': 'Pet'}
    assert 'additionalProperties' not in schema['category']

    original_schema = raw['candidates'][0]['input_schema']
    unchanged = _metadata_from_ctx2skill_payload(
        raw, raw_content=source, source_type='document', known_skills=[]
    )
    untouched_schema = unchanged['candidate_interface']['input_schema']['properties']['pet']['properties']
    assert untouched_schema == original_schema['properties']['pet']['properties']
    assert unchanged['ctx2skill_evidence']['source_schema_enrichment'] == []


def test_api_nested_schema_mapping_does_not_depend_on_pet_field_name():
    raw = json.loads((FIXTURES / 'c03.json').read_text(encoding='utf-8'))
    raw['candidates'][0]['prompt_format'] = 'literal'
    candidate = raw['candidates'][0]
    candidate['input_schema']['properties']['payload'] = candidate['input_schema']['properties'].pop('pet')
    source = """
openapi: 3.0.0
components:
  requestBodies:
    Pet:
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/Pet'
  schemas:
    Pet:
      type: object
      properties:
        category:
          $ref: '#/components/schemas/Category'
    Category:
      type: object
      properties:
        name:
          type: string
        id:
          type: integer
"""
    enriched = _metadata_from_ctx2skill_payload(
        raw, raw_content=source, source_type='api_doc', known_skills=[]
    )
    assert enriched['candidate_interface']['input_schema'] == candidate['input_schema']
    text = enriched['candidate_implementation']['prompt_template']
    domain = json.loads(text.split('<skillwiki-source-request-schema>' + chr(10), 1)[1].split(chr(10) + '</skillwiki-source-request-schema>', 1)[0])
    payload_schema = domain['schema']
    assert payload_schema['properties']['category']['properties'] == {
        'name': {'type': 'string'}, 'id': {'type': 'integer'}
    }


def test_api_schema_enrichment_skips_ambiguous_multiple_request_shapes():
    raw = json.loads((FIXTURES / 'c03.json').read_text(encoding='utf-8'))
    raw['candidates'][0]['prompt_format'] = 'literal'
    source = """
openapi: 3.0.0
paths:
  /first:
    post:
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                pet:
                  type: object
                  properties:
                    category:
                      type: object
  /second:
    post:
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                pet:
                  type: object
                  properties:
                    category:
                      type: object
                      properties:
                        unrelated:
                          type: string
"""
    original = deepcopy(raw['candidates'][0]['input_schema'])
    enriched = _metadata_from_ctx2skill_payload(
        raw, raw_content=source, source_type='api_doc', known_skills=[]
    )
    assert enriched['candidate_interface']['input_schema'] == original
    assert any('ambiguous' in x.get('reason', '') for x in enriched['ctx2skill_evidence']['source_schema_enrichment'])


def test_ctx2skill_generation_prompt_preserves_unspecified_additional_property_policy():
    from skillwiki.layers.input_knowledge.pipeline import _CTX2SKILL_LITE_PROMPT

    assert 'If an object schema omits additionalProperties, do not describe or enforce a ban on extra properties.' in _CTX2SKILL_LITE_PROMPT
    assert 'Preserve an explicit additionalProperties: false only when the source declares it.' in _CTX2SKILL_LITE_PROMPT


def test_api_candidate_drops_unsupported_extra_property_ban_but_keeps_source_declared_ban():
    raw = json.loads((FIXTURES / 'c03.json').read_text(encoding='utf-8'))
    raw['candidates'][0]['prompt_format'] = 'literal'
    candidate = raw['candidates'][0]
    candidate['input_schema']['properties']['pet']['additionalProperties'] = False
    candidate['prompt_template'] += ' Validate JSON body against the source, no additional properties.'
    source = """
openapi: 3.0.0
components:
  requestBodies:
    Pet:
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/Pet'
  schemas:
    Pet:
      type: object
      properties:
        name:
          type: string
"""
    enriched = _metadata_from_ctx2skill_payload(raw, raw_content=source, source_type='api_doc', known_skills=[])
    pet = enriched['candidate_interface']['input_schema']['properties']['pet']
    assert pet['additionalProperties'] is False  # Never silently relax a declared invocation gate.
    assert 'additional properties follow the source schema' in enriched['candidate_implementation']['prompt_template']
    assert any(item['kind'] == 'removed_unsupported_constraint' for item in enriched['ctx2skill_evidence']['source_schema_enrichment'])

    declared = source.replace("type: object\n      properties:", "type: object\n      additionalProperties: false\n      properties:")
    raw2 = json.loads((FIXTURES / 'c03.json').read_text(encoding='utf-8'))
    raw2['candidates'][0]['input_schema']['properties']['pet']['additionalProperties'] = False
    out2 = _metadata_from_ctx2skill_payload(raw2, raw_content=declared, source_type='api_doc', known_skills=[])
    assert out2['candidate_interface']['input_schema']['properties']['pet']['additionalProperties'] is False


def test_api_candidate_distinguishes_absent_fields_from_present_invalid_values():
    raw = json.loads((FIXTURES / 'c03.json').read_text(encoding='utf-8'))
    source = """
openapi: 3.0.0
components:
  requestBodies:
    Pet:
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/Pet'
  schemas:
    Pet:
      type: object
      properties:
        petId:
          type: integer
"""
    api_result = _metadata_from_ctx2skill_payload(raw, raw_content=source, source_type='api_doc', known_skills=[])
    prompt = api_result['candidate_implementation']['prompt_template']
    assert 'report required only when the key is absent' in prompt
    assert 'including with false, 0, an empty value, or null' in prompt
    assert "Follow the task output contract's path convention." in prompt

    document_result = _metadata_from_ctx2skill_payload(
        raw, raw_content=source, source_type='document', known_skills=[]
    )
    assert 'report required only when the key is absent' not in document_result['candidate_implementation']['prompt_template']

def test_same_display_name_does_not_merge_candidate_evidence():
    e=metadata()['ctx2skill_evidence'];rows=e['candidate_scores']
    assert len(set(r['name'] for r in rows))==1
    assert len(set(r['candidate_id'] for r in rows))==2
    assert all(r['challenge_count']==3 and r['executed_challenges']==0 and r['passed_challenges'] is None for r in rows)
    for r in rows:
        linked=[j for j in e['judge_results'] if j['candidate_id']==r['candidate_id']]
        assert len(linked)==3
        assert all(j['executed'] is False and j['passed'] is None and j['evidence_origin']=='model_self_score' for j in linked)

def test_candidate_identity_stable_on_reordering_and_distinguishes_duplicates():
    raw=json.loads((FIXTURES/'c02.json').read_text(encoding='utf-8'));a=metadata(payload=raw)['ctx2skill_evidence']
    raw['candidates'].reverse();b=metadata(payload=raw)['ctx2skill_evidence']
    assert {r['score']:r['candidate_id'] for r in a['candidate_scores']}=={r['score']:r['candidate_id'] for r in b['candidate_scores']}
    raw['candidates']=[deepcopy(raw['candidates'][0]),deepcopy(raw['candidates'][0])]
    c=metadata(payload=raw)['ctx2skill_evidence']['candidate_scores'];assert len(set(x['candidate_id'] for x in c))==2

def test_different_self_scores_are_linked_but_not_executed_results():
    raw=json.loads((FIXTURES/'c02.json').read_text(encoding='utf-8'));raw['candidates'][1]['score']=.1
    e=metadata(payload=raw)['ctx2skill_evidence']
    assert sorted(r['self_score_threshold_met'] for r in e['candidate_scores'])==[False,True]
    assert all(r['passed_challenges'] is None for r in e['candidate_scores'])

@pytest.mark.parametrize('source_type',['document','script','api_doc'])
def test_default_contract_keeps_its_specific_assertions(source_type):
    selected={'name':'default','input_schema':_default_input_schema(source_type),'output_schema':_default_output_schema(source_type)}
    specs=_candidate_evaluation_for(selected,source_type)['verifier_specs']
    assert {'type':'json_equals','path':'output.verifier.passed','value':True} in specs
    assert any(s['type']=='json_schema' for s in specs)

@pytest.mark.asyncio
async def test_native_executor_keeps_runtime_metadata_outside_business_output():
    schema={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}
    selected={'name':'strict','input_schema':{'type':'object','properties':{'task':{'type':'string'}},'required':['task']},'output_schema':schema}
    evaluation=_candidate_evaluation_for(selected,'document')
    skill=Skill(name='strict',description='Fixed local contract probe',interface={'input_schema':selected['input_schema'],'output_schema':schema},implementation={'language':'prompt','prompt_template':'Return the answer as JSON.'},evaluation=evaluation)
    class Fake:
        def chat(self,*args,**kwargs):return SimpleNamespace(content=json.dumps({'answer':'recorded'}))
    executor=SkillExecutor(llm_client=Fake());record=await executor.execute_single(skill,{'task':'read'})
    assert record.output_data == {'answer':'recorded'}
    assert record.state_after == {'strict_result': {'answer':'recorded'}}
    assert evaluate_verifier_specs(evaluation['verifier_specs'],{'input':{'task':'read'},'output':record.output_data}).passed
    bad=deepcopy(record.output_data);bad['extra']='not declared'
    assert not evaluate_verifier_specs(evaluation['verifier_specs'],{'input':{'task':'read'},'output':bad}).passed

@pytest.mark.parametrize('schema,value,expected',[
    ({'type':'integer'},True,False),({'type':'integer'},2,True),
    ({'type':'array','items':{'type':'string'}},['x',2],False),
    ({'type':'string','enum':['yes']},'no',False),
    ({'type':'object','properties':{'x':{'type':'string'}},'required':['x']},{},False),
    ({'$defs':{'x':{'type':'integer'}},'$ref':'#/$defs/x'},3,True),
    ({'$ref':'https://example.invalid/must-not-fetch'},3,False),
    ({'type':'not-a-type'},3,False),
])
def test_schema_rule_uses_real_local_validation(schema,value,expected):
    result=evaluate_verifier_specs([{'type':'json_schema','path':'output','schema':schema}],{'output':value})
    assert result.passed is expected

def test_remote_reference_fails_without_network(monkeypatch):
    import socket
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(True)
        raise AssertionError('network must not be used')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    result=evaluate_verifier_specs([{'type':'json_schema','path':'output','schema':{'$ref':'https://example.invalid/schema'}}],{'output':{}})
    assert not result.passed and not calls

@pytest.mark.parametrize('value',[False,0,[],None])
def test_declared_content_presence_does_not_invent_nonempty_constraints(value):
    spec={'type':'json_declared_content','path':'output','schema':{'properties':{'answer':{}}}}
    assert evaluate_verifier_specs([spec],{'output':{'answer':value}}).passed

def test_missing_roots_and_wrong_schema_fail_closed():
    assert not evaluate_verifier_specs([{'type':'json_schema','path':'input','schema':{}}],{'output':{}}).passed
    assert not evaluate_verifier_specs([{'type':'json_schema','path':'output','schema':None}],{'output':{}}).passed

def test_runtime_fields_explicitly_declared_are_not_stripped():
    spec={'type':'json_schema','path':'output','runtime_output':True,'schema':{'type':'object','properties':{'skill_name':{'type':'integer'}},'required':['skill_name']}}
    assert not evaluate_verifier_specs([spec],{'output':{'skill_name':'wrong'}}).passed
    assert evaluate_verifier_specs([spec],{'output':{'skill_name':7}}).passed

def test_missing_score_is_a_default_heuristic_not_model_evidence():
    raw=json.loads((FIXTURES/'c02.json').read_text(encoding='utf-8'))
    raw['candidates'][0].pop('score')
    e=metadata(payload=raw)['ctx2skill_evidence']
    assert any(r['evidence_origin']=='default_heuristic' for r in e['candidate_scores'])
    assert all(r['executed_challenges']==0 and r['passed_challenges'] is None for r in e['candidate_scores'])

def test_duplicate_challenge_labels_remain_distinct_unexecuted_slots():
    raw=json.loads((FIXTURES/'c02.json').read_text(encoding='utf-8'));raw['challenges']=[raw['challenges'][0],deepcopy(raw['challenges'][0])]
    e=metadata(payload=raw)['ctx2skill_evidence'];candidate=e['candidate_scores'][0]['candidate_id']
    linked=[j for j in e['judge_results'] if j['candidate_id']==candidate]
    assert [j['challenge_index'] for j in linked]==[0,1]
    assert all(j['passed'] is None and not j['executed'] for j in linked)

@pytest.mark.parametrize('properties',[None,1,[],False])
def test_malformed_properties_return_failure_without_throwing(properties):
    schema={'type':'object','properties':properties}
    selected={'input_schema':{'type':'object'},'output_schema':schema}
    specs=_candidate_evaluation_for(selected,'document')['verifier_specs']
    result=evaluate_verifier_specs(specs,{'input':{},'output':{'answer':'x'}})
    assert not result.passed

@pytest.mark.parametrize('flag',['passed','success','ok','verifier'])
def test_only_self_reported_flags_are_not_business_evidence(flag):
    schema={'type':'object','properties':{flag:{'type':'boolean'}}}
    spec={'type':'json_declared_content','path':'output','schema':schema}
    assert not evaluate_verifier_specs([spec],{'output':{flag:True}}).passed

@pytest.mark.parametrize('score',[None,'not-a-number',True,float('nan'),float('inf'),-float('inf')])
def test_invalid_scores_are_default_heuristics(score):
    raw=json.loads((FIXTURES/'c02.json').read_text(encoding='utf-8'));raw['candidates'][0]['score']=score
    evidence=metadata(payload=raw)['ctx2skill_evidence'];row=evidence['candidate_scores'][0]
    assert row['score']==.72 and row['evidence_origin']=='default_heuristic'
    assert all('Proposal score only' in j['rationale'] for j in evidence['judge_results'])

@pytest.mark.parametrize('score,normalized',[(-1,0),(2,1)])
def test_out_of_range_scores_have_explicit_normalization_provenance(score,normalized):
    raw=json.loads((FIXTURES/'c02.json').read_text(encoding='utf-8'));raw['candidates'][0]['score']=score
    row=metadata(payload=raw)['ctx2skill_evidence']['candidate_scores'][0]
    assert row['score']==normalized and row['evidence_origin']=='model_self_score_clamped'
