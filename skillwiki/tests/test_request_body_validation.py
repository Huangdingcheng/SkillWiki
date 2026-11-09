"""Opt-in request policy: native runtime, source boundaries, and raw evidence."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from skillwiki.layers.skill_runtime.request_body_validation import (
    compile_request_body_policy, request_body_issues, RequestBodyValidationError,
)
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.layers.skill_runtime.planner import ExecutionPlan, PlanStep
from skillwiki.layers.skill_governance.artifact_identity import canonical_skill_artifact_hash
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillInterface
from skillwiki.utils.llm_client import LLMResponse

ROOT = Path(__file__).parents[2]
SOURCE = ROOT/'research/stage2-demo-api-pilot-20261029/roster-preparation/event-source-deltas/E-P-source.json'
VALID = {'name': 'dog', 'photoUrls': [], 'status': 'available'}

def policy(doc=None):
    raw = SOURCE.read_bytes().decode('utf-8') if doc is None else json.dumps(doc)
    return {'profile':'openapi-json-request-v1','source_document':raw,
            'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'operation':{'path':'/pet','method':'post'}}

def candidate(enabled=True):
    return Skill(name='source_guard', implementation=SkillImplementation(
        prompt_template='Ordinary text {method: POST}; validate the supplied task.',
        prompt_format='literal', request_body_validation=policy() if enabled else None),
        interface=SkillInterface(input_schema={}, output_schema={'type':'object'}))

def data(body=None, kind='request'):
    return {'case_id':'T','task_data':{'case_id':'T','kind':kind,
        'input':{'body':copy.deepcopy(VALID if body is None else body)}}}

def answer(decision='send', issues=None):
    return {'case_id':'T','decision':decision,'issues':issues or [],
            'request':{'method':'POST','url':'https://example.invalid/pet',
                       'headers':{'Content-Type':'application/json'},'body':VALID} if decision=='send' else None,
            'response':None}

class Fixed:
    def __init__(self, value):
        self.raw = value if isinstance(value,str) else json.dumps(value)
        self.calls=[]
    def chat(self,messages):
        self.calls.append([m.to_dict() for m in messages])
        return LLMResponse(self.raw,'fixed-offline',{},'stop')

@pytest.mark.parametrize('body,errors',[
    (VALID, []),
    ({'name':'dog','photoUrls':[]}, [('/body/status','required')]),
    ({'photoUrls':[],'status':'available'}, [('/body/name','required')]),
    (dict(VALID,status=None), [('/body/status','type')]),
    (dict(VALID,status=False), [('/body/status','type')]),
    (dict(VALID,status=0), [('/body/status','type')]),
    (dict(VALID,status=''), [('/body/status','enum')]),
    (dict(VALID,category={'id':False}), [('/body/category/id','type')]),
    (dict(VALID,category={'id':0}), []),
    (dict(VALID,id=2**63), [('/body/id','format')]),
    (dict(VALID,id=-(2**63)), []),
    (dict(VALID,id=1.0), []),
    (dict(VALID,tags=[{'id':'bad'}]), [('/body/tags/0/id','type')]),
    (dict(VALID,photoUrls=[False]), [('/body/photoUrls/0','type')]),
    (dict(VALID,extra={'free':True}), []),
    ({'photoUrls':[], 'category':{'id':'bad'}}, [('/body/category/id','type'),('/body/name','required'),('/body/status','required')]),
    ({}, [('/body/name','required'),('/body/photoUrls','required'),('/body/status','required')]),
    (None, [('/body','type')]),
])
def test_source_constraints(body,errors):
    got=request_body_issues(body,compile_request_body_policy(policy()))
    assert [(e['path'],e['code']) for e in got]==errors

def test_optional_object_and_own_required_and_additional_fields():
    d=json.loads(SOURCE.read_bytes());s=d['components']['schemas']['PetCreate']
    s['properties']['optional']={'type':'object','properties':{'x':{'type':'integer'}},'required':['x'],'additionalProperties':False}
    c=compile_request_body_policy(policy(d))
    assert request_body_issues(VALID,c)==[]
    assert request_body_issues(dict(VALID,optional={}),c)==[{'path':'/body/optional/x','code':'required'}]
    assert request_body_issues(dict(VALID,optional={'x':0,'y':1}),c)==[{'path':'/body/optional/y','code':'additional'}]

def test_request_body_presence():
    assert request_body_issues(None,compile_request_body_policy(policy()),body_present=False)==[{'path':'/body','code':'required'}]
    d=json.loads(SOURCE.read_bytes());d['paths']['/pet']['post']['requestBody']['required']=False
    assert request_body_issues(None,compile_request_body_policy(policy(d)),body_present=False)==[]

@pytest.mark.parametrize('kind',['hash','external','unsupported','format'])
def test_policy_fails_closed(kind):
    d=json.loads(SOURCE.read_bytes());p=policy()
    if kind=='hash':p['source_sha256']='0'*64
    else:
        s=d['components']['schemas']['PetCreate']
        if kind=='external':s['properties']['foreign']={'$ref':'https://example.invalid/schema'}
        elif kind=='unsupported':s['minProperties']=2
        else:s['properties']['name']['format']='made-up'
        p=policy(d)
    with pytest.raises((RequestBodyValidationError,ValueError)):compile_request_body_policy(p)

@pytest.mark.asyncio
async def test_native_fixed_error_red_green_same_wire_original_retained():
    payload=data({'name':'dog','photoUrls':[]});raw=answer()
    off,on=Fixed(raw),Fixed(raw)
    before=await SkillExecutor(llm_client=off,max_retries=0).execute_single(candidate(False),payload)
    after=await SkillExecutor(llm_client=on,max_retries=0).execute_single(candidate(),payload)
    assert before.output_data==raw and before.output_data['decision']=='send'
    assert after.status.value=='success' and after.output_data['decision']=='reject'
    assert after.output_data['issues']==[{'path':'/body/status','code':'required'}]
    assert after.execution_evidence['model_output']==raw
    assert after.execution_evidence['model_raw_text']==on.raw
    assert after.state_after['source_guard_result']==after.output_data
    assert off.calls==on.calls

@pytest.mark.asyncio
@pytest.mark.parametrize('raw',['not JSON','{}','{"case_id":"T","case_id":"X"}', answer() | {'request':{'method':'POST'}}])
async def test_invalid_model_output_is_failed_not_repaired(raw):
    client=Fixed(raw)
    r=await SkillExecutor(llm_client=client,max_retries=0).execute_single(candidate(),data({}),initial_state={'stable':1})
    assert r.status.value=='failed' and r.state_after=={'stable':1}
    assert r.execution_evidence['model_raw_text']==client.raw and len(client.calls)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('decision',['send','reject'])
async def test_valid_body_does_not_change_model_decision(decision):
    raw=answer(decision);r=await SkillExecutor(llm_client=Fixed(raw),max_retries=0).execute_single(candidate(),data())
    assert r.status.value=='success' and r.output_data==raw
    assert not r.execution_evidence['request_body_validation']['applied']

@pytest.mark.asyncio
async def test_response_mode_excluded_and_other_errors_preserved():
    raw={'case_id':'T','decision':'report','issues':[],'request':None,'response':{'matched_response':'200','body_schema_valid':True,'classification':'success','status':200}}
    r=await SkillExecutor(llm_client=Fixed(raw),max_retries=0).execute_single(candidate(),data({'name':'dog','photoUrls':[]},'response'))
    assert r.output_data==raw and r.execution_evidence['request_body_validation']['status']=='not_applicable'
    raw=answer('reject',[{'path':'/auth','code':'authentication'},{'path':'/body/fiction','code':'required'},{'path':'/bodyguard','code':'type'}])
    r=await SkillExecutor(llm_client=Fixed(raw),max_retries=0).execute_single(candidate(),data({}))
    paths={x['path'] for x in r.output_data['issues']}
    assert '/auth' in paths and '/bodyguard' in paths and '/body/fiction' not in paths

@pytest.mark.asyncio
async def test_configuration_preflight_zero_calls():
    s=candidate();s.implementation.request_body_validation['source_sha256']='0'*64
    c=Fixed(answer());r=await SkillExecutor(llm_client=c,max_retries=0).execute_single(s,data())
    assert r.status.value=='failed' and not c.calls

@pytest.mark.asyncio
async def test_plan_evidence_preserved():
    s=candidate()
    class Registry:
        async def get(self, sid):return s
    step=PlanStep(step_index=0,skill_id=s.skill_id,skill_name=s.name,input_mapping=data({}))
    plan=ExecutionPlan(task_id='guard-plan',steps=[step])
    result=await SkillExecutor(llm_client=Fixed(answer()),skill_registry=Registry(),max_retries=0).execute_plan(plan, {s.skill_id:s})
    assert step.result['decision']=='reject'
    assert step.execution_evidence['model_output']['decision']=='send'
    assert result["source_guard_result"]["decision"]=="reject"

def test_old_identity_and_explicit_policy_identity():
    s=candidate(False);h=canonical_skill_artifact_hash(s)
    raw=s.model_dump(mode='json');raw['implementation'].pop('request_body_validation')
    assert canonical_skill_artifact_hash(Skill.model_validate(raw))==h
    s.implementation.request_body_validation=policy()
    assert canonical_skill_artifact_hash(s)!=h

def test_http_execution_retains_evidence_and_final_state(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location("offline_runtime_fixture",Path(__file__).with_name("test_demo_runtime_defects.py"))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    client_for=fixture.client_for
    from skillwiki.models.skill_model import SkillState
    # Explicit legacy-released test fixture; not a publication experiment.
    s=candidate();s.state=SkillState.RELEASED
    client,app=client_for(tmp_path,s,json.dumps(answer()))
    response=client.post('/execution/skill',json={'skill_id':s.skill_id,'inputs':data({})})
    assert response.status_code==200
    got=response.json()['steps'][0]
    assert got['result']['decision']=='reject'
    assert got['execution_evidence']['model_output']['decision']=='send'

@pytest.mark.parametrize('backend',['memory','git'])
def test_native_versions_and_recovery_keep_policy_reset_proof(tmp_path,monkeypatch,backend):
    import sys,subprocess
    sys.path.insert(0,str(ROOT/'research/stage2-integrated-fix-20261102'))
    import integration as integ
    from skillwiki.layers.skill_governance import commit_skill_snapshot,skill_to_snapshot,diff_skill_snapshots
    w=integ.repo(tmp_path/'store' if backend=='git' else None)
    parent=integ.run(w.create(candidate(False)))
    impl=parent.implementation.model_dump(mode='json');impl['request_body_validation']=policy()
    child,receipt=integ.version(w,parent,impl)
    assert child.implementation.request_body_validation==policy()
    diffs=diff_skill_snapshots(skill_to_snapshot(parent),skill_to_snapshot(child))
    assert any(d.field=='implementation.request_body_validation' for d in diffs)
    repo=tmp_path/'governance';repo.mkdir()
    for args in [['init'],['config','--local','user.name','Offline regression'],['config','--local','user.email','offline@example.invalid'],['config','--local','commit.gpgsign','false']]:
        subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True)
    monkeypatch.setenv('SKILLOS_GOVERNANCE_REPO',str(repo))
    ref=commit_skill_snapshot(repo,child)
    response=integ.client(w).post(f'/api/v1/lifecycle/{child.skill_id}/rollback',json={'source_ref':ref,'source_version':child.version,'reason':'Offline policy persistence regression'})
    assert response.status_code==200,response.text
    restored=integ.run(integ.reload(w).get(response.json()['restored_skill_id']))
    assert restored.skill_id!=child.skill_id
    assert restored.implementation.request_body_validation==policy()
    assert not restored.evaluation.harness_validation and restored.metrics.usage_count==0
    assert integ.run(integ.reload(w).get(child.skill_id)).implementation==child.implementation

@pytest.mark.asyncio
async def test_plan_malformed_output_evidence_no_retry():
    s=candidate();c=Fixed('{}')
    step=PlanStep(step_index=0,skill_id=s.skill_id,input_mapping=data({}))
    plan=ExecutionPlan(task_id='bad-output-plan',steps=[step])
    result=await SkillExecutor(llm_client=c,max_retries=2).execute_plan(plan,{s.skill_id:s},initial_state={'before':True})
    assert len(c.calls)==1 and result=={'before':True}
    assert step.execution_evidence['model_raw_text']=='{}'

@pytest.mark.asyncio
async def test_prompt_real_missing_parameter_and_model_error_never_corrected():
    s=candidate();s.implementation.prompt_format='python_format';s.implementation.prompt_template='Task {missing}'
    c=Fixed(answer());r=await SkillExecutor(llm_client=c,max_retries=0).execute_single(s,data({}))
    assert r.status.value=='failed' and not c.calls
    class Broken:
        def chat(self,messages):raise RuntimeError('fixed network failure')
    r=await SkillExecutor(llm_client=Broken(),max_retries=0).execute_single(candidate(),data({}))
    assert r.status.value=='failed' and r.output_data is None

@pytest.mark.asyncio
async def test_candidate_harness_persists_original_and_final(tmp_path):
    from skillwiki.layers.skill_runtime.harness.local_skillwiki import LocalSkillWikiHarness
    from skillwiki.layers.skill_runtime.harness.workspace import HarnessWorkspace
    from skillwiki.layers.skill_runtime.harness.base import HarnessTestCase
    task=HarnessTestCase(test_id='body-required',name='Required body',goal='Reject incomplete body',input_data=data({}),
        verifier_specs=[{'type':'json_schema','path':'output','schema':{'type':'object','properties':{'decision':{'const':'reject'}},'required':['decision']}}])
    h=LocalSkillWikiHarness(executor=SkillExecutor(llm_client=Fixed(answer()),max_retries=0))
    result=await h.run_skill(candidate(),task,HarnessWorkspace('body-guard',tmp_path))
    assert result.verifier_passed
    stored=json.loads((Path(result.evidence_path)/'output.json').read_text())
    assert stored['output']['decision']=='reject'
    assert stored['record']['execution_evidence']['model_output']['decision']=='send'
