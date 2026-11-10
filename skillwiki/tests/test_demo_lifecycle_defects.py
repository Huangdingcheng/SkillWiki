"""D14-D16 offline regression cases with isolated real storage backends."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes.lifecycle import router
from skillwiki.layers.skill_governance.artifact_identity import canonical_skill_artifact_hash
from skillwiki.layers.skill_governance.reviewer import SkillReviewer
from skillwiki.layers.skill_repository.repository import SkillWikiManager
from skillwiki.models.skill_model import Skill, SkillEvaluation, SkillImplementation, SkillInterface, SkillMetrics, SkillProvenance, SkillState
from skillwiki.storage.skill_repo.common import GitSkillStore

def make_skill():
    return Skill(name='lifecycle_fixture', description='Echo an input under a fixed contract.', state=SkillState.RELEASED,
        interface=SkillInterface(input_schema={'type':'object'}, output_schema={'type':'object'}),
        implementation=SkillImplementation(code='output = dict(input_data)'),
        evaluation=SkillEvaluation(verifier_specs=[{'type':'json_exists','path':'value'}], test_case_refs=['case-a'],
            benchmark_task_ids=['task-a'], validation_summary='The old identity passed.',
            harness_validation={'promotion_gate':'passed','old':'evidence'}),
        provenance=SkillProvenance(source_type='doc', source_ids=['source-a']),
        metrics=SkillMetrics(usage_count=3, success_count=2, failure_count=1))

@pytest.fixture(params=['memory','git'])
def wiki(request, tmp_path):
    if request.param == 'memory': return MemoryWikiManager()
    store=GitSkillStore(tmp_path/'storage',auto_commit=False)
    store.init_repo(initial_commit=False)
    return SkillWikiManager(storage_dir=store.base_dir, auto_commit=False)

def run(awaitable): return asyncio.run(awaitable)
def reload(wiki):
    return SkillWikiManager(storage_dir=wiki.store.base_dir, auto_commit=False) if isinstance(wiki,SkillWikiManager) else wiki

def attach_proof(skill, **changes):
    proof={'schema_version':'skillwiki-current-identity-proof-v1','skill_id':skill.skill_id,'skill_version':skill.version,
        'artifact_hash':canonical_skill_artifact_hash(skill),'source':'verifier_loop','evidence_id':'loop-fresh',
        'trace_ids':['run-fresh'],'status':'passed'}
    proof.update(changes)
    skill.evaluation.harness_validation['current_identity_proof']=proof
    return skill.evaluation

def client_for(wiki,status='approved',score=9.5,broken=False):
    class FixedClient:
        def chat(self,messages):
            if broken: raise RuntimeError('fixed reviewer unavailable')
            return SimpleNamespace(content=json.dumps({'status':status,'overall_score':score,'comments':[
                {'field':'description','severity':'suggestion','message':'Keep the contract explicit.','suggestion':'Retain it.'}],
                'summary':'fixed offline recommendation'}))
    reviewer=SkillReviewer(FixedClient())
    app=FastAPI()
    app.include_router(router,prefix='/api/v1')
    state=SimpleNamespace(wiki=wiki,reviewer=reviewer,graph=None,auditor=SimpleNamespace(
        audit=lambda s: SimpleNamespace(passed=True,issues=[],warnings=[],recommendations=[])))
    app.dependency_overrides[get_app_state]=lambda:state
    return TestClient(app,raise_server_exceptions=False),reviewer

@pytest.mark.parametrize('override',[False,True])
def test_new_identity_clears_results_after_overrides_and_preserves_contract(wiki,override):
    source=run(wiki.create(make_skill()))
    original=source.model_dump(mode='json')
    overrides={'evaluation':source.evaluation.model_copy(deep=True),'metrics':source.metrics.model_copy(deep=True),
        'state':SkillState.RELEASED} if override else {}
    child=run(wiki.create_new_version(source.skill_id,**overrides))
    saved=run(reload(wiki).get(child.skill_id))
    assert saved.evaluation.harness_validation == {}
    assert saved.evaluation.validation_summary is None
    assert saved.metrics == SkillMetrics()
    assert saved.state == SkillState.DRAFT
    assert saved.evaluation.verifier_specs == source.evaluation.verifier_specs
    assert saved.evaluation.test_case_refs == ['case-a']
    assert saved.provenance.source_ids == ['source-a']
    assert source.skill_id in saved.provenance.parent_skill_ids
    assert run(wiki.get(source.skill_id)).model_dump(mode='json') == original

@pytest.mark.parametrize('entry',['transition','update','release'])
def test_new_identity_cannot_promote_without_current_proof(wiki,entry):
    source=run(wiki.create(make_skill()))
    child=run(wiki.create_new_version(source.skill_id))
    with pytest.raises(ValueError,match='fresh|current.*proof|verification'):
        if entry=='transition': run(wiki.transition_state(child.skill_id,SkillState.VERIFIED))
        elif entry=='update': run(wiki.update(child.skill_id,state=SkillState.RELEASED))
        else: run(wiki.release(child.skill_id))
    assert run(reload(wiki).get(child.skill_id)).state == SkillState.DRAFT

@pytest.mark.parametrize('mismatch',['skill_id','skill_version','artifact_hash','status'])
def test_old_or_failed_proof_cannot_promote_new_identity(wiki,mismatch):
    source=run(wiki.create(make_skill()))
    child=run(wiki.create_new_version(source.skill_id))
    run(wiki.update(child.skill_id,evaluation=attach_proof(child,**{mismatch:'old-or-failed'})))
    with pytest.raises(ValueError,match='fresh|current.*proof|verification'):
        run(wiki.transition_state(child.skill_id,SkillState.VERIFIED))

def test_current_proof_allows_explicit_transition_and_release_after_reload(wiki):
    source=run(wiki.create(make_skill()))
    child=run(wiki.create_new_version(source.skill_id))
    run(wiki.update(child.skill_id,evaluation=attach_proof(child)))
    wiki=reload(wiki)
    assert run(wiki.transition_state(child.skill_id,SkillState.VERIFIED)).state == SkillState.VERIFIED
    assert run(wiki.release(child.skill_id)).state == SkillState.RELEASED
    assert run(reload(wiki).get(source.skill_id)).state == SkillState.RELEASED

def test_ordinary_legacy_release_remains_compatible(wiki):
    source=make_skill();source.state=SkillState.DRAFT
    run(wiki.create(source));client,_=client_for(wiki)
    response=client.post(f'/api/v1/lifecycle/{source.skill_id}/release',json={})
    assert response.status_code==200,response.text
    assert run(reload(wiki).get(source.skill_id)).state==SkillState.RELEASED

def test_reviewer_recommendation_does_not_publish_in_memory(wiki):
    source=make_skill();source.state=SkillState.VERIFIED
    _,reviewer=client_for(wiki)
    returned,result=run(reviewer.review_and_release(source))
    assert result.is_approved
    assert returned.state==SkillState.VERIFIED
    assert source.state==SkillState.VERIFIED

def test_review_comments_serialize_without_nonexistent_score(wiki):
    source=run(wiki.create(make_skill()));client,_=client_for(wiki)
    response=client.post(f'/api/v1/lifecycle/{source.skill_id}/review')
    assert response.status_code==200,response.text
    assert response.json()['comments'][0]['suggestion']=='Retain it.'

def test_explicit_approved_review_route_persists_release_with_fresh_proof(wiki):
    source=run(wiki.create(make_skill()));child=run(wiki.create_new_version(source.skill_id))
    run(wiki.update(child.skill_id,evaluation=attach_proof(child)))
    client,_=client_for(wiki)
    response=client.post(f'/api/v1/lifecycle/{child.skill_id}/review-and-release')
    assert response.status_code==200,response.text
    saved=run(reload(wiki).get(child.skill_id))
    assert saved.state==SkillState.RELEASED
    assert saved.evaluation.harness_validation['review']['is_approved'] is True

@pytest.mark.parametrize('status,broken',[('rejected',False),('needs_revision',False),('approved',True)])
def test_nonapproval_or_reviewer_error_never_publishes(wiki,status,broken):
    source=run(wiki.create(make_skill()));child=run(wiki.create_new_version(source.skill_id))
    run(wiki.update(child.skill_id,evaluation=attach_proof(child)))
    client,_=client_for(wiki,status=status,broken=broken)
    response=client.post(f'/api/v1/lifecycle/{child.skill_id}/review-and-release')
    assert response.status_code in {400,409}
    assert run(reload(wiki).get(child.skill_id)).state==SkillState.DRAFT

def test_approval_without_verification_does_not_publish(wiki):
    source=make_skill();source.state=SkillState.VERIFIED
    run(wiki.create(source));client,_=client_for(wiki)
    response=client.post(f'/api/v1/lifecycle/{source.skill_id}/review-and-release')
    assert response.status_code in {400,409}
    assert run(reload(wiki).get(source.skill_id)).state==SkillState.VERIFIED


def test_recovery_api_keeps_live_version_and_requires_fresh_evidence(wiki, tmp_path, monkeypatch):
    import subprocess
    from skillwiki.layers.skill_governance import GitVersionStore, write_skill_snapshot
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('release_fixture', Path(__file__).with_name('test_b03_release_with_evidence.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    _evidence = fixture._evidence
    repo = tmp_path / 'governance'
    repo.mkdir()
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
    monkeypatch.setenv('SKILLOS_GOVERNANCE_REPO', str(repo))
    source = run(wiki.create(make_skill()))
    original = source.model_dump(mode='json')
    path = write_skill_snapshot(repo, source)
    commit = GitVersionStore(repo).commit_paths([path], 'offline original')
    client, _ = client_for(wiki)
    recovered = client.post(f'/api/v1/lifecycle/{source.skill_id}/rollback', json={'source_ref': commit})
    assert recovered.status_code == 200, recovered.text
    body = recovered.json()
    child = run(reload(wiki).get(body['restored_skill_id']))
    assert child.state == SkillState.DRAFT
    assert child.evaluation.harness_validation == {}
    assert child.evaluation.validation_summary is None
    assert child.metrics == SkillMetrics()
    assert child.provenance.creation_context['recovery_source_skill_id'] == source.skill_id
    assert run(reload(wiki).get(source.skill_id)).model_dump(mode='json') == original
    for endpoint, payload in [('transition', {'new_state':'S3'}), ('release', {}), ('release-tag', {})]:
        blocked = client.post(f'/api/v1/lifecycle/{child.skill_id}/{endpoint}', json=payload)
        assert blocked.status_code in {400, 409}, (endpoint, blocked.text)
    # Empty file_hashes remains permitted; the existing evidence gate decides.
    payload = _evidence(child)
    payload['manifest']['file_hashes'] = {}
    accepted = client.post(f'/api/v1/lifecycle/{child.skill_id}/release-with-evidence', json=payload)
    assert accepted.status_code == 200, accepted.text
    assert run(reload(wiki).get(child.skill_id)).state == SkillState.RELEASED
    assert run(reload(wiki).get(source.skill_id)).model_dump(mode='json') == original


def test_direct_durable_overwrite_cannot_bypass_gate(tmp_path):
    store = GitSkillStore(tmp_path / 'storage', auto_commit=False)
    source = store.add_skill(make_skill())
    child = store.create_new_version(source.name)
    child.state = SkillState.RELEASED
    with pytest.raises(ValueError, match='fresh|current.*proof|verification'):
        store.add_skill(child, overwrite=True)
    assert store.get_skill(child.name, child.version).state == SkillState.DRAFT


def test_merge_new_identity_does_not_inherit_validation(tmp_path):
    store = GitSkillStore(tmp_path / 'storage', auto_commit=False)
    source = store.add_skill(make_skill())
    second = source.model_copy(deep=True)
    second.skill_id = 'second-version'
    second.version = '1.1.0'
    store.add_skill(second)
    merged = store.merge_skills(source.name, source.version, second.version)
    assert merged.evaluation.harness_validation == {}
    assert merged.evaluation.validation_summary is None
    assert merged.metrics == SkillMetrics()
    with pytest.raises(ValueError, match='fresh|current.*proof|verification'):
        store.transition_skill_state(merged.name, merged.version, SkillState.VERIFIED)

