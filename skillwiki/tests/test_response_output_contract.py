from copy import deepcopy
import asyncio, importlib.util, json
from pathlib import Path
from types import SimpleNamespace
import pytest
from skillwiki.layers.input_knowledge import api_response_rules as rules
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.skill_model import Skill

ROOT=Path(__file__).resolve().parents[2]
MATERIAL=ROOT/'research/stage2-capability-response-repair-20261101/frozen-materials.json'
START='<skillwiki-response-output-contract>'
END='</skillwiki-response-output-contract>'

def sample():
    m=json.loads(MATERIAL.read_text(encoding='utf-8'));s=deepcopy(m['bases']['G']['skill'])
    c={'name':s['name'],'input_schema':deepcopy(s['interface']['input_schema']),
       'output_schema':deepcopy(s['interface']['output_schema']),
       'prompt_format':'literal','prompt_template':s['implementation']['prompt_template']}
    return c,s,m

def apply(c):
    fn=getattr(rules,'attach_response_output_contract',None)
    assert callable(fn),'output schema preservation not implemented'
    return fn(c)

def parsed(c):return json.loads(c['prompt_template'].split(START,1)[1].split(END,1)[0])

def test_exact_schema_preserved_with_arrays_nullable_nested_and_false():
    c,_,_=sample();c['output_schema']['properties']['custom']={'type':['object','null'],'additionalProperties':False,'properties':{'flag':{'type':'boolean','default':False}}}
    old=deepcopy(c);notes=apply(c)
    assert parsed(c)==old['output_schema']
    assert c['input_schema']==old['input_schema'] and c['output_schema']==old['output_schema']
    assert c['prompt_template'].startswith(old['prompt_template'])
    assert notes[0]['kind']=='response_output_contract_attached'

def test_no_issue_name_hardcoding_schema_can_have_other_result_fields():
    c,_,_=sample();c['output_schema']['properties'].pop('issues');c['output_schema']['required'].remove('issues')
    apply(c);assert 'issues' not in parsed(c)['properties']

def test_idempotent_and_schema_change_conflict_does_not_append_stale_block():
    c,_,_=sample();apply(c);old=deepcopy(c);assert apply(c)==[] and c==old
    c['output_schema']['properties']['extra']={'type':'integer'};before=deepcopy(c)
    result=apply(c);assert c==before and result[0]['reason']=='existing_output_contract_conflict'

@pytest.mark.parametrize('mode',[None,'python_format'])
def test_named_or_legacy_templates_not_changed(mode):
    c,_,_=sample();c['prompt_format']=mode;before=deepcopy(c)
    assert apply(c)[0]['reason']=='explicit_literal_prompt_required' and c==before

@pytest.mark.parametrize('schema',[None,{},True,{'type':'object','properties':{'x':{'type':'string'}}}])
def test_absent_or_unrelated_schema_not_changed(schema):
    c,_,_=sample();c['output_schema']=schema;before=deepcopy(c)
    assert apply(c)[0]['kind']=='response_output_contract_skipped' and c==before

def test_invalid_schema_is_not_treated_as_valid_source():
    c,_,_=sample();c['output_schema']['properties']['issues']['type']='not-a-json-type';before=deepcopy(c)
    assert apply(c)[0]['reason']=='invalid_output_schema' and c==before

def test_unsupported_schema_dialect_is_not_silently_reinterpreted():
    c,_,_=sample();c['output_schema']['$schema']='https://example.invalid/unsupported-schema';before=deepcopy(c)
    assert apply(c)[0]['reason']=='unsupported_output_schema_dialect' and c==before

def test_oversized_contract_is_skipped_without_truncating_original():
    c,_,_=sample();c['output_schema']['description']='字面文本'*15000;before=deepcopy(c)
    assert apply(c)[0]['reason']=='output_contract_size_limit' and c==before

def test_partial_existing_marker_is_reported_without_second_block():
    c,_,_=sample();c['prompt_template']+=START;before=deepcopy(c)
    assert apply(c)[0]['reason']=='existing_output_contract_conflict' and c==before

@pytest.mark.asyncio
async def test_native_request_carries_schema_and_does_not_repair_model_null():
    c,s,m=sample();apply(c);s['implementation'].update(prompt_format='literal',prompt_template=c['prompt_template'])
    value=next(x['input'] for x in m['cases'] if x['id']=='G-explicit404');calls=[]
    answer={'case_id':value['case_id'],'decision':'report','issues':None,'request':None,'response':{'status':404,'matched_response':'404','classification':'error','body_schema_valid':None}}
    class Fixed:
        def chat(self,messages):calls.append(messages);return SimpleNamespace(content=json.dumps(answer))
    record=await SkillExecutor(llm_client=Fixed(),max_retries=0).execute_single(Skill.model_validate(s),value,initial_state={})
    assert record.status.value=='success' and len(calls)==1
    assert parsed({'prompt_template':calls[0][1].content})==c['output_schema']
    assert record.output_data['issues'] is None

@pytest.mark.parametrize('backend',['memory','git'])
def test_real_candidate_import_reload_retains_contract_and_identity(backend,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from skillwiki.api.deps import get_app_state
    from skillwiki.api.memory_store import MemoryWikiManager,MemoryGraphManager
    from skillwiki.api.routes import ingest
    from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
    from skillwiki.layers.skill_repository.repository import SkillWikiManager
    c,_,_=sample();apply(c)
    wiki=MemoryWikiManager() if backend=='memory' else SkillWikiManager(storage_dir=tmp_path/'repo',auto_commit=False)
    app=FastAPI();app.include_router(ingest.router,prefix='/api/v1')
    app.dependency_overrides[get_app_state]=lambda:SimpleNamespace(wiki=wiki,graph=MemoryGraphManager(),auditor=SkillAuditorAgent(),pipeline=None)
    payload={'source_type':'api_doc','unit_id':'output-contract','raw_content':'fixed source','name':'response_contract_roundtrip',
      'description':'Interpret a supplied response against a source.','skill_type':'atomic','input_schema':c['input_schema'],
      'output_schema':c['output_schema'],'prompt_template':c['prompt_template'],'prompt_format':'literal','postconditions':['Return the declared report.']}
    response=TestClient(app).post('/api/v1/ingest/create-candidate',json=payload)
    assert response.status_code==201,response.json()
    sid=response.json()['created_skill']['skill_id']
    if backend=='git':wiki=SkillWikiManager(storage_dir=tmp_path/'repo',auto_commit=False)
    stored=asyncio.run(wiki.get(sid));assert stored.implementation.prompt_template==c['prompt_template']
    assert stored.interface.output_schema==c['output_schema'] and stored.state.value=='S1'
    assert not stored.evaluation.validation_summary
