"""Real Windows Git regressions; all repositories are isolated evidence fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillwiki.layers.skill_governance import (
    GitVersionStore,
    GitVersionStoreError,
    commit_skill_snapshot,
    read_skill_snapshot_at_ref,
    restore_skill_snapshot,
    skill_snapshot_path,
)
from skillwiki.models import Skill, SkillImplementation, SkillState, SkillType
from skillwiki.storage.skill_repo.common import GitSkillStore

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows Git path regression')


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # The fixture uses an extended path for Python filesystem writes; Git's
    # process cwd must use its ordinary Windows spelling.
    cwd = str(repo)
    if cwd.startswith('\\\\?\\'):
        cwd = cwd[4:]
    return subprocess.run(
        ['git', *args], cwd=cwd, check=check, capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=15,
    )


@pytest.fixture
def isolated_repo() -> Path:
    evidence = Path(__file__).resolve().parents[2] / 'research' / 'stage2-runtime-repair-20261030' / 'evidence' / 'git'
    attempt = evidence / ('pytest-' + uuid.uuid4().hex[:12])
    prefix = str(attempt / '中文仓_')
    repo = Path(prefix + 'p' * max(1, 220 - len(prefix)))
    if os.name == 'nt' and not str(repo).startswith('\\\\?\\'):
        repo = Path('\\\\?\\' + str(repo))
    repo.mkdir(parents=True)
    return repo


def _skill(description: str = '原始中文说明') -> Skill:
    return Skill(
        skill_id='中文技能_' + 's' * 36,
        name='long_path_skill_' + 'n' * 36,
        description=description,
        skill_type=SkillType.FUNCTIONAL,
        state=SkillState.DRAFT,
        implementation=SkillImplementation(code="output['ok'] = True"),
    )


def _prepare_repo(repo: Path, *, existing: bool) -> str | None:
    _git(repo, '-c', 'core.longpaths=true', 'init')
    _git(repo, '-c', 'core.longpaths=true', 'config', '--local', 'commit.gpgsign', 'false')
    if not existing:
        return None
    (repo / 'existing.txt').write_text('existing repository must survive\n', encoding='utf-8')
    _git(repo, '-c', 'core.longpaths=true', 'add', '--', 'existing.txt')
    _git(repo, '-c', 'core.longpaths=true', '-c', 'user.name=Path Test',
         '-c', 'user.email=path@example.local', 'commit', '-m', 'existing history')
    old_head = _git(repo, '-c', 'core.longpaths=true', 'rev-parse', 'HEAD').stdout.strip()
    _git(repo, '-c', 'core.longpaths=true', 'config', '--local', 'core.longpaths', 'false')
    return old_head


@pytest.mark.parametrize('existing', [False, True], ids=['new', 'existing'])
def test_skill_store_persists_and_reopens_long_unicode_paths(isolated_repo: Path, existing: bool) -> None:
    old_head = _prepare_repo(isolated_repo, existing=True) if existing else None
    store = GitSkillStore(isolated_repo)
    skill = _skill()
    assert len(str(isolated_repo / 'skills' / skill.name / '1.0.0.json')) > 260
    store.add_skill(skill)
    reopened = GitSkillStore(isolated_repo)
    restored = reopened.get_skill(skill.name, '1.0.0')
    assert restored is not None
    assert restored.description == '原始中文说明'
    assert restored.implementation.code == "output['ok'] = True"
    assert _git(isolated_repo, 'config', '--local', '--bool', '--get', 'core.longpaths').stdout.strip() == 'true'
    if old_head:
        assert _git(isolated_repo, 'show', f'{old_head}:existing.txt').stdout == 'existing repository must survive\n'


@pytest.mark.parametrize('existing', [False, True], ids=['new', 'existing'])
def test_governance_snapshot_show_rollback_draft_and_reopen(isolated_repo: Path, existing: bool) -> None:
    old_head = _prepare_repo(isolated_repo, existing=existing)
    original = _skill()
    path = skill_snapshot_path(original)
    assert len(str(isolated_repo / path)) > 260
    original_commit = commit_skill_snapshot(isolated_repo, original)
    assert json.loads(_git(isolated_repo, 'show', f'{original_commit}:{path}').stdout)['description'] == '原始中文说明'
    changed = _skill('改动后的说明')
    changed_commit = commit_skill_snapshot(isolated_repo, changed)
    rollback = restore_skill_snapshot(isolated_repo, changed, original_commit)
    assert rollback.restore_commit not in (original_commit, changed_commit)
    assert read_skill_snapshot_at_ref(isolated_repo, 'HEAD', path)['description'] == '原始中文说明'
    draft = _skill('恢复后的新草稿')
    draft.version = '1.0.1'
    draft_commit = commit_skill_snapshot(isolated_repo, draft)
    reopened = GitVersionStore(isolated_repo)
    payload = json.loads(reopened.read_file_at_ref(draft_commit, skill_snapshot_path(draft)))
    assert payload['description'] == '恢复后的新草稿'
    assert payload['state'] == 'S2'
    assert payload['version'] == '1.0.1'
    assert _git(isolated_repo, 'config', '--local', '--bool', '--get', 'core.longpaths').stdout.strip() == 'true'
    if old_head:
        assert _git(isolated_repo, 'show', f'{old_head}:existing.txt').stdout == 'existing repository must survive\n'


@pytest.mark.parametrize('wrong_ref', [True, False], ids=['missing-ref', 'wrong-snapshot'])
def test_long_path_restore_rejects_missing_identity_without_writes(isolated_repo: Path, wrong_ref: bool) -> None:
    _prepare_repo(isolated_repo, existing=True)
    skill = _skill()
    commit = commit_skill_snapshot(isolated_repo, skill)
    before = (isolated_repo / skill_snapshot_path(skill)).read_bytes()
    if not wrong_ref:
        skill.skill_id = 'different-skill'
    with pytest.raises(GitVersionStoreError):
        restore_skill_snapshot(isolated_repo, skill, 'missing-ref' if wrong_ref else commit)
    assert GitVersionStore(isolated_repo).head_commit() == commit
    assert (isolated_repo / skill_snapshot_path(_skill())).read_bytes() == before
    assert not (isolated_repo / 'skills/different-skill/1.0.0.json').exists()


@pytest.mark.parametrize('backend', ['skill', 'governance'])
def test_local_config_write_failure_is_explicit(isolated_repo: Path, backend: str) -> None:
    _prepare_repo(isolated_repo, existing=True)
    (isolated_repo / '.git/config.lock').write_text('owned by another process\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='core.longpaths'):
        if backend == 'skill':
            GitSkillStore(isolated_repo).init_repo(initial_commit=False)
        else:
            GitVersionStore(isolated_repo).head_commit()
    assert _git(isolated_repo, '-c', 'core.longpaths=true', 'config', '--local', '--bool', '--get', 'core.longpaths').stdout.strip() == 'false'


@pytest.mark.parametrize('backend', ['skill', 'governance'])
@pytest.mark.parametrize('readback_code,readback_value', [(1, ''), (0, 'false\n')])
def test_local_config_unconfirmed_readback_fails_closed(
    isolated_repo: Path, backend: str, readback_code: int, readback_value: str, monkeypatch,
) -> None:
    _prepare_repo(isolated_repo, existing=True)
    real_run = subprocess.run

    def unconfirmed_readback(args, **kwargs):
        # Only inject the otherwise impractical write/read race. Repo creation,
        # Git config writes, and all other Git commands remain real.
        if args == ['git', 'config', '--local', '--bool', '--get', 'core.longpaths']:
            return subprocess.CompletedProcess(args, readback_code, readback_value, 'readback unavailable')
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', unconfirmed_readback)
    with pytest.raises(RuntimeError, match='core.longpaths'):
        if backend == 'skill':
            GitSkillStore(isolated_repo).init_repo(initial_commit=False)
        else:
            GitVersionStore(isolated_repo).head_commit()
