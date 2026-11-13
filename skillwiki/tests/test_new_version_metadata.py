import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes.lifecycle import router
from skillwiki.layers.skill_repository.repository import SkillWikiManager
from skillwiki.models import Skill, SkillImplementation, SkillProvenance, SkillState


@pytest.mark.parametrize('backend', ['memory', 'git'])
def test_new_version_review_metadata_is_saved_as_provenance(tmp_path, backend):
    wiki = MemoryWikiManager() if backend == 'memory' else SkillWikiManager(storage_dir=tmp_path)
    parent = asyncio.run(wiki.create(Skill(name='maintenance_metadata_demo', state=SkillState.DRAFT,
        implementation=SkillImplementation(code="output['ok'] = True"),
        provenance=SkillProvenance(source_type='manual', source_ids=['source-a'],
            creation_context={'retained_source': True}))))
    before = parent.model_dump(mode='json')
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(wiki=wiki, graph=None)
    metadata = {'maintenance_proposal_id': 'proposal-a', 'maintenance_review': {'head_commit': 'review-a'}}
    with TestClient(app) as client:
        response = client.post(f'/api/v1/lifecycle/{parent.skill_id}/new-version', json={'metadata': metadata})
    assert response.status_code == 200, response.text
    new_id = response.json()['skill_id']
    assert new_id != parent.skill_id
    if backend == 'git':
        wiki = SkillWikiManager(storage_dir=tmp_path)
    draft = asyncio.run(wiki.get(new_id))
    assert draft.state == SkillState.DRAFT
    assert draft.provenance.parent_skill_ids == [parent.skill_id]
    assert draft.provenance.source_ids == ['source-a']
    assert draft.provenance.creation_context['retained_source'] is True
    assert draft.provenance.creation_context['version_request_metadata'] == metadata
    assert not draft.evaluation.harness_validation.get('current_identity_proof')
    assert asyncio.run(wiki.get(parent.skill_id)).model_dump(mode='json') == before
