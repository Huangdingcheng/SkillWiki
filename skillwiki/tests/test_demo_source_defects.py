from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillwiki.api.routes import ingest
from skillwiki.layers.input_knowledge.pipeline import ExperiencePipeline
from skillwiki.layers.skill_management.auditor import SkillAuditorAgent
from skillwiki.layers.skill_runtime.executor import SkillExecutor
from skillwiki.layers.skill_repository.repository import SkillWikiManager
from skillwiki.models.skill_model import Skill, SkillImplementation
from skillwiki.utils.llm_client import LLMBudgetError, LLMResponse


class SourceClient:
    """Replace only external model transport; pipeline and API remain real."""
    def __init__(self, fail_stage=None, invalid_stage=None, offline=False, overrides=None):
        self._cfg = SimpleNamespace(api_key='dummy' if offline else 'fixture-network-disabled')
        self.calls = []
        self.fail_stage = fail_stage
        self.invalid_stage = invalid_stage
        self.overrides = overrides or {}

    def chat(self, messages):
        self.calls.append(messages)
        stage = len(self.calls)
        if stage == self.fail_stage:
            raise LLMBudgetError('controlled request capacity rejection')
        payloads = [
            {'actions': ['Inspect the provided source.'], 'proposed_skill_name': 'inspect_source', 'confidence': .8},
            {'normalized': [{'verb': 'inspect', 'object': 'source', 'condition': '', 'description': 'Inspect source.'}]},
            {'description': 'Inspect the source.', 'skill_type': 'atomic', 'tags': ['source'], 'confidence': .8},
            json.loads((Path(__file__).parent/'fixtures/source_contract_alignment/c02.json').read_text(encoding='utf-8')),
        ]
        content = '[]' if stage == self.invalid_stage else json.dumps(self.overrides.get(stage, payloads[stage - 1]))
        return LLMResponse(content, 'fixed-no-network', {'total_tokens': 7}, 'stop')


def client(wiki=None, graph=None, pipeline=None):
    app = FastAPI()
    app.include_router(ingest.router, prefix='/api/v1')
    state = SimpleNamespace(wiki=wiki, graph=graph, pipeline=pipeline, auditor=SkillAuditorAgent())
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app)


def candidate_payload():
    return {
        'source_type': 'document', 'unit_id': 'reviewed-source', 'raw_content': 'verified source',
        'name': 'reviewed_source', 'description': 'Return a source-grounded answer.',
        'input_schema': {'type': 'object', 'properties': {'task': {'type': 'string'}}, 'required': ['task']},
        'output_schema': {'type': 'object', 'properties': {'answer': {'type': 'string'}}},
        'prompt_template': 'Answer {task}', 'postconditions': ['output.answer exists'],
    }


def test_long_source_and_caller_metadata_survive_pipeline_and_parse():
    source = 'HEAD\n' + ('source text\n' * 800) + 'TAIL_REQUIRED_RULE'
    supplied = {'source_uri': 'fixture://long-source', 'version': 'v1', 'ctx2skill_evidence': {'evaluation_status': 'forged'}}
    original = deepcopy(supplied)
    transport = SourceClient()
    response = client(pipeline=ExperiencePipeline(transport)).post('/api/v1/ingest/parse', json={
        'source_type': 'document', 'content': source, 'metadata': supplied})
    assert response.status_code == 200
    data = response.json()
    assert data['success']
    assert source in transport.calls[0][1].content
    assert source in transport.calls[3][1].content
    unit = data['units'][0]
    assert unit['raw_content'] == source
    assert unit['metadata']['source']['content'] == source
    assert unit['metadata']['source']['sha256'] == hashlib.sha256(source.encode()).hexdigest()
    assert unit['metadata']['caller_metadata'] == original == supplied
    assert unit['metadata']['ctx2skill_evidence']['evaluation_status'] == 'not_executed'


@pytest.mark.parametrize('stage', [1, 2, 3, 4])
@pytest.mark.parametrize('failure', ['exception', 'invalid_response'])
def test_real_stage_failures_stop_without_successful_fallback(stage, failure):
    transport = SourceClient(fail_stage=stage if failure == 'exception' else None,
                             invalid_stage=stage if failure == 'invalid_response' else None)
    result = ExperiencePipeline(transport).process('Inspect this source.', 'document')
    assert not result.success
    assert result.unit_count == 0 and not result.units
    assert len(transport.calls) == stage
    assert result.errors and ['extractor', 'normalizer', 'summarizer', 'ctx2skill'][stage-1] in result.errors[0].lower()
    assert result.token_usage == 7 * (stage - (failure == 'exception'))


