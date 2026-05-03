from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.deps import app_state
from skillos.api.memory_store import MemoryGraphManager, MemorySearchEngine, MemoryWikiManager
from skillos.api.routes import graph, skills
from skillos.layers.skill_repository.indexing import SearchQuery
from skillos.models.graph_model import SkillEdge
from skillos.models.skill_model import EdgeType, Skill, SkillImplementation, SkillState, SkillType


def make_skill(
    name: str,
    *,
    state: SkillState = SkillState.RELEASED,
    skill_type: SkillType = SkillType.ATOMIC,
    tags: list[str] | None = None,
    domain: str = "general",
    version: str = "1.0.0",
) -> Skill:
    return Skill(
        name=name,
        description=f"{name} handles browser automation",
        state=state,
        skill_type=skill_type,
        tags=tags or [],
        domain=domain,
        version=version,
        implementation=SkillImplementation(
            language="python",
            prompt_template=f"Run {name}",
        ),
    )


@pytest.fixture
def wiki() -> MemoryWikiManager:
    return MemoryWikiManager()


@pytest.fixture
def graph_mgr() -> MemoryGraphManager:
    return MemoryGraphManager()


@pytest.fixture
def api_app(wiki: MemoryWikiManager, graph_mgr: MemoryGraphManager) -> FastAPI:
    app_state.wiki = wiki
    app_state.graph = graph_mgr
    app_state.search = MemorySearchEngine(wiki)
    app = FastAPI()
    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(graph.router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
async def test_memory_wiki_crud_and_duplicate_guard(wiki: MemoryWikiManager):
    skill = make_skill("click_button", tags=["web"], domain="browser")

    created = await wiki.create(skill)
    assert await wiki.get(created.skill_id) == created
    assert await wiki.get_by_name("click_button", "1.0.0") == created

    with pytest.raises(ValueError, match="already exists"):
        await wiki.create(skill)

    updated = await wiki.update(created.skill_id, description="Updated description")
    assert updated is not None
    assert updated.description == "Updated description"
    assert updated.skill_id == created.skill_id
    assert updated.created_at == created.created_at
    assert updated.updated_at >= created.updated_at

    assert await wiki.delete(created.skill_id) is True
    assert await wiki.get(created.skill_id) is None


@pytest.mark.asyncio
async def test_memory_wiki_list_filters_versions_and_metrics(wiki: MemoryWikiManager):
    web = await wiki.create(make_skill("web_click", tags=["web", "ui"], domain="browser"))
    api = await wiki.create(make_skill("api_call", tags=["api"], domain="backend"))
    old = await wiki.create(make_skill("web_click", version="1.1.0", tags=["web"]))

    assert [skill.name for skill in await wiki.list(tags=["web"])] == ["web_click", "web_click"]
    assert [skill.name for skill in await wiki.list(domain="backend")] == ["api_call"]
    assert [skill.name for skill in await wiki.list(skill_type=SkillType.ATOMIC)] == [
        "web_click",
        "api_call",
        "web_click",
    ]
    assert len(await wiki.list(limit=1, offset=1)) == 1

    history = await wiki.get_version_history("web_click")
    assert [skill.version for skill in history] == ["1.0.0", "1.1.0"]

    await wiki.record_execution(web.skill_id, success=True, latency_ms=120)
    await wiki.record_execution(web.skill_id, success=False, latency_ms=240)
    refreshed = await wiki.get(web.skill_id)
    assert refreshed.metrics.usage_count == 2
    assert refreshed.metrics.success_rate == pytest.approx(0.5)

    stats = await wiki.get_overview_stats()
    assert stats["total_skills"] == 3
    assert stats["by_type"][SkillType.ATOMIC.value] == 3
    assert api.created_at <= datetime.utcnow()
    assert old.created_at <= datetime.utcnow()


@pytest.mark.asyncio
async def test_memory_search_respects_filters_and_scores(wiki: MemoryWikiManager):
    released = await wiki.create(
        make_skill("search_web_page", tags=["web", "search"], domain="browser")
    )
    deprecated = await wiki.create(
        make_skill("old_search_tool", state=SkillState.DEPRECATED, tags=["search"], domain="browser")
    )
    backend = await wiki.create(
        make_skill("database_query", tags=["database"], domain="backend")
    )
    for _ in range(5):
        released.record_execution(success=True, latency_ms=50)
    for _ in range(5):
        backend.record_execution(success=False, latency_ms=50)

    engine = MemorySearchEngine(wiki)
    results = await engine.search(SearchQuery(
        text="search web",
        tags=["search"],
        domain="browser",
        min_success_rate=0.8,
        max_results=5,
    ))
    assert [result.skill.name for result in results] == ["search_web_page"]
    assert 0 <= results[0].score <= 1
    assert results[0].match_reasons

    hidden = await engine.search(SearchQuery(text="old", max_results=5))
    assert all(result.skill.skill_id != deprecated.skill_id for result in hidden)

    included = await engine.search(SearchQuery(
        text="old",
        include_deprecated=True,
        max_results=5,
    ))
    assert any(result.skill.skill_id == deprecated.skill_id for result in included)


@pytest.mark.asyncio
async def test_memory_graph_edges_subgraph_stats_and_order(
    wiki: MemoryWikiManager,
    graph_mgr: MemoryGraphManager,
):
    parent = await wiki.create(make_skill("fill_form", skill_type=SkillType.FUNCTIONAL))
    child = await wiki.create(make_skill("click_button"))
    dependency = await wiki.create(make_skill("locate_element"))
    for skill in (parent, child, dependency):
        await graph_mgr.sync_skill(skill)

    await graph_mgr.create_edge(SkillEdge(
        source_id=parent.skill_id,
        target_id=child.skill_id,
        edge_type=EdgeType.COMPOSES_WITH,
        weight=0.8,
    ))
    await graph_mgr.add_dependency(child.skill_id, dependency.skill_id, weight=0.9)

    subgraph = await graph_mgr.get_subgraph([parent.skill_id], depth=2)
    assert {edge.edge_type for edge in subgraph.edges} == {
        EdgeType.COMPOSES_WITH,
        EdgeType.DEPENDS_ON,
    }

    stats = await graph_mgr.get_stats()
    assert stats["nodes"] == 3
    assert stats["edges"] == 2

    order = await graph_mgr.get_execution_order([parent.skill_id, child.skill_id, dependency.skill_id])
    assert order.index(dependency.skill_id) < order.index(child.skill_id)

    chain = await graph_mgr.get_dependency_chain(child.skill_id)
    assert chain == [dependency.skill_id]


def test_skill_and_graph_api_smoke(
    api_app: FastAPI,
    wiki: MemoryWikiManager,
    graph_mgr: MemoryGraphManager,
):
    first = make_skill("api_click", tags=["web"], domain="browser")
    second = make_skill("api_locate", tags=["web"], domain="browser")
    import anyio

    anyio.run(wiki.create, first)
    anyio.run(wiki.create, second)
    anyio.run(graph_mgr.sync_skill, first)
    anyio.run(graph_mgr.sync_skill, second)

    client = TestClient(api_app)

    listed = client.get("/api/v1/skills").json()
    assert len(listed) == 2

    search_resp = client.post("/api/v1/skills/search", json={"query": "click", "limit": 5})
    assert search_resp.status_code == 200
    assert search_resp.json()[0]["name"] == "api_click"

    patch_resp = client.patch(
        f"/api/v1/skills/{first.skill_id}",
        json={"description": "Patched from API"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["description"] == "Patched from API"

    edge_resp = client.post(
        "/api/v1/graph/edges",
        json={
            "source_id": first.skill_id,
            "target_id": second.skill_id,
            "edge_type": "depends_on",
            "weight": 0.7,
        },
    )
    assert edge_resp.status_code == 200

    graph_resp = client.get("/api/v1/graph")
    assert graph_resp.status_code == 200
    assert len(graph_resp.json()["edges"]) == 1

    subgraph_resp = client.post(
        "/api/v1/graph/subgraph",
        json={"skill_id": first.skill_id, "depth": 2},
    )
    assert subgraph_resp.status_code == 200
    assert {node["id"] for node in subgraph_resp.json()["nodes"]} == {
        first.skill_id,
        second.skill_id,
    }

    delete_resp = client.delete(f"/api/v1/skills/{second.skill_id}")
    assert delete_resp.status_code == 200
