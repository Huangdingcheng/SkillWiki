from copy import deepcopy
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.skill_model import Skill

ROOT=Path(__file__).resolve().parents[2]
MATERIALS=ROOT/'research/stage2-capability-response-repair-20261101/frozen-materials.json'
START='<skillwiki-source-response-rules>'
END='</skillwiki-source-response-rules>'

def module():
    name='skillwiki.layers.input_knowledge.api_response_rules'
    assert importlib.util.find_spec(name) is not None, 'response source preservation is missing'
    return importlib.import_module(name)

def data(label='D'):
    m=json.loads(MATERIALS.read_text(encoding='utf-8'));b=m['bases'][label];s=b['skill']
    c={'name':s['name'],'input_schema':deepcopy(s['interface']['input_schema']),
       'output_schema':deepcopy(s['interface']['output_schema']),
       'prompt_template':s['implementation']['prompt_template'],'prompt_format':'literal',
       'source_operation':deepcopy(b['source_operation'])}
    return c,json.dumps(b['source'],ensure_ascii=False),s,m

def block(c):return json.loads(c['prompt_template'].split(START,1)[1].split(END,1)[0])

def test_delete_without_request_body_keeps_default_and_all_response_rules():
    c,raw,_,_=data();before=deepcopy(c)
    notes=module().attach_response_rules(c,raw)
    payload=block(c)
    assert set(payload['responses'])=={'200','400','default'}
    assert payload['responses']['default']['description']=='Unexpected error'
    assert payload['operation']=={'path':'/pet/{petId}','method':'DELETE'}
    assert c['input_schema']==before['input_schema'] and c['output_schema']==before['output_schema']
    assert c['prompt_template'].startswith(before['prompt_template'])
    assert notes[0]['kind']=='source_response_rules_attached'

def test_request_petcreate_and_response_pet_cannot_be_confused():
    c,raw,_,_=data('EP');module().attach_response_rules(c,raw)
    response=block(c)['responses']['200']['content']['application/json']['schema']
    assert response['required']==['name','photoUrls']
    assert 'status' not in response['required'] and response['properties']['status']['enum']
    assert 'PetCreate' not in json.dumps(block(c))

def test_explicit404_is_separate_from_default():
    c,raw,_,_=data('G');module().attach_response_rules(c,raw)
    assert '404' in block(c)['responses'] and 'default' in block(c)['responses']

@pytest.mark.parametrize('mode',[None,'python_format'])
def test_unconfirmed_template_mode_is_untouched(mode):
    c,raw,_,_=data();c['prompt_format']=mode;before=deepcopy(c)
    result=module().attach_response_rules(c,raw)
    assert c==before and result[0]['reason']=='explicit_literal_prompt_required'

def test_request_only_interface_is_not_guessed_from_prose():
    c,raw,_,_=data();c['output_schema']['properties'].pop('response');before=deepcopy(c)
    result=module().attach_response_rules(c,raw)
    assert c==before and result[0]['reason']=='response_task_not_declared'

@pytest.mark.parametrize('enum',[None,False,5,'report'])
def test_malformed_response_declaration_is_skipped_without_throwing(enum):
    c,raw,_,_=data();c['output_schema']['properties']['decision']['enum']=enum;before=deepcopy(c)
    notes=module().attach_response_rules(c,raw)
    assert c==before and notes[0]['reason']=='response_task_not_declared'

def test_nonjson_evaluation_boundary_is_explicit_and_not_a_skill_failure():
    spec=importlib.util.spec_from_file_location('response_boundary_oracle',ROOT/'research/stage2-demo-api-execution-20261029/oracle/scorer.py')
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    case={'case_id':'boundary','task_id':'API-P','kind':'response',
          'input':{'status':200,'content_type':'text/plain','body':'not JSON'}}
    with pytest.raises(ValueError,match='Only JSON response media admitted'):oracle.expected(case)
    case['task_id']='API-D'
    assert oracle.expected(case)['response']['body_schema_valid'] is None

