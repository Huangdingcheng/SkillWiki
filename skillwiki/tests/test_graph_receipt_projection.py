"""Offline, real native execution; no model or remote service calls."""
from types import SimpleNamespace
import json

import pytest

from skillwiki.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillwiki.api.routes.graph import get_heterogeneous_graph, get_skill_only_projection
from skillwiki.layers.skill_runtime import SkillExecutor
from skillwiki.layers.skill_runtime.harness import workspace
from skillwiki.layers.skill_runtime.harness.verifier_loop import VerificationLoop
from skillwiki.layers.skill_runtime.harness.base import HarnessTestCase
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillProvenance, SkillState


@pytest.mark.asyncio
async def test_real_pass_fail_unassessed_restart_and_no_descendant_inheritance(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path / 'harness')
    wiki = MemoryWikiManager()
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=None)
    skill = Skill(name='receipt', description='receipt test', state=SkillState.DRAFT,
                  implementation=SkillImplementation(language='python', code="output['ok'] = True"))
    await wiki.create(skill)
    loop = VerificationLoop(wiki=wiki, graph=app.graph,
                            executor=SkillExecutor(skill_registry=wiki, llm_client=None, max_retries=0))
    for name, specs in [('pass', [{'type': 'json_exists', 'path': 'output.ok'}]),
                        ('fail', [{'type': 'json_exists', 'path': 'output.missing'}]), ('none', [])]:
        result = await loop.run(skill.skill_id, max_attempts=1, allow_repair=False, promote_on_pass=False,
                                test_cases=[HarnessTestCase(test_id=name, name=name, goal=name, verifier_specs=specs)])
        assert len(result.attempts) == 1
    data = await get_heterogeneous_graph(app=app)
    verdicts = [node for node in data.nodes if node.kind == 'validation']
    assert {node.metadata['outcome'] for node in verdicts} == {'passed', 'failed', 'not_evaluated'}
    assert all(node.metadata['qualification_proof'] is False for node in verdicts)
    assert all(node.metadata['artifact_hash_verified'] is True for node in verdicts)
    ids = {node.id for node in data.nodes}
    assert all(edge.source in ids and edge.target in ids for edge in data.edges)
    rebuilt_app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=None)
    rebuilt = await get_heterogeneous_graph(app=rebuilt_app)
    assert [node.model_dump() for node in data.nodes] == [node.model_dump() for node in rebuilt.nodes]
    assert [edge.model_dump() for edge in data.edges] == [edge.model_dump() for edge in rebuilt.edges]
    descendant = Skill(name=skill.name, description='new', version='1.0.1', state=SkillState.DRAFT,
                       provenance=SkillProvenance(source_type='evolution', parent_skill_ids=[skill.skill_id]))
    await wiki.create(descendant)
    projection = await get_skill_only_projection(app=app)
    assert len(projection.validation_evidence[skill.skill_id]) == 3
    assert descendant.skill_id not in projection.validation_evidence
    # A content change under the same id/version must not borrow the prior pass.
    await wiki.update(skill.skill_id, implementation=SkillImplementation(language='python', code="output['ok'] = False"))
    changed = await get_heterogeneous_graph(app=app)
    assert all(node.metadata['outcome'] == 'stale' for node in changed.nodes if node.kind == 'validation')
    await wiki.delete(skill.skill_id)
    deleted = await get_heterogeneous_graph(app=app)
    assert not [node for node in deleted.nodes if node.kind in {'execution', 'validation'}]


@pytest.mark.asyncio
async def test_bad_receipt_is_reported_without_fabricating_success(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path)
    wiki = MemoryWikiManager()
    skill = Skill(name='bad_receipt', description='test')
    await wiki.create(skill)
    folder = tmp_path / 'loop'; folder.mkdir()
    (folder/'result.json').write_text(json.dumps({'loop_id': 'other', 'skill_id': skill.skill_id}))
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=None)
    data = await get_heterogeneous_graph(app=app)
    assert data.stats['evidence_sync']['issues']
    assert not [node for node in data.nodes if node.kind == 'validation']


