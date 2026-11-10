"""Release failures must not persist evidence; exercise real API and stores."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes.lifecycle import router
from skillwiki.layers.skill_repository.repository import SkillWikiManager
from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
from skillwiki.layers.skill_governance.artifact_identity import canonical_skill_artifact_hash
from skillwiki.layers.skill_governance.skill_snapshot import write_skill_snapshot
from skillwiki.layers.skill_governance.git_version_store import GitVersionStore
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.layers.skill_runtime.verifier import evaluate_verifier_specs
from skillwiki.models.skill_model import Skill, SkillState, SkillImplementation, SkillInterface, SkillProvenance
from skillwiki.models.experience_model import ExecutionStatus
from skillwiki.utils.llm_client import LLMResponse

def run(awaitable): return asyncio.run(awaitable)

class FixedClient:
    def chat(self, messages):
        return LLMResponse('{"result": "verified fixture"}', 'offline-fixed', {}, 'stop')

def make_skill():
    return Skill(skill_id='release-consistency', name='release_consistency',
        description='Return the supplied fixed fixture for deterministic release validation.',
        state=SkillState.DRAFT,
        implementation=SkillImplementation(prompt_template='Return the supplied result.', prompt_format='literal'),
        interface=SkillInterface(input_schema={'type':'object','properties':{'result':{'type':'string'}}},
           output_schema={'type':'object','properties':{'result':{'type':'string'}},'required':['result']},
           postconditions=['The result equals the expected fixture.']),
        provenance=SkillProvenance(source_type='manual'))

def evidence(skill, *, label='first'):
    record=run(SkillExecutor(llm_client=FixedClient(),max_retries=0).execute_single(skill,{'result':'verified fixture'}))
    assert record.status==ExecutionStatus.SUCCESS
    check=evaluate_verifier_specs([{'type':'json_equals','path':'result','expected':'verified fixture'}],record.output_data)
    assert check.passed, check
    evidence_dir=Path(os.environ.get('RELEASE_RECEIPTS') or tempfile.mkdtemp(prefix='release-evidence-'))/'executions'
    evidence_dir.mkdir(parents=True,exist_ok=True)
    evidence_path=evidence_dir/(record.record_id+'.json')
    evidence_path.write_text(record.model_dump_json(indent=2),encoding='utf-8')
    art=canonical_skill_artifact_hash(skill); env='offline-release/'+label
    return {'schema_version':'m1-evidence-release-v1','author':'release-consistency-test',
      'manifest':{'artifact_id':f'{skill.skill_id}@{skill.version}','skill_id':skill.skill_id,'version':skill.version,
       'content_hash':{'algorithm':'sha256','value':art},'source_snapshot':'fixed-fixture','contract_version':'release-fixture-v1',
       'license_refs':['project-test'],'environment_fingerprint':env},
      'traces':[{'run_id':record.record_id,'artifact_hash':art,'task_id':'echo-fixture','input_ref':'fixed/result','evidence_path':str(evidence_path),
        'environment_fingerprint':env,'verifier_version':'equals-v1','status':'passed',
        'evidence_spans':[{'actual':record.output_data,'passed':check.passed}]}],
      'evaluation':{'evaluation_id':'evaluation-'+label,'artifact_hash':art,'trace_ids':[record.record_id],
        'applicability':{'contract_version':'release-fixture-v1','environment_fingerprint':env,'verifier_version':'equals-v1'},
        'result':'pass','decision':'accept'}}

def make_client(wiki):
    app=FastAPI();app.include_router(router,prefix='/api/v1')
    app.dependency_overrides[get_app_state]=lambda:SimpleNamespace(wiki=wiki,auditor=SkillAuditorAgent(),graph=None,llm=None)
    return TestClient(app)

@pytest.fixture(params=['memory','git'])
def ctx(request,tmp_path):
    path=tmp_path/'store'
    wiki=MemoryWikiManager() if request.param=='memory' else SkillWikiManager(storage_dir=path,auto_commit=True)
    skill=run(wiki.create(make_skill()))
    return SimpleNamespace(wiki=wiki,client=make_client(wiki),skill=skill,backend=request.param,path=path,tmp=tmp_path,case=request.node.name)

def readback(ctx,skill_id):
    manager=ctx.wiki if ctx.backend=='memory' else SkillWikiManager(storage_dir=ctx.path,auto_commit=True)
    return run(manager.get(skill_id))

def snapshot(ctx,skill_id):
    result={'object':readback(ctx,skill_id).model_dump(mode='json')}
    if ctx.backend=='git':
        result['files']={str(p.relative_to(ctx.path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ctx.path.rglob('*') if p.is_file() and '.git' not in p.parts}
        result['head']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ctx.path,text=True).strip()
        result['events']=ctx.wiki.store.events_file.read_text(encoding='utf-8')
    return result

def post(ctx,skill,payload,tag='request'):
    before=snapshot(ctx,skill.skill_id)
    response=ctx.client.post(f'/api/v1/lifecycle/{skill.skill_id}/release-with-evidence',json=payload)
    after=snapshot(ctx,skill.skill_id)
    dest=os.environ.get('RELEASE_RECEIPTS')
    if dest:
        path=Path(dest)/ctx.case/tag;path.mkdir(parents=True,exist_ok=False)
        for name,data in [('request',payload),('response',{'http_status':response.status_code,'body':response.json()}),('before',before),('after',after)]:
            (path/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return response,before,after

@pytest.mark.parametrize('different',[False,True],ids=['same-proof','different-proof'])
def test_rejected_rerelease_never_replaces_proof_or_timestamps(ctx,different):
    payload=evidence(ctx.skill)
    response,_,_=post(ctx,ctx.skill,payload,'first')
    assert response.status_code==200,response.text
    current=readback(ctx,ctx.skill.skill_id)
    if different:payload=evidence(current,label='corrected-environment')
    response,before,after=post(ctx,current,payload,'repeat')
    assert response.status_code==400,response.text
    assert response.json()['detail']['released'] is False
    assert 'S4' in response.text
    assert after==before

@pytest.mark.parametrize('state',[SkillState.RAW_EXPERIENCE,SkillState.SKILL_CANDIDATE,SkillState.DEPRECATED,SkillState.ARCHIVED])
def test_all_illegal_origins_preserve_entire_store(ctx,state):
    # Model valid persisted historical state is fixture setup, not a publishing bypass.
    skill=run(ctx.wiki.update(ctx.skill.skill_id,state=state))
    response,before,after=post(ctx,skill,evidence(skill))
    assert response.status_code==400,response.text
    assert after==before

@pytest.mark.parametrize('fault',['identity','content','invalid-proof','missing-traces','audit'])
def test_precondition_failures_preserve_entire_store(ctx,fault):
    skill=ctx.skill
    if fault=='audit':
        # Executable and verifiable, but not publishable without provenance.
        skill=run(ctx.wiki.update(skill.skill_id,provenance=None))
    payload=evidence(skill)
    if fault=='identity':payload['manifest']['skill_id']='wrong'
    if fault=='content':payload['manifest']['content_hash']['value']='0'*64
    if fault=='invalid-proof':payload['traces'][0]['status']='failed';payload['traces'][0]['failure_class']='semantic'
    if fault=='missing-traces':payload['traces']=[]
    response,before,after=post(ctx,skill,payload)
    assert response.status_code in (400,409,422),response.text
    assert after==before

@pytest.mark.parametrize('state',[SkillState.DRAFT,SkillState.VERIFIED,SkillState.DEGRADED])
def test_supported_release_origins_keep_state_proof_time_and_audit(ctx,state):
    skill=ctx.skill
    if state!=SkillState.DRAFT:
        skill=run(ctx.wiki.transition_state(skill.skill_id,SkillState.VERIFIED))
    if state==SkillState.DEGRADED:
        response,_,_=post(ctx,skill,evidence(skill),'initial-release');assert response.status_code==200
        skill=run(ctx.wiki.transition_state(skill.skill_id,SkillState.DEGRADED))
    payload=evidence(skill,label='valid-'+state.value)
    response,before,after=post(ctx,skill,payload,'target-release')
    assert response.status_code==200,response.text
    saved=readback(ctx,skill.skill_id)
    assert saved.state==SkillState.RELEASED and saved.released_at is not None
    assert saved.evaluation.harness_validation['evidence_decision']==response.json()['evidence_reference']
    assert canonical_skill_artifact_hash(saved)==canonical_skill_artifact_hash(skill)
    assert saved.updated_at>datetime.fromisoformat(before['object']['updated_at'])
    assert saved.released_at>=datetime.fromisoformat(before['object']['updated_at'])
    if before['object']['released_at'] is not None:
        assert saved.released_at>datetime.fromisoformat(before['object']['released_at'])
    if ctx.backend=='git':
        events=[json.loads(line) for line in after['events'][len(before['events']):].splitlines()]
        assert events and events[-1]['action']=='transition' and events[-1]['to_state']=='S4'
        pairs=[(e['from_state'],e['to_state']) for e in events if e['action']=='transition']
        if len(events)==1 and 'transitions' in events[0]:
            pairs=[(e['from_state'],e['to_state']) for e in events[0]['transitions']]
        assert pairs==([('S2','S3'),('S3','S4')] if state==SkillState.DRAFT else [(state.value,'S4')])

def test_new_identity_rejects_parent_proof_then_releases_with_fresh_evidence(ctx):
    payload=evidence(ctx.skill);assert post(ctx,ctx.skill,payload,'parent')[0].status_code==200
    parent_before=snapshot(ctx,ctx.skill.skill_id)['object']
    child=run(ctx.wiki.create_new_version(ctx.skill.skill_id))
    assert child.evaluation.harness_validation=={}
    response,before,after=post(ctx,child,payload,'old-proof')
    assert response.status_code==409 and before==after
    response,_,_=post(ctx,child,evidence(child,label='child'),'fresh-proof')
    assert response.status_code==200,response.text
    assert snapshot(ctx,ctx.skill.skill_id)['object']==parent_before

def test_recovery_native_draft_requires_fresh_proof_and_preserves_online(ctx,monkeypatch):
    payload=evidence(ctx.skill);assert post(ctx,ctx.skill,payload,'parent')[0].status_code==200
    source=readback(ctx,ctx.skill.skill_id);before=source.model_dump(mode='json')
    repo=ctx.tmp/'snapshots';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True,capture_output=True)
    path=write_skill_snapshot(repo,source)
    commit=GitVersionStore(repo).commit_paths([path],'release fixture',author_name='release-test')
    draft=run(ctx.wiki.create_new_version(source.skill_id))
    monkeypatch.setenv('SKILLOS_GOVERNANCE_REPO',str(repo))
    result=ctx.client.post(f'/api/v1/lifecycle/{draft.skill_id}/rollback',json={'source_ref':commit,'source_version':source.version,'author':'release-test'})
    assert result.status_code==200,result.text
    recovered=readback(ctx,result.json()['restored_skill_id'])
    assert recovered.state==SkillState.DRAFT and recovered.evaluation.harness_validation=={}
    response,b,a=post(ctx,recovered,payload,'old-proof');assert response.status_code==409 and b==a
    assert ctx.client.post(f'/api/v1/lifecycle/{recovered.skill_id}/release',json={}).status_code==409
    assert post(ctx,recovered,evidence(recovered,label='recovery'),'fresh-proof')[0].status_code==200
    assert readback(ctx,source.skill_id).model_dump(mode='json')==before

def test_evidence_release_manager_rechecks_current_content_before_writing(ctx):
    # Obtain an actually gate-accepted proof, then make it stale in isolation.
    response,_,_=post(ctx,ctx.skill,evidence(ctx.skill),'verified-parent')
    assert response.status_code==200
    approved=readback(ctx,ctx.skill.skill_id).evaluation.model_copy(deep=True)
    run(ctx.wiki.transition_state(ctx.skill.skill_id,SkillState.DEGRADED))
    run(ctx.wiki.update(ctx.skill.skill_id,description='Changed artifact after evidence was produced; old proof must not apply.'))
    before=snapshot(ctx,ctx.skill.skill_id)
    with pytest.raises(ValueError,match='current identity proof'):
        run(ctx.wiki.release(ctx.skill.skill_id,evaluation=approved))
    assert snapshot(ctx,ctx.skill.skill_id)==before

def test_successful_preparation_isolates_both_nested_inputs(ctx):
    from skillwiki.layers.skill_governance.release_preparation import prepare_evidence_release
    assert post(ctx,ctx.skill,evidence(ctx.skill),'approved')[0].status_code==200
    run(ctx.wiki.transition_state(ctx.skill.skill_id,SkillState.DEGRADED))
    source=readback(ctx,ctx.skill.skill_id);evaluation=source.evaluation.model_copy(deep=True)
    before=source.model_dump(mode='json');evaluation_before=evaluation.model_dump(mode='json')
    candidate,transitions=prepare_evidence_release(source,evaluation)
    assert transitions==[{'from_state':'S5','to_state':'S4'}]
    candidate.evaluation.harness_validation['evidence_decision']['trace_ids'].append('changed-only-copy')
    candidate.interface.input_schema['properties']['added']={'type':'string'}
    assert source.model_dump(mode='json')==before
    assert evaluation.model_dump(mode='json')==evaluation_before
