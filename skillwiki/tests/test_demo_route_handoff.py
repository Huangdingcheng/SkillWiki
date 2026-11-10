"""Bounded configuration handoffs; provider responses are deterministic fixtures."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes import ingest
from skillwiki.layers.input_knowledge.pipeline import ExperiencePipeline
from skillwiki.layers.input_knowledge.api_source_rules import START, END, source_contract
from skillwiki.layers.skill_governance.merger import SkillMerger
from skillwiki.layers.skill_management.maintainer import SkillMaintainerAgent
from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillState
from skillwiki.utils.llm_client import LLMResponse

def policy():
    source={'openapi':'3.0.3','paths':{'/items':{'post':{'requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','properties':{'name':{'type':'string'}},'required':['name']}}}},'responses':{'200':{'description':'OK'}}}}}}
    raw=json.dumps(source)
    return {'profile':'openapi-json-request-v1','source_document':raw,'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),'operation':{'path':'/items','method':'post'}}

def parent(mode='literal', guarded=True):
    p=policy()
    prompt='Use ordinary example {method: POST}. '
    if mode=='python_format': prompt='Use {task}. '
    if guarded:
        block=source_contract({'source_operation':p['operation']},p['source_document'])
        prompt+=START+json.dumps(block)+END
    return Skill(name='handoff_parent',description='Bounded handoff fixture',implementation=SkillImplementation(prompt_template=prompt,prompt_format=mode,request_body_validation=p if guarded else None))

class Fixed:
    def __init__(self, data): self.data=data;self.calls=[];self._cfg=SimpleNamespace(api_key='fixture-only')
    def chat(self,messages,**kwargs):
        self.calls.append([m.to_dict() for m in messages])
        return LLMResponse(json.dumps(self.data),'fixed-offline',{},'stop')

def api(wiki,pipeline=None):
    app=FastAPI();app.include_router(ingest.router,prefix='/api/v1')
    app.dependency_overrides[get_app_state]=lambda:SimpleNamespace(wiki=wiki,pipeline=pipeline,graph=None,auditor=SkillAuditorAgent())
    return TestClient(app)

def payload(s):
    return {'source_type':'past_skills','unit_id':s.skill_id,'raw_content':s.model_dump_json(),'name':s.name,'description':s.description,'prompt_template':s.implementation.prompt_template,'prompt_format':s.implementation.prompt_format,'request_body_validation':s.implementation.request_body_validation,'input_schema':{'type':'object','properties':{}},'output_schema':{'type':'object','properties':{}}}

def test_candidate_review_create_preserves_guard():
    s=parent();wiki=MemoryWikiManager();c=api(wiki)
    a=c.post('/api/v1/ingest/audit-candidate',json=payload(s));assert a.status_code==200 and a.json()['passed']
    r=c.post('/api/v1/ingest/create-candidate',json=payload(s));assert r.status_code==201,r.text
    got=asyncio.run(wiki.get(r.json()['created_skill']['skill_id']))
    assert got.implementation.request_body_validation==s.implementation.request_body_validation

def test_past_skill_pipeline_preserves_guard():
    s=parent();p=ExperiencePipeline(Fixed({}));r=p.process(s.model_dump_json(),'past_skills')
    assert r.success,r.errors
    assert r.units[0].metadata['candidate_implementation'].get('request_body_validation')==s.implementation.request_body_validation

@pytest.mark.parametrize('service',['governance','maintainer'])
@pytest.mark.parametrize('operation',['merge','split'])
def test_rebuild_preserves_mode_policy_and_requires_new_proof(service,operation):
    s=parent();impl=s.implementation.model_dump(mode='json')
    child={'name':'child','description':'Retain the source request contract','implementation':impl,'prompt_template':impl['prompt_template'],'prompt_format':impl['prompt_format'],'request_body_validation':impl['request_body_validation']}
    fixed=Fixed({'merged_name':'merged','merged_description':'Retain source contract','merged_implementation':impl,'sub_skills':[child]})
    if service=='governance':
        other=parent();other.name='other'
        op=SkillMerger(fixed);r=asyncio.run(op.merge(s,other) if operation=='merge' else op.split(s));assert r.success,r.error
        got=r.merged_skill if operation=='merge' else r.sub_skills[0]
    else:
        other=parent();other.name='other'
        op=SkillMaintainerAgent(fixed);r=op.merge(s,other) if operation=='merge' else op.split(s);assert r.success,r.reason
        got=r.updated_skill if operation=='merge' else r.new_skills[0]
    assert got.implementation.prompt_format=='literal'
    assert got.implementation.request_body_validation==impl['request_body_validation']
    assert got.skill_id!=s.skill_id and got.evaluation.harness_validation=={}
    from skillwiki.layers.skill_governance.version_evidence import require_current_identity_proof
    with pytest.raises(ValueError,match='Fresh verification'):
        require_current_identity_proof(got,SkillState.RELEASED)

def test_composite_declared_order_controls_observable_state():
    async def run():
        w=MemoryWikiManager()
        for key in ['a','b']:
            await w.create(Skill(skill_id=key,name=key,implementation=SkillImplementation(code=f"output['order'] = state.get('order', '') + '{key}'")))
        s=Skill(name='combo',implementation=SkillImplementation(sub_skill_ids=['a','b'],execution_order=['b','a']))
        return await SkillExecutor(skill_registry=w).execute_single(s,{})
    r=asyncio.run(run());assert r.status.value=='success',r.error_message
    assert r.state_after['order']=='ba'

def test_invalid_order_is_rejected_before_any_subskill():
    async def run():
        w=MemoryWikiManager();await w.create(Skill(skill_id='a',name='a',implementation=SkillImplementation(code="output['ran']=True")))
        return await SkillExecutor(skill_registry=w).execute_single(Skill(name='combo',implementation=SkillImplementation(sub_skill_ids=['a'],execution_order=['missing'])),{})
    r=asyncio.run(run());assert r.status.value=='failed'
    assert r.state_after=={} and 'execution_order' in r.error_message

def test_formalizer_keeps_explicit_configuration():
    from skillwiki.layers.skill_construction.formalizer import SkillFormalizer
    from skillwiki.models.experience_model import SkillProposal
    s=parent();response=s.model_dump(mode='json')
    proposal=SkillProposal(source_experience_id='source-test',proposed_name=s.name,proposed_description=s.description)
    got=asyncio.run(SkillFormalizer(Fixed(response)).formalize(proposal))
    assert got.implementation.prompt_format=='literal'
    assert got.implementation.request_body_validation==s.implementation.request_body_validation

@pytest.mark.parametrize('mode',['literal','python_format',None])
def test_rebuild_preserves_mode_and_native_interpretation(mode):
    from skillwiki.layers.skill_governance.implementation_handoff import rebuild_implementation
    s=parent(mode,False)
    if mode is None: s.implementation.prompt_template='Use {task} and literal JSON {"key":1}.'
    data={'prompt_template':s.implementation.prompt_template}
    s.implementation=rebuild_implementation(data,[s]);fixed=Fixed({'ok':True})
    r=asyncio.run(SkillExecutor(llm_client=fixed,max_retries=0).execute_single(s,{'task':'sample'}))
    assert r.status.value=='success',r.error_message
    if mode=='python_format':
        assert 'Use sample.' in json.dumps(fixed.calls)
        miss=asyncio.run(SkillExecutor(llm_client=fixed,max_retries=0).execute_single(s,{}))
        assert miss.status.value=='failed'
    assert s.implementation.prompt_format==mode

@pytest.mark.parametrize('mutation',['missing_block','wrong_hash','changed_source','different_parents'])
def test_rebuild_does_not_guess_source_identity(mutation):
    from skillwiki.layers.skill_governance.implementation_handoff import rebuild_implementation
    s=parent();data=s.implementation.model_dump(mode='json');parents=[s]
    if mutation=='missing_block': data['prompt_template']='Some new task.'
    elif mutation=='wrong_hash': data['request_body_validation']['source_sha256']='0'*64
    elif mutation=='changed_source':
        raw=json.loads(data['request_body_validation']['source_document']);raw['info']={'title':'new source','version':'2'}
        raw=json.dumps(raw);block=source_contract({'source_operation':policy()['operation']},raw)
        data['prompt_template']=START+json.dumps(block)+END
    else:
        other=parent();other.implementation.request_body_validation=None;parents.append(other);data.pop('request_body_validation')
    with pytest.raises(ValueError): rebuild_implementation(data,parents)

def test_explicit_new_source_policy_is_checked_and_old_proof_not_inherited():
    from skillwiki.layers.skill_governance.implementation_handoff import rebuild_implementation
    s=parent();raw=json.loads(policy()['source_document']);raw['info']={'title':'new','version':'2'};raw=json.dumps(raw)
    p={**policy(),'source_document':raw,'source_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    block=source_contract({'source_operation':p['operation']},raw)
    data={'prompt_template':START+json.dumps(block)+END,'prompt_format':'literal','request_body_validation':p}
    got=rebuild_implementation(data,[s]);assert got.request_body_validation==p
    fixed=Fixed({'merged_name':'fresh','merged_description':'New source draft','merged_implementation':data})
    r=asyncio.run(SkillMerger(fixed).merge(s,parent()));assert r.success,r.error
    assert r.merged_skill.evaluation.harness_validation=={}
    from skillwiki.layers.skill_governance.version_evidence import require_current_identity_proof
    with pytest.raises(ValueError):require_current_identity_proof(r.merged_skill,SkillState.RELEASED)

def test_candidate_import_rejects_stale_source_policy_without_write():
    s=parent();body=payload(s);body['request_body_validation']['source_sha256']='0'*64
    w=MemoryWikiManager();r=api(w).post('/api/v1/ingest/create-candidate',json=body)
    assert r.status_code==400,r.text
    assert asyncio.run(w.list())==[]

def test_default_composite_order_is_unchanged():
    async def run():
        w=MemoryWikiManager()
        for key in ['a','b']:await w.create(Skill(skill_id=key,name=key,implementation=SkillImplementation(code=f"output['order']=state.get('order','')+'{key}'")))
        return await SkillExecutor(skill_registry=w).execute_single(Skill(name='combo',implementation=SkillImplementation(sub_skill_ids=['a','b'])),{})
    assert asyncio.run(run()).state_after['order']=='ab'

def test_import_new_identity_does_not_copy_supplied_old_proof():
    s=parent();body=payload(s);body['evaluation']={'harness_validation':{'current_identity_proof':{'skill_id':s.skill_id,'status':'passed'}},'validation_summary':'old pass'}
    w=MemoryWikiManager();r=api(w).post('/api/v1/ingest/create-candidate',json=body);assert r.status_code==201,r.text
    got=asyncio.run(w.get(r.json()['created_skill']['skill_id']))
    assert got.skill_id!=s.skill_id
    assert got.evaluation.harness_validation=={} and got.evaluation.validation_summary is None

def test_parse_and_create_preserves_policy_and_invalidates_old_proof():
    s=parent();s.evaluation.harness_validation={'current_identity_proof':{'skill_id':s.skill_id,'status':'passed'}}
    w=MemoryWikiManager();c=api(w,ExperiencePipeline(Fixed({})))
    r=c.post('/api/v1/ingest/parse-and-create',json={'source_type':'past_skills','content':s.model_dump_json()})
    assert r.status_code==200 and r.json()['success'],r.text
    got=asyncio.run(w.get(r.json()['created_skills'][0]['skill_id']))
    assert got.implementation.prompt_format=='literal'
    assert got.implementation.request_body_validation==s.implementation.request_body_validation
    assert got.evaluation.harness_validation=={}
    from skillwiki.layers.skill_governance.version_evidence import require_current_identity_proof
    with pytest.raises(ValueError):require_current_identity_proof(got,SkillState.RELEASED)

def test_formalizer_rejects_invalid_explicit_policy_instead_of_fallback():
    from skillwiki.layers.skill_construction.formalizer import SkillFormalizer
    from skillwiki.models.experience_model import SkillProposal
    s=parent();data=s.model_dump(mode='json');data['implementation']['request_body_validation']['source_sha256']='0'*64
    p=SkillProposal(source_experience_id='source-test',proposed_name=s.name,proposed_description=s.description)
    with pytest.raises(ValueError):asyncio.run(SkillFormalizer(Fixed(data)).formalize(p))

def test_guarded_merge_cannot_silently_drop_entire_implementation():
    s=parent();other=parent();other.name='other'
    result=asyncio.run(SkillMerger(Fixed({'merged_name':'bad_merge','merged_description':'Missing implementation'})).merge(s,other))
    assert not result.success and 'implementation' in result.error