def test_explicit_offline_pipeline_is_labeled_and_never_calls_transport():
    transport = SourceClient(offline=True)
    result = ExperiencePipeline(transport).process('Read the document.', 'document')
    assert result.success and not transport.calls
    assert result.units[0].metadata['generation']['mode'] == 'explicit_offline'
    assert result.units[0].metadata['ctx2skill_evidence']['evaluation_status'] == 'not_executed'


def test_legacy_literal_braces_and_full_instructions_reach_runtime_unchanged():
    original = 'Return {"result": {"n": 1}}. Set: {x for x in rows}. Literal {name}.\n' + ('guidance\n' * 900) + 'TAIL_RULE'
    result = ExperiencePipeline(SourceClient(offline=True)).process(json.dumps({
        'name': 'literal_source', 'description': 'Read instructions.', 'instructions_markdown': original}), 'past_skills')
    unit = result.units[0]
    implementation = unit.metadata['candidate_implementation']
    capture = SimpleNamespace(calls=[])
    def respond(messages):
        capture.calls.append(messages)
        return LLMResponse('{"result":"ok"}', 'fixed-no-network', {'total_tokens': 1}, 'stop')
    capture.chat = respond
    skill = Skill(name=unit.proposed_skill_name, description=unit.proposed_description,
                  implementation=SkillImplementation(**implementation))
    record = asyncio.run(SkillExecutor(llm_client=capture).execute_single(skill, {
        'task': 'read', 'source_context': 'context', 'constraints': []}))
    assert record.status.value == 'success'
    assert original in capture.calls[0][1].content


@pytest.mark.parametrize('source_type', ['document', 'script'])
@pytest.mark.parametrize('backend', ['memory', 'git'])
def test_two_sources_create_distinct_candidates_with_full_provenance(source_type, backend, tmp_path):
    wiki = MemoryWikiManager() if backend == 'memory' else SkillWikiManager(storage_dir=tmp_path/'repo', auto_commit=False)
    api = client(wiki=wiki, pipeline=ExperiencePipeline(SourceClient(offline=True)))
    outputs = []
    for title in ['alpha', 'beta']:
        source = f'{title}: inspect this source.\n' + ('detail\n' * 100) + title
        response = api.post('/api/v1/ingest/parse-and-create', json={
            'source_type': source_type, 'content': source, 'metadata': {'source_uri': f'fixture://{title}'}})
        data = response.json()
        assert data['success'], data
        created = asyncio.run(wiki.get(data['created_skills'][0]['skill_id']))
        context = created.provenance.creation_context
        assert context['source']['content'] == source
        assert context['caller_metadata'] == {'source_uri': f'fixture://{title}'}
        outputs.append(created)
    assert outputs[0].name != outputs[1].name
    assert outputs[0].skill_id != outputs[1].skill_id
    assert all(s.version == '1.0.0' for s in outputs)
    assert asyncio.run(wiki.count()) == 2
    if backend == 'git':
        reloaded = SkillWikiManager(storage_dir=tmp_path/'repo', auto_commit=False)
        assert asyncio.run(reloaded.count()) == 2
        for expected in outputs:
            assert asyncio.run(reloaded.get(expected.skill_id)).model_dump() == expected.model_dump()


class InterruptibleGraph(MemoryGraphManager):
    def __init__(self):
        super().__init__()
        self.fail = True

    async def sync_skill(self, skill):
        if self.fail:
            raise RuntimeError('controlled graph unavailable')
        return await super().sync_skill(skill)


