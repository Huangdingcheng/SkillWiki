from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.deps import app_state
from skillos.api.memory_store import MemoryGraphManager, MemorySearchEngine, MemoryWikiManager
from skillos.api.routes import skills
from skillos.layers.skill_repository.indexing import SearchQuery, score_skill_match
from skillos.models.skill_model import Skill, SkillImplementation, SkillState, SkillType


def make_skill(
    name: str,
    *,
    description: str = "",
    tags: list[str] | None = None,
    domain: str = "general",
    state: SkillState = SkillState.RELEASED,
    version: str = "1.0.0",
) -> Skill:
    return Skill(
        name=name,
        description=description or f"{name} utility",
        tags=tags or [],
        domain=domain,
        state=state,
        skill_type=SkillType.ATOMIC,
        version=version,
        implementation=SkillImplementation(prompt_template=f"Run {name}"),
    )


async def add_skill(wiki: MemoryWikiManager, skill: Skill, successes: int = 0, failures: int = 0) -> Skill:
    for _ in range(successes):
        skill.record_execution(success=True, latency_ms=50)
    for _ in range(failures):
        skill.record_execution(success=False, latency_ms=80)
    return await wiki.create(skill)


@pytest.fixture
def wiki() -> MemoryWikiManager:
    return MemoryWikiManager()


@pytest.mark.asyncio
async def test_fill_form_matches_snake_case_name_before_description_only(wiki: MemoryWikiManager):
    direct = await add_skill(
        wiki,
        make_skill("fill_form", description="Submit structured browser forms", tags=["form"]),
    )
    description_only = await add_skill(
        wiki,
        make_skill("submit_record", description="This can fill form data after validation", tags=["data"]),
    )

    engine = MemorySearchEngine(wiki)
    results = await engine.search(SearchQuery(text="fill form", max_results=5))

    assert results[0].skill.skill_id == direct.skill_id
    assert results[0].score > next(
        result.score for result in results if result.skill.skill_id == description_only.skill_id
    )
    assert "exact name match" in results[0].match_reasons


@pytest.mark.asyncio
async def test_exact_name_scores_above_partial_token_match(wiki: MemoryWikiManager):
    exact = await add_skill(wiki, make_skill("click_button", description="Click a browser button"))
    partial = await add_skill(wiki, make_skill("button_detector", description="Find buttons"))

    query = SearchQuery(text="click button", max_results=5)
    exact_score = score_skill_match(exact, query)
    partial_score = score_skill_match(partial, query)

    assert exact_score.score > partial_score.score
    assert "exact name match" in exact_score.match_reasons
    assert "name token match" in partial_score.match_reasons


@pytest.mark.asyncio
async def test_filters_for_tags_domain_success_rate_and_deprecated(wiki: MemoryWikiManager):
    strong = await add_skill(
        wiki,
        make_skill("search_web_page", tags=["search", "web"], domain="browser"),
        successes=9,
        failures=1,
    )
    await add_skill(
        wiki,
        make_skill("search_database", tags=["search"], domain="backend"),
        successes=10,
    )
    await add_skill(
        wiki,
        make_skill("weak_web_search", tags=["search", "web"], domain="browser"),
        successes=1,
        failures=9,
    )
    deprecated = await add_skill(
        wiki,
        make_skill(
            "old_web_search",
            tags=["search", "web"],
            domain="browser",
            state=SkillState.DEPRECATED,
        ),
        successes=10,
    )

    engine = MemorySearchEngine(wiki)
    results = await engine.search(SearchQuery(
        text="search web",
        tags=["search"],
        domain="browser",
        min_success_rate=0.8,
        max_results=10,
    ))
    assert [result.skill.skill_id for result in results] == [strong.skill_id]

    with_deprecated = await engine.search(SearchQuery(
        text="old web search",
        tags=["search"],
        domain="browser",
        include_deprecated=True,
        max_results=10,
    ))
    assert any(result.skill.skill_id == deprecated.skill_id for result in with_deprecated)


@pytest.mark.asyncio
async def test_match_reasons_are_readable_and_stable(wiki: MemoryWikiManager):
    skill = await add_skill(
        wiki,
        make_skill(
            "api_fetch",
            description="Fetch data from remote APIs",
            tags=["api", "network"],
            domain="backend",
        ),
        successes=5,
    )

    result = score_skill_match(skill, SearchQuery(text="api backend", tags=["api"], domain="backend"))

    assert result.match_reasons
    assert "domain match" in result.match_reasons
    assert "tag match" in result.match_reasons
    assert all(reason.isascii() and "?" not in reason for reason in result.match_reasons)


@pytest.mark.asyncio
async def test_same_name_versions_return_highest_scored_version(wiki: MemoryWikiManager):
    old = await add_skill(
        wiki,
        make_skill("extract_table", description="Extract tables", version="1.0.0"),
        successes=1,
        failures=9,
    )
    new = await add_skill(
        wiki,
        make_skill("extract_table", description="Extract tables from documents", version="1.1.0"),
        successes=10,
        failures=0,
    )

    engine = MemorySearchEngine(wiki)
    results = await engine.search(SearchQuery(text="extract table", max_results=10))

    same_name = [result for result in results if result.skill.name == "extract_table"]
    assert len(same_name) == 1
    assert same_name[0].skill.skill_id == new.skill_id
    assert old.skill_id != same_name[0].skill.skill_id


@pytest.mark.asyncio
async def test_memory_engine_uses_shared_score_order(wiki: MemoryWikiManager):
    first = await add_skill(wiki, make_skill("click_button", tags=["web"]))
    second = await add_skill(wiki, make_skill("button_detector", tags=["web"]))
    query = SearchQuery(text="click button", max_results=10)

    engine = MemorySearchEngine(wiki)
    results = await engine.search(query)
    shared_order = sorted(
        [score_skill_match(first, query), score_skill_match(second, query)],
        reverse=True,
    )

    assert [result.skill.skill_id for result in results] == [
        result.skill.skill_id for result in shared_order
    ]


def test_search_api_accepts_phase_two_fields():
    wiki = MemoryWikiManager()
    app_state.wiki = wiki
    app_state.graph = MemoryGraphManager()
    app_state.search = MemorySearchEngine(wiki)
    app = FastAPI()
    app.include_router(skills.router, prefix="/api/v1")

    import anyio

    web_skill = make_skill("api_search_web", tags=["search"], domain="browser")
    for _ in range(8):
        web_skill.record_execution(success=True, latency_ms=40)
    anyio.run(wiki.create, web_skill)
    anyio.run(wiki.create, make_skill("api_search_backend", tags=["search"], domain="backend"))

    client = TestClient(app)
    response = client.post(
        "/api/v1/skills/search",
        json={
            "query": "api search",
            "tags": ["search"],
            "domain": "browser",
            "min_success_rate": 0.8,
            "include_deprecated": False,
            "limit": 10,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body] == ["api_search_web"]
    assert body[0]["match_reason"]