@pytest.mark.asyncio
async def test_historical_unbound_receipt_stays_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path)
    wiki = MemoryWikiManager()
    skill = Skill(name='historical', description='historical')
    await wiki.create(skill)
    folder = tmp_path / 'old_loop'; folder.mkdir()
    receipt = {'loop_id': 'old_loop', 'skill_id': skill.skill_id, 'attempts': [{
        'run_id': 'old_run', 'skill_id': skill.skill_id, 'status': 'passed',
        'verifier_passed': True, 'output': {'record': {
            'skill_id': skill.skill_id, 'skill_version': skill.version, 'status': 'success'}}}]}
    (folder/'result.json').write_text(json.dumps(receipt))
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=None)
    data = await get_heterogeneous_graph(app=app)
    node = next(node for node in data.nodes if node.kind == 'validation')
    assert node.metadata['outcome'] == 'unknown'
    assert node.metadata['recorded_outcome'] == 'passed'
    assert node.metadata['artifact_binding'] == 'unavailable'


@pytest.mark.asyncio
async def test_import_audit_rebuild_does_not_become_task_pass_or_transfer_to_child():
    wiki = MemoryWikiManager()
    skill = Skill(name='imported', description='imported', state=SkillState.SKILL_CANDIDATE,
                  provenance=SkillProvenance(source_type='document', source_ids=['source-a']))
    skill.provenance.creation_context['candidate_import_receipt'] = {
        'skill_id': skill.skill_id, 'skill_version': skill.version,
        'audit_status': 'completed', 'audit': {'passed': True}}
    await wiki.create(skill)
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=None)
    first = await get_heterogeneous_graph(app=app)
    node = next(node for node in first.nodes if node.kind == 'validation')
    assert node.metadata['outcome'] == 'candidate'
    assert node.metadata['task_correctness_evaluated'] is False
    child = await wiki.create_new_version(skill.skill_id)
    app.graph = MemoryGraphManager()
    projection = await get_skill_only_projection(app=app)
    assert projection.validation_evidence[skill.skill_id][0]['outcome'] == 'candidate'
    assert child.skill_id not in projection.validation_evidence


@pytest.mark.asyncio
async def test_runtime_multistep_verdict_is_not_each_skill_pass(tmp_path, monkeypatch):
    from skillwiki.api.execution_history import save, history_directory
    from skillwiki.layers.skill_governance.artifact_identity import canonical_skill_artifact_hash
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path/'harness')
    wiki = MemoryWikiManager()
    first = await wiki.create(Skill(name='step_one', description='first'))
    second = await wiki.create(Skill(name='step_two', description='second'))
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=tmp_path/'traces/log.jsonl')
    steps = [{'skill_id':s.skill_id, 'status':'success', 'execution_evidence':{'artifact_identity':{
        'skill_id':s.skill_id, 'skill_version':s.version, 'artifact_hash':canonical_skill_artifact_hash(s)}}}
        for s in (first, second)]
    row = {'execution_id':'real-plan-format', 'created_at':'2026-11-09T00:00:00',
        'steps':steps, 'verifier_passed':True, 'experience_unit':{'metadata':{
            'retrieved_skill_versions':{s.skill_id:s.version for s in (first,second)}}}}
    save(history_directory(app),row)
    data = await get_heterogeneous_graph(app=app)
    verdicts = [node.metadata for node in data.nodes if node.kind=='validation']
    assert len(verdicts)==2
    assert all(v['outcome']=='not_evaluated' and v['task_verifier_passed'] is True for v in verdicts)
    assert all(v['validation_scope']=='task_only' for v in verdicts)


@pytest.mark.asyncio
async def test_repair_loop_projects_runs_for_distinct_child_identity(tmp_path, monkeypatch):
    from skillwiki.layers.skill_governance.artifact_identity import canonical_skill_artifact_hash
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path)
    wiki = MemoryWikiManager()
    old = await wiki.create(Skill(name='repair_chain', description='old'))
    child = await wiki.create_new_version(old.skill_id)
    folder=tmp_path/'repair_loop';folder.mkdir()
    rows=[]
    for s,passed in [(old,False),(child,True)]:
        rows.append({'run_id':s.skill_id,'skill_id':s.skill_id,'status':'passed' if passed else 'failed',
                     'verifier_passed':passed,'output':{'artifact_hash':canonical_skill_artifact_hash(s),
                     'record':{'skill_id':s.skill_id,'skill_version':s.version,'status':'success'}}})
    (folder/'result.json').write_text(json.dumps({'loop_id':'repair_loop','skill_id':old.skill_id,'attempts':rows}))
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=None)
    result = await get_skill_only_projection(app=app)
    assert result.validation_evidence[old.skill_id][0]['outcome']=='failed'
    assert result.validation_evidence[child.skill_id][0]['outcome']=='passed'


