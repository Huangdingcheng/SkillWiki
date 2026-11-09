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