@pytest.mark.parametrize('mutation',['multiple','external','cycle','range','malformed','missing'])
def test_ambiguous_or_unsupported_source_is_not_partly_appended(mutation):
    c,raw,_,_=data('P');doc=json.loads(raw);op=doc['paths']['/pet']['post']
    if mutation=='multiple':
        c.pop('source_operation');doc['paths']['/other']={'get':deepcopy(op)}
    elif mutation=='external':op['responses']['200']={'$ref':'https://example.invalid/response'}
    elif mutation=='cycle':op['responses']['200']={'$ref':'#/paths/~1pet/post/responses/200'}
    elif mutation=='range':op['responses']['2XX']={'description':'ranged'}
    elif mutation=='malformed':op['responses']['200']['content']=False
    else:op['responses']={}
    before=deepcopy(c);notes=module().attach_response_rules(c,json.dumps(doc))
    assert c==before and notes[0]['kind']=='source_response_rules_skipped'

def test_idempotent_attachment_and_changed_source_conflict():
    c,raw,_,_=data();module().attach_response_rules(c,raw);before=deepcopy(c)
    assert module().attach_response_rules(c,raw)==[] and c==before
    doc=json.loads(raw);doc['paths']['/pet/{petId}']['delete']['responses']['404']={'description':'now explicit'}
    notes=module().attach_response_rules(c,json.dumps(doc))
    assert c==before and notes[0]['reason']=='existing_response_rules_conflict'

def test_literal_source_examples_and_request_prose_are_not_rewritten():
    c,raw,_,_=data('P');doc=json.loads(raw)
    text='Validate response body with no additional properties {x}'
    doc['components']['schemas']['Pet']['example']={'name':text}
    before=c['prompt_template'];module().attach_response_rules(c,json.dumps(doc))
    assert c['prompt_template'].startswith(before)
    assert block(c)['responses']['200']['content']['application/json']['schema']['example']=={'name':text}

@pytest.mark.asyncio
async def test_real_native_executor_receives_rules_and_complete_original_input():
    c,raw,s,m=data();module().attach_response_rules(c,raw)
    s['implementation'].update(prompt_template=c['prompt_template'],prompt_format='literal')
    value=next(x for x in m['cases'] if x['id']=='D-default404')['input']
    calls=[]
    class Fixed:
        def chat(self,messages):
            calls.append(messages);return SimpleNamespace(content='{"offline_transport_sentinel":true}')
    result=await SkillExecutor(llm_client=Fixed(),max_retries=0).execute_single(Skill.model_validate(s),value,initial_state={})
    assert result.status.value=='success' and len(calls)==1
    assert calls[0][1].content==c['prompt_template']
    assert json.loads(calls[0][-1].content.split(chr(10),1)[1])==value

@pytest.mark.parametrize('backend',['memory','git'])
def test_native_candidate_import_and_persistent_reload_keep_response_block(backend,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from skillwiki.api.deps import get_app_state
    from skillwiki.api.memory_store import MemoryWikiManager,MemoryGraphManager
    from skillwiki.api.routes import ingest
    from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
    from skillwiki.layers.skill_repository.repository import SkillWikiManager
    c,raw,_,_=data();module().attach_response_rules(c,raw)
    wiki=MemoryWikiManager() if backend=='memory' else SkillWikiManager(storage_dir=tmp_path/'repo',auto_commit=False)
    app=FastAPI();app.include_router(ingest.router,prefix='/api/v1')
    app.dependency_overrides[get_app_state]=lambda:SimpleNamespace(wiki=wiki,graph=MemoryGraphManager(),auditor=SkillAuditorAgent(),pipeline=None)
    payload={'source_type':'api_doc','unit_id':'response-boundary','raw_content':raw,'name':'response_boundary',
        'description':'Interpret a supplied response using a verified operation source.',
        'skill_type':'atomic','input_schema':c['input_schema'],'output_schema':c['output_schema'],
        'prompt_template':c['prompt_template'],'prompt_format':'literal','postconditions':['Return the declared response report.']}
    response=TestClient(app).post('/api/v1/ingest/create-candidate',json=payload)
    assert response.status_code==201,response.json()
    created=response.json()['created_skill'];sid=created['skill_id']
    if backend=='git':wiki=SkillWikiManager(storage_dir=tmp_path/'repo',auto_commit=False)
    stored=asyncio.run(wiki.get(sid))
    assert stored.implementation.prompt_template==c['prompt_template']
    assert stored.implementation.prompt_format=='literal' and stored.state.value=='S1'
    assert not stored.evaluation.validation_summary