def _runtime_step(skill, step_id):
    from skillwiki.layers.skill_governance.artifact_identity import canonical_skill_artifact_hash
    return {'step_id': step_id, 'skill_id': skill.skill_id, 'status': 'success',
            'execution_evidence': {'artifact_identity': {
                'skill_id': skill.skill_id, 'skill_version': skill.version,
                'artifact_hash': canonical_skill_artifact_hash(skill)}}}


def _runtime_row(ident, skills, steps, summary=None):
    return {'execution_id': ident, 'created_at': '2026-11-09T00:00:00',
            'steps': steps, 'verifier_passed': True, 'verifier_summary': summary,
            'experience_unit': {'metadata': {
                'retrieved_skill_versions': {s.skill_id: s.version for s in skills}}}}


@pytest.mark.asyncio
async def test_runtime_projects_persisted_explicit_step_verdicts(tmp_path, monkeypatch):
    from skillwiki.api.execution_history import history_directory, load
    from skillwiki.api.routes.execution import (
        _build_execution_experience_unit, _store_execution_history, _verify_plan_steps)
    from skillwiki.api.schemas import ExecutionStepResult
    from skillwiki.models.skill_model import SkillEvaluation
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path/'harness')
    wiki = MemoryWikiManager()
    skills = [await wiki.create(Skill(name=name, description=name, evaluation=SkillEvaluation(
        verifier_specs=[{'type': 'json_exists', 'path': path}])))
        for name, path in [('explicit_pass', 'output.ok'), ('explicit_fail', 'output.missing')]]
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), verifier=None,
                          execution_trace_path=tmp_path/'traces/log.jsonl')
    steps = [ExecutionStepResult(**_runtime_step(s, f'step-{i}'), step_index=i, skill_name=s.name,
                                 outputs={'ok': True}, result={'ok': True})
             for i, s in enumerate(skills)]
    summary = _verify_plan_steps('check steps', steps, {s.skill_id: s for s in skills}, {}, app)
    unit = _build_execution_experience_unit(
        execution_id='explicit-verdicts', goal='check steps', status='failed', steps=steps,
        final_state={}, retrieved_skills=[s.skill_id for s in skills],
        retrieved_skill_versions={s.skill_id: s.version for s in skills},
        verifier_passed=summary['passed'], verifier_summary=summary, total_latency_ms=0)
    _store_execution_history(app=app, execution_id='explicit-verdicts', goal='check steps',
        status='failed', steps=steps, success_count=2, total_latency_ms=0,
        retrieved_skill_count=2, experience_unit=unit)
    assert [r['passed'] for r in load(history_directory(app))[0]['verifier_summary']['results']] == [True, False]
    data = await get_heterogeneous_graph(app=app)
    verdicts = {n.metadata['skill_id']: n.metadata for n in data.nodes if n.kind == 'validation'}
    assert [verdicts[s.skill_id]['outcome'] for s in skills] == ['passed', 'failed']
    assert all(v['validation_scope'] == 'step' and v['qualification_proof'] is False for v in verdicts.values())
    await wiki.update(skills[0].skill_id, description='changed artifact')
    child = await wiki.create_new_version(skills[1].skill_id)
    projection = await get_skill_only_projection(app=app)
    assert projection.validation_evidence[skills[0].skill_id][0]['outcome'] == 'stale'
    assert child.skill_id not in projection.validation_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize('ambiguity', ['duplicate_result', 'wrong_skill', 'wrong_step', 'duplicate_step', 'non_boolean'])