@pytest.mark.parametrize('route', ['create-candidate', 'parse-and-create'])
@pytest.mark.parametrize('backend', ['memory', 'git'])
def test_graph_failure_reports_persisted_identity_and_sync_retry_does_not_recreate(route, backend, tmp_path):
    wiki = MemoryWikiManager() if backend == 'memory' else SkillWikiManager(storage_dir=tmp_path/'repo', auto_commit=False)
    graph = InterruptibleGraph()
    transport = SourceClient(offline=True)
    api = client(wiki=wiki, graph=graph, pipeline=ExperiencePipeline(transport))
    payload = candidate_payload() if route == 'create-candidate' else {'source_type':'document', 'content':'Read the source.'}
    response = api.post('/api/v1/ingest/'+route, json=payload)
    assert response.status_code in (200, 201)
    data = response.json()
    assert not data['success']
    created = data['created_skill'] if route == 'create-candidate' else data['created_skills'][0]
    assert created['graph_sync_status'] == 'failed'
    skill_id = created['skill_id']
    assert asyncio.run(wiki.get(skill_id)) is not None
    if backend == 'git':
        wiki = SkillWikiManager(storage_dir=tmp_path/'repo', auto_commit=False)
        api = client(wiki=wiki, graph=graph)  # Retry after restart has no generation pipeline.
        assert asyncio.run(wiki.get(skill_id)) is not None
    still_failed = api.post(f'/api/v1/ingest/candidates/{skill_id}/sync-graph')
    assert not still_failed.json()['success']
    graph.fail = False
    for _ in range(2):
        retried = api.post(f'/api/v1/ingest/candidates/{skill_id}/sync-graph')
        assert retried.status_code == 200
        assert retried.json()['success']
        assert retried.json()['created_skill']['skill_id'] == skill_id
        assert retried.json()['created_skill']['graph_sync_status'] == 'synced'
    assert asyncio.run(wiki.count()) == 1
    assert not transport.calls
    assert f'skill:{skill_id}' in asyncio.run(graph.get_hetero_graph()).nodes


def test_sync_retry_unknown_identity_does_not_create():
    wiki = MemoryWikiManager()
    api = client(wiki=wiki, graph=MemoryGraphManager())
    response = api.post('/api/v1/ingest/candidates/missing/sync-graph')
    assert response.status_code == 404
    assert asyncio.run(wiki.count()) == 0


def test_reviewed_candidate_keeps_full_source_identity():
    payload = candidate_payload()
    payload['raw_content'] = 'start\n' + ('source\n' * 100) + 'end'
    skill = ingest._build_candidate_skill(ingest.CandidateSkillReviewRequest(**payload))
    assert skill.provenance.creation_context['source']['content'] == payload['raw_content']
    assert skill.provenance.creation_context['source']['sha256'] == hashlib.sha256(payload['raw_content'].encode()).hexdigest()


@pytest.mark.parametrize('stage,payload', [(1, {'actions': []}), (2, {'normalized': [{}]}),
    (3, {'description': 42}), (4, {'candidates': [{'score': .9}]})])
def test_structurally_invalid_stage_object_is_not_a_successful_candidate(stage, payload):
    transport = SourceClient(overrides={stage: payload})
    result = ExperiencePipeline(transport).process('Source with real requirements.', 'document')
    assert not result.success and not result.units
    assert len(transport.calls) == stage


def test_review_flow_parse_names_are_distinct_before_explicit_create():
    wiki = MemoryWikiManager()
    api = client(wiki=wiki, pipeline=ExperiencePipeline(SourceClient(offline=True)))
    names = []
    for source in ['Read alpha source.', 'Read beta source.']:
        unit = api.post('/api/v1/ingest/parse', json={'source_type': 'document', 'content': source}).json()['units'][0]
        payload = candidate_payload()
        payload.update(name=unit['proposed_skill_name'], raw_content=unit['raw_content'], unit_id=unit['unit_id'])
        response = api.post('/api/v1/ingest/create-candidate', json=payload)
        assert response.status_code == 201, response.json()
        names.append(response.json()['created_skill']['name'])
    assert names[0] != names[1] and asyncio.run(wiki.count()) == 2


@pytest.mark.parametrize('missing', ['context_pack', 'challenges'])
def test_real_ctx2skill_missing_section_is_not_filled_as_success(missing):
    payload = json.loads((Path(__file__).parent/'fixtures/source_contract_alignment/c02.json').read_text(encoding='utf-8'))
    payload.pop(missing)
    result = ExperiencePipeline(SourceClient(overrides={4: payload})).process('Real source.', 'document')
    assert not result.success and not result.units
    assert 'ctx2skill' in result.errors[0]
