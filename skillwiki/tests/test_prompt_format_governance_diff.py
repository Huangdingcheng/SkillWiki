"""Execution-mode-only changes must reach native maintenance review and snapshots."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from skillwiki.api.deps import AppState, get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes.lifecycle import router
from skillwiki.layers.skill_governance import (
    GitVersionStore, canonical_skill_artifact_hash, commit_skill_snapshot,
    diff_skill_snapshots, skill_to_snapshot,
)
from skillwiki.models import Skill, SkillImplementation

@pytest.mark.parametrize('before,after', [(None,'literal'),(None,'python_format'),
    ('literal',None),('python_format',None),('literal','python_format'),('python_format','literal')])
def test_every_prompt_mode_change_is_an_implementation_diff(before,after):
    original=Skill(name='mode_diff',implementation=SkillImplementation(prompt_template='Echo {query}',prompt_format=before))
    changed=original.model_copy(deep=True);changed.implementation.prompt_format=after
    assert canonical_skill_artifact_hash(original)!=canonical_skill_artifact_hash(changed)
    diffs=diff_skill_snapshots(skill_to_snapshot(original),skill_to_snapshot(changed))
    assert len(diffs)==1 and diffs[0].field=='implementation.prompt_format'
    assert diffs[0].old_value==before and diffs[0].new_value==after
    assert diffs[0].change_category=='implementation_change'

def test_omitted_legacy_mode_and_explicit_none_remain_equal():
    current=skill_to_snapshot(Skill(name='legacy_mode',implementation=SkillImplementation(prompt_template='Echo {query}')))
    old=copy.deepcopy(current);old['implementation'].pop('prompt_format')
    assert diff_skill_snapshots(old,current)==[]

def test_native_mode_only_maintenance_proposal_commits_review_branch(tmp_path,monkeypatch):
    repo=tmp_path/'governance';repo.mkdir()
    subprocess.run(['git','init'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','config','--local','commit.gpgsign','false'],cwd=repo,check=True,capture_output=True)
    monkeypatch.setenv('SKILLOS_GOVERNANCE_REPO',str(repo))
    original=Skill(name='mode_change_review',implementation=SkillImplementation(prompt_template='Echo {query}',prompt_format='literal'))
    base=commit_skill_snapshot(repo,original)
    wiki=MemoryWikiManager();asyncio.run(wiki.create(original))
    state=AppState();state.initialize(wiki=wiki,llm=None,graph=None)
    app=FastAPI();app.include_router(router,prefix='/api/v1');app.dependency_overrides[get_app_state]=lambda:state
    client=TestClient(app,raise_server_exceptions=False)
    response=client.post(f'/api/v1/lifecycle/{original.skill_id}/propose-maintenance-change',json={
        'proposal_id':'mode-only-regression','patched_skill':{'implementation':{'prompt_format':'python_format'}},
        'reason':'Execution semantics changed without text changes'})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['review_status']!='no_changes'
    assert any(d['field']=='implementation.prompt_format' for d in result['structured_diff'])
    assert result['head_commit']!=base
    store=GitVersionStore(repo)
    assert store.branch_exists(result['branch_name'])
    snapshot=json.loads(store.read_file_at_ref(result['head_commit'],result['snapshot_path']))
    assert snapshot['implementation']['prompt_format']=='python_format'
    assert snapshot['implementation']['prompt_template']=='Echo {query}'
    assert asyncio.run(wiki.get(original.skill_id)).implementation.prompt_format=='literal'