@pytest.mark.parametrize('single_step', [False, True])
async def test_runtime_rejects_ambiguous_step_verdicts(tmp_path, monkeypatch, ambiguity, single_step):
    from skillwiki.api.execution_history import save, history_directory
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path/'harness')
    wiki = MemoryWikiManager()
    skill = await wiki.create(Skill(name='ambiguous', description='ambiguous'))
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=tmp_path/'traces/log.jsonl')
    step = _runtime_step(skill, 'step-a')
    steps = [step, _runtime_step(skill, 'step-b')]
    result = {'step_id': 'step-a', 'skill_id': skill.skill_id, 'passed': True}
    results = [result]
    if ambiguity == 'duplicate_result':
        results.append(dict(result))
    elif ambiguity == 'wrong_skill':
        result['skill_id'] = 'another-skill'
    elif ambiguity == 'wrong_step':
        result['step_id'] = 'another-step'
    elif ambiguity == 'duplicate_step':
        steps[1] = dict(step)
    else:
        result['passed'] = 'true'
    if single_step and ambiguity != 'duplicate_step':
        steps = steps[:1]
    save(history_directory(app), _runtime_row('ambiguous', [skill], steps, {'results': results}))
    data = await get_heterogeneous_graph(app=app)
    verdicts = [n.metadata for n in data.nodes if n.kind == 'validation']
    assert len(verdicts) == len(steps)
    expected_scope = 'single_step' if len(steps) == 1 else 'task_only'
    assert all(v['outcome'] == 'not_evaluated' and v['validation_scope'] == expected_scope for v in verdicts)


@pytest.mark.asyncio
async def test_runtime_step_verdicts_do_not_mix_repeated_skill_steps(tmp_path, monkeypatch):
    from skillwiki.api.execution_history import save, history_directory
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path/'harness')
    wiki = MemoryWikiManager()
    skill = await wiki.create(Skill(name='repeated', description='repeated'))
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=tmp_path/'traces/log.jsonl')
    steps = [_runtime_step(skill, step_id) for step_id in ('first', 'second')]
    results = [{'step_id': step_id, 'skill_id': skill.skill_id, 'passed': passed}
               for step_id, passed in [('second', False), ('first', True)]]
    save(history_directory(app), _runtime_row('repeated', [skill], steps, {'results': results}))
    data = await get_heterogeneous_graph(app=app)
    verdicts = {n.metadata['step_index']: n.metadata['outcome'] for n in data.nodes if n.kind == 'validation'}
    assert verdicts == {0: 'passed', 1: 'failed'}


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['skill_id', 'status', 'step_shape', 'row_shape', 'json', 'filename'])
async def test_invalid_history_preserves_valid_events(tmp_path, monkeypatch, invalid):
    from skillwiki.api.execution_history import save, history_directory
    monkeypatch.setattr(workspace, 'default_harness_root', lambda: tmp_path/'harness')
    wiki = MemoryWikiManager()
    skill = await wiki.create(Skill(name='valid_survivor', description='valid survivor'))
    app = SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=tmp_path/'traces/log.jsonl')
    directory = history_directory(app)
    save(directory, _runtime_row('valid', [skill], [_runtime_step(skill, 'valid-step')]))
    bad_step = _runtime_step(skill, 'bad-step')
    bad_row = _runtime_row('bad', [skill], [bad_step, _runtime_step(skill, 'survivor-step')])
    if invalid == 'skill_id':
        bad_step['skill_id'] = []
    elif invalid == 'status':
        bad_step['status'] = None
    elif invalid == 'step_shape':
        bad_row['steps'][0] = 42
    elif invalid == 'row_shape':
        bad_row['experience_unit'] = None
    if invalid == 'json':
        (directory/'broken.json').write_text('{', encoding='utf-8')
    elif invalid == 'filename':
        (directory/'wrong.json').write_text(json.dumps(bad_row), encoding='utf-8')
    else:
        save(directory, bad_row)
    data = await get_heterogeneous_graph(app=app)
    verdicts = [n.metadata for n in data.nodes if n.kind == 'validation']
    expected = 2 if invalid in {'skill_id', 'status', 'step_shape'} else 1
    assert len(verdicts) == expected
    assert any(v['execution_id'] == 'valid' and v['outcome'] == 'passed' for v in verdicts)
    assert data.stats['evidence_sync']['issues']
