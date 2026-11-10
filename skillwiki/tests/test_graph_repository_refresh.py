from types import SimpleNamespace

import pytest

from skillwiki.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillwiki.api.routes.graph import get_full_graph, get_heterogeneous_graph
from skillwiki.models.skill_model import Skill, SkillProvenance, SkillState


class VersionedWiki(MemoryWikiManager):
    async def list_all_versions(self, **kwargs):
        return await super().list(**kwargs)

    async def list(self, **kwargs):
        rows = await super().list(**kwargs)
        latest = {}
        for row in rows:
            if row.name not in latest or row.version > latest[row.name].version:
                latest[row.name] = row
        return list(latest.values())


def app_for(wiki):
    return SimpleNamespace(wiki=wiki, graph=MemoryGraphManager(), execution_trace_path=None)


@pytest.mark.asyncio
async def test_skill_view_includes_real_parent_and_never_dangling_edges():
    wiki = VersionedWiki()
    old = Skill(name='versioned', description='old', state=SkillState.DRAFT)
    new = Skill(name='versioned', description='new', version='1.0.1',
                state=SkillState.DRAFT,
                provenance=SkillProvenance(source_type='doc', parent_skill_ids=[old.skill_id]))
    await wiki.create(old)
    await wiki.create(new)
    app = app_for(wiki)
    for item in (old, new):
        await app.graph.sync_skill(item)
        await app.graph.sync_auto_edges(item, [old.skill_id, new.skill_id])
    result = await get_full_graph(limit=200, app=app)
    ids = {node.id for node in result.nodes}
    assert {old.skill_id, new.skill_id} <= ids
    assert result.edges
    assert all(edge.source in ids and edge.target in ids for edge in result.edges)
    limited = await get_full_graph(limit=1, app=app)
    ids = {node.id for node in limited.nodes}
    assert len(ids) <= 1
    assert all(edge.source in ids and edge.target in ids for edge in limited.edges)


@pytest.mark.asyncio
async def test_refresh_reflects_create_state_delete_and_is_repeatable():
    wiki = MemoryWikiManager()
    app = app_for(wiki)
    skill = Skill(name='live', description='live', state=SkillState.SKILL_CANDIDATE)
    await wiki.create(skill)
    first = await get_heterogeneous_graph(app=app)
    assert any(node.id == f'skill:{skill.skill_id}' for node in first.nodes)
    skill.state = SkillState.DRAFT
    await wiki.update(skill.skill_id, state=SkillState.DRAFT)
    second = await get_heterogeneous_graph(app=app)
    node = next(node for node in second.nodes if node.id == f'skill:{skill.skill_id}')
    assert node.metadata['skill_state'] == 'S2'
    repeat = await get_heterogeneous_graph(app=app)
    assert second.model_dump() == repeat.model_dump()
    await wiki.delete(skill.skill_id)
    deleted = await get_heterogeneous_graph(app=app)
    assert not deleted.nodes and not deleted.edges


@pytest.mark.asyncio
async def test_startup_and_read_refresh_share_deletion_tracking():
    from skillwiki.api.main import _sync_graph_from_wiki
    wiki = MemoryWikiManager()
    skill = await wiki.create(Skill(name='startup', description='startup record'))
    app = app_for(wiki)
    await _sync_graph_from_wiki(wiki, app.graph)
    await wiki.delete(skill.skill_id)
    await get_heterogeneous_graph(app=app)
    assert (await app.graph.get_stats())['nodes'] == 0
