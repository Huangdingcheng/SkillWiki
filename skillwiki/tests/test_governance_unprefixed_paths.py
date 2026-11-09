"""Public governance calls must accept ordinary Windows roots with deep children."""
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillwiki.layers.skill_governance import (
    GitVersionStore, GitVersionStoreError, commit_skill_snapshot,
    read_skill_snapshot_at_ref, restore_skill_snapshot, skill_snapshot_path,
    write_skill_snapshot,
)
from skillwiki.models import Skill, SkillImplementation
from skillwiki.utils.filesystem_paths import filesystem_path

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows filesystem boundary')

def git(repo, *args):
    result = subprocess.run(['git','-c','core.longpaths=true','-c','commit.gpgsign=false',
        '-c','user.name=Repair Test','-c','user.email=repair@example.local',*args],
        cwd=str(repo),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()

def ordinary_repo(length, existing):
    root = Path(__file__).resolve().parents[2]
    base = root / 'research/stage2-runtime-repair-20261030/evidence/git'
    prefix = str(base / ('ordinary-' + uuid.uuid4().hex[:12]) / 'repo-')
    repo = Path(prefix + 'p' * (length - len(prefix)))
    assert len(str(repo)) == length < 260 and not str(repo).startswith('\\?\\')
    filesystem_path(repo).mkdir(parents=True,exist_ok=False)
    git(repo,'init')
    if existing:
        filesystem_path(repo/'existing.txt').write_text('keep history',encoding='utf-8')
        git(repo,'add','--','existing.txt');git(repo,'commit','-m','old history')
    git(repo,'config','--local','core.longpaths','false')
    return repo

def skill():
    return Skill(skill_id='中文技能_'+'s'*36,name='ordinary_path_governance',
        description='original',implementation=SkillImplementation(code="output['ok']=True"))

@pytest.mark.parametrize('length',[220,240])
@pytest.mark.parametrize('existing',[False,True])
def test_ordinary_governance_snapshot_restore_dirty_guard_and_reopen(length,existing):
    before_global=subprocess.run(['git','config','--global','--get-all','core.longpaths'],capture_output=True).stdout
    repo=ordinary_repo(length,existing);original=skill();relative=skill_snapshot_path(original)
    assert len(str(repo/relative))>260
    first=commit_skill_snapshot(repo,original)
    changed=original.model_copy(deep=True);changed.description='changed'
    second=commit_skill_snapshot(repo,changed)
    assert second != first
    restored=restore_skill_snapshot(repo,changed,first)
    assert restored.restore_commit not in (first,second)
    assert read_skill_snapshot_at_ref(repo,'HEAD',relative)['description']=='original'
    assert json.loads(GitVersionStore(repo).read_file_at_ref('HEAD',relative))['description']=='original'
    assert not filesystem_path(repo/'.git/skillos-governance.lock').exists()
    target=filesystem_path(repo/relative)
    target.write_text('uncommitted bytes',encoding='utf-8')
    with pytest.raises(GitVersionStoreError,match='uncommitted'):
        write_skill_snapshot(repo,changed)
    assert target.read_text(encoding='utf-8')=='uncommitted bytes'
    assert git(repo,'config','--local','--get','core.longpaths')=='true'
    if existing:assert git(repo,'show','HEAD:existing.txt')=='keep history'
    assert subprocess.run(['git','config','--global','--get-all','core.longpaths'],capture_output=True).stdout==before_global

def test_ordinary_long_lock_refuses_collision_and_preserves_owner():
    repo=ordinary_repo(240,False)
    lock=filesystem_path(repo/'.git/skillos-governance.lock')
    lock.write_text('other owner',encoding='utf-8')
    with pytest.raises(GitVersionStoreError,match='locked'):
        with GitVersionStore(repo).lock():
            pytest.fail('colliding lock was entered')
    assert lock.read_text(encoding='utf-8')=='other owner'
