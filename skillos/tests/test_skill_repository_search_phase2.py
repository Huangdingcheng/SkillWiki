from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from benchmarks.run_search_eval import load_fixture, run_search_eval, write_outputs
from skillos.api.deps import app_state
from skillos.api.memory_store import MemoryGraphManager, MemorySearchEngine, MemoryWikiManager
from skillos.api.routes import skills
from skillos.layers.skill_repository.indexing import (
    LocalHashEmbeddingProvider,
    SearchQuery,
    cosine_similarity,
    rank_search_results,
    score_skill_hybrid,
    score_skill_match,
)
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


def test_local_hash_embedding_provider_is_deterministic_and_safe():
    provider = LocalHashEmbeddingProvider(dimensions=32)

    first = provider.embed("press css target")
    second = provider.embed("press css target")

    assert first == second
    assert len(first) == 32
    assert cosine_similarity(first, second) == pytest.approx(1.0)
    assert provider.embed("") == [0.0] * 32
    assert cosine_similarity([], first) == 0.0


def test_hybrid_score_uses_lexical_semantic_health_formula():
    skill = make_skill(
        "click_element",
        description="Click a browser element located by CSS selector",
        tags=["web", "click", "selector"],
        domain="web",
    )
    for _ in range(5):
        skill.record_execution(success=True, latency_ms=40)

    result = score_skill_hybrid(skill, SearchQuery(text="press css target", domain="web"))
    components = result.score_components

    assert set(components) == {"lexical", "semantic", "health"}
    assert all(0.0 <= value <= 1.0 for value in components.values())
    assert result.score == pytest.approx(
        round(
            0.5 * components["lexical"]
            + 0.4 * components["semantic"]
            + 0.1 * components["health"],
            6,
        )
    )
    assert "semantic match" in result.match_reasons


def test_rank_search_results_can_use_explicit_hybrid_mode():
    click = make_skill(
        "click_element",
        description="Click a browser element located by CSS selector",
        tags=["web", "click", "selector"],
        domain="web",
    )
    unrelated = make_skill(
        "database_report",
        description="Summarize records from a database table",
        tags=["database", "report"],
        domain="backend",
    )

    results = rank_search_results(
        [unrelated, click],
        SearchQuery(text="press css target", mode="hybrid", max_results=5),
    )

    assert results[0].skill.skill_id == click.skill_id
    assert results[0].score_components["semantic"] > 0


def test_search_api_accepts_hybrid_mode_with_explanations():
    wiki = MemoryWikiManager()
    app_state.wiki = wiki
    app_state.graph = MemoryGraphManager()
    app_state.search = MemorySearchEngine(wiki)
    app = FastAPI()
    app.include_router(skills.router, prefix="/api/v1")

    import anyio

    anyio.run(wiki.create, make_skill(
        "click_element",
        description="Click a browser element located by CSS selector",
        tags=["web", "click", "selector"],
        domain="web",
    ))

    client = TestClient(app)
    response = client.post(
        "/api/v1/skills/search",
        json={
            "query": "press css target",
            "mode": "hybrid",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["name"] == "click_element"
    assert body[0]["search_mode"] == "hybrid"
    assert set(body[0]["explanation"]) == {"lexical", "semantic", "health"}
    assert body[0]["score_components"]["semantic"] > 0


def test_search_eval_fixture_has_fixed_twenty_queries():
    fixture = load_fixture(_search_eval_fixture_path())
    queries = fixture["queries"]

    assert fixture["benchmark"] == "skill_search_eval"
    assert fixture["mode"] == "rule"
    assert len(queries) == 20
    assert len({query["query_id"] for query in queries}) == 20
    assert all(query.get("query") for query in queries)
    assert all(query.get("expected_skill_ids") for query in queries)
    skill_ids = {skill["skill_id"] for skill in fixture["skills"]}
    assert len(skill_ids) >= 20
    assert all(
        expected_skill_id in skill_ids
        for query in queries
        for expected_skill_id in query["expected_skill_ids"]
    )


def test_search_eval_rule_baseline_reports_top1_top3_and_writes_outputs(tmp_path: Path):
    fixture = load_fixture(_search_eval_fixture_path())
    payload = run_search_eval(fixture)

    assert payload["schema_version"] == "search_eval.v0.2"
    assert payload["retrieval_mode"] == "lexical_vs_hybrid"
    assert payload["query_count"] == 20
    assert payload["top_k"] == 3
    assert payload["summary"]["top1_hits"] == 20
    assert payload["summary"]["top1_hit_rate"] == pytest.approx(1.0)
    assert payload["summary"]["top3_hits"] == 20
    assert payload["summary"]["top3_hit_rate"] == pytest.approx(1.0)
    assert payload["comparison"]["lexical"]["top1_hits"] == 20
    assert payload["comparison"]["hybrid"]["top1_hits"] == 20
    assert payload["comparison"]["hybrid"]["top3_hits"] == 20
    assert payload["comparison"]["delta"]["top1_hit_rate"] == pytest.approx(0.0)
    assert all(query["results"] for query in payload["queries"])
    assert all(query["hybrid"]["results"] for query in payload["queries"])
    assert all(
        set(query["hybrid"]["results"][0]["explanation"]) == {"lexical", "semantic", "health"}
        for query in payload["queries"]
    )

    paths = write_outputs(payload, tmp_path / "search_eval_test.json")

    result_path = Path(paths["json"])
    markdown_path = Path(paths["markdown"])
    assert result_path.exists()
    assert markdown_path.exists()
    assert json.loads(result_path.read_text(encoding="utf-8"))["schema_version"] == "search_eval.v0.2"
    assert "# SkillOS Search Evaluation Baseline" in markdown_path.read_text(encoding="utf-8")
    assert "Hybrid Top-1 hit rate" in markdown_path.read_text(encoding="utf-8")


def _search_eval_fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "benchmarks" / "search_queries.json"
