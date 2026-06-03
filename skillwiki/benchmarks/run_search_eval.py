"""Run the offline Skill search evaluation.

This is a small SkillsBench-style retrieval fixture for the Skill repository.
It reports the original lexical/rule baseline and the A-P1 local hybrid search
side by side so later UI work can consume a paired comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from skillos.layers.skill_repository.indexing import (  # noqa: E402
    SearchQuery,
    rank_search_results,
)
from skillos.models.skill_model import (  # noqa: E402
    MetaSkillCategory,
    Skill,
    SkillImplementation,
    SkillState,
    SkillType,
)


DEFAULT_QUERY_PATH = Path(__file__).with_name("search_queries.json")
DEFAULT_OUTPUT_PATH = Path(__file__).with_name("results") / "search_eval_latest.json"


def load_queries(path: Path) -> List[Dict[str, Any]]:
    """Load and validate search evaluation queries."""

    raw_fixture = json.loads(path.read_text(encoding="utf-8"))
    queries = raw_fixture.get("queries", []) if isinstance(raw_fixture, dict) else raw_fixture
    return _normalize_query_rows(queries)


def load_fixture(path: Path) -> Dict[str, Any]:
    """Load the fixed search benchmark fixture."""

    raw_fixture = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw_fixture, dict):
        skills = _normalize_skill_specs(raw_fixture.get("skills", []))
        queries = _normalize_query_rows(raw_fixture.get("queries", []))
        benchmark = str(raw_fixture.get("benchmark", "skill_search_eval")).strip()
        mode = str(raw_fixture.get("mode", "rule")).strip()
    elif isinstance(raw_fixture, list):
        skills = []
        queries = _normalize_query_rows(raw_fixture)
        benchmark = "skill_search_eval"
        mode = "rule"
    else:
        raise ValueError("Search eval fixture must be a JSON object or list.")

    if benchmark != "skill_search_eval":
        raise ValueError("Search eval fixture benchmark must be 'skill_search_eval'.")
    if mode != "rule":
        raise ValueError("Search eval fixture mode must be 'rule'.")
    if len(queries) != 20:
        raise ValueError("Search eval fixture must contain exactly 20 queries.")

    if skills:
        skill_ids = {skill["skill_id"] for skill in skills}
        missing_expected = sorted({
            expected_id
            for query in queries
            for expected_id in query["expected_skill_ids"]
            if expected_id not in skill_ids
        })
        if missing_expected:
            raise ValueError(
                "Search eval expected_skill_ids missing from skills: "
                + ", ".join(missing_expected)
            )

    return {
        "benchmark": benchmark,
        "mode": mode,
        "skills": skills,
        "queries": queries,
    }


def _normalize_query_rows(queries: Any) -> List[Dict[str, Any]]:
    if not isinstance(queries, list):
        raise ValueError("Search query file must contain a JSON list or a fixture object with queries.")
    seen_ids: set[str] = set()
    normalized: List[Dict[str, Any]] = []
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            raise ValueError(f"search_queries[{index}] must be an object.")
        query_id = str(query.get("query_id", "")).strip()
        query_text = str(query.get("query") or query.get("text") or "").strip()
        expected_skill_ids = [
            str(skill_id).strip()
            for skill_id in query.get("expected_skill_ids", [])
            if str(skill_id).strip()
        ]
        if not query_id:
            raise ValueError(f"search_queries[{index}].query_id must not be blank.")
        if query_id in seen_ids:
            raise ValueError(f"Duplicate search query id: {query_id}")
        if not query_text:
            raise ValueError(f"search_queries[{index}].query must not be blank.")
        if not expected_skill_ids:
            raise ValueError(f"search_queries[{index}].expected_skill_ids must not be empty.")

        seen_ids.add(query_id)
        normalized.append({
            **query,
            "query_id": query_id,
            "query": query_text,
            "text": query_text,
            "expected_skill_ids": expected_skill_ids,
        })

    return normalized


def _normalize_skill_specs(skills: Any) -> List[Dict[str, Any]]:
    if not isinstance(skills, list):
        raise ValueError("Search eval fixture skills must be a JSON list.")
    seen_ids: set[str] = set()
    normalized: List[Dict[str, Any]] = []
    for index, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise ValueError(f"skills[{index}] must be an object.")
        skill_id = str(skill.get("skill_id", "")).strip()
        if not skill_id:
            raise ValueError(f"skills[{index}].skill_id must not be blank.")
        if skill_id in seen_ids:
            raise ValueError(f"Duplicate skill id in search eval fixture: {skill_id}")
        seen_ids.add(skill_id)
        normalized.append({
            **skill,
            "skill_id": skill_id,
            "name": str(skill.get("name") or skill_id).strip(),
            "description": str(skill.get("description", "")).strip(),
            "domain": str(skill.get("domain", "general")).strip() or "general",
            "tags": [str(tag).strip().lower() for tag in skill.get("tags", []) if str(tag).strip()],
        })
    return normalized


def build_search_eval_catalog() -> List[Skill]:
    """Return a deterministic Skill catalog for rule-search evaluation."""

    catalog_specs = [
        {
            "skill_id": "fill_form",
            "domain": "web",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["web", "form", "browser"],
            "description": "Fill structured browser forms with provided field values.",
        },
        {
            "skill_id": "click_element",
            "display_name": "click element selector",
            "domain": "web",
            "tags": ["web", "click", "selector"],
            "description": "Click a browser element located by CSS selector.",
        },
        {
            "skill_id": "type_text",
            "display_name": "type text input",
            "domain": "web",
            "tags": ["web", "text", "input"],
            "description": "Type text into an active browser input.",
        },
        {
            "skill_id": "locate_element",
            "domain": "web",
            "tags": ["web", "selector", "dom"],
            "description": "Locate a DOM element for downstream browser actions.",
        },
        {
            "skill_id": "submit_form",
            "domain": "web",
            "tags": ["web", "form", "submit"],
            "description": "Submit a browser form and wait for the resulting state.",
        },
        {
            "skill_id": "extract_selector",
            "display_name": "extract selector text",
            "domain": "web",
            "tags": ["web", "selector", "extract"],
            "description": "Extract visible text or attributes from a selector.",
        },
        {
            "skill_id": "parse_openapi_endpoint",
            "domain": "api",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["api", "openapi", "endpoint"],
            "description": "Parse an OpenAPI path and method into a callable endpoint spec.",
        },
        {
            "skill_id": "build_tool_call",
            "domain": "api",
            "tags": ["api", "tool", "call"],
            "description": "Build a validated tool call payload from a capability request.",
        },
        {
            "skill_id": "validate_response_schema",
            "domain": "api",
            "tags": ["api", "schema", "validation"],
            "description": "Validate an API response object against its JSON schema.",
        },
        {
            "skill_id": "extract_steps",
            "display_name": "extract procedural steps",
            "domain": "document",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["document", "steps", "procedure"],
            "description": "Extract ordered procedural steps from a document passage.",
        },
        {
            "skill_id": "extract_function_skill",
            "domain": "code",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["code", "function", "skill"],
            "description": "Extract a reusable Skill candidate from a code function.",
        },
        {
            "skill_id": "normalize_email",
            "display_name": "normalize email helper",
            "domain": "code",
            "tags": ["code", "email", "normalize"],
            "description": "Normalize email strings for deterministic comparison.",
        },
        {
            "skill_id": "reflect_failure",
            "display_name": "reflect failure repair",
            "domain": "runtime",
            "skill_type": SkillType.STRATEGIC,
            "meta_category": MetaSkillCategory.MAINTENANCE,
            "tags": ["runtime", "reflection", "repair"],
            "description": "Reflect on a failed execution and propose a repair action.",
        },
        {
            "skill_id": "detect_schema_change",
            "domain": "governance",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["governance", "schema", "diff"],
            "description": "Detect whether a Skill schema change may break callers.",
        },
        {
            "skill_id": "trace_provenance",
            "display_name": "trace provenance graph",
            "domain": "graph",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["graph", "provenance", "source"],
            "description": "Trace source, execution, validation, and version evidence in the graph.",
        },
        {
            "skill_id": "generate_skill_from_trajectory",
            "domain": "repository",
            "skill_type": SkillType.STRATEGIC,
            "meta_category": MetaSkillCategory.GENERATION,
            "tags": ["repository", "trajectory", "generation"],
            "description": "Generate a reusable Skill candidate from an action trajectory.",
        },
        {
            "skill_id": "verify_json_output",
            "domain": "runtime",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["runtime", "json", "verify"],
            "description": "Verify JSON output with deterministic checks.",
        },
        {
            "skill_id": "summarize_benchmark_results",
            "domain": "evaluation",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["evaluation", "benchmark", "summary"],
            "description": "Summarize benchmark results for paper and UI evidence.",
        },
        {
            "skill_id": "propose_maintenance_change",
            "domain": "maintenance",
            "skill_type": SkillType.STRATEGIC,
            "meta_category": MetaSkillCategory.MAINTENANCE,
            "tags": ["maintenance", "proposal", "repair"],
            "description": "Propose a maintenance change for human review.",
        },
        {
            "skill_id": "compare_skill_snapshots",
            "domain": "governance",
            "skill_type": SkillType.FUNCTIONAL,
            "tags": ["governance", "snapshot", "diff"],
            "description": "Compare Skill snapshots and classify semantic differences.",
        },
        {
            "skill_id": "search_web_page",
            "display_name": "search web page",
            "domain": "web",
            "tags": ["web", "search"],
            "description": "Search a web page for visible text.",
            "successes": 2,
        },
        {
            "skill_id": "generate_benchmark_fixture",
            "display_name": "generate benchmark fixture",
            "domain": "evaluation",
            "tags": ["evaluation", "fixture"],
            "description": "Generate a benchmark fixture from a task list.",
            "successes": 2,
        },
    ]

    return build_catalog(catalog_specs)


def build_catalog(skill_specs: Sequence[Dict[str, Any]]) -> List[Skill]:
    """Build Pydantic Skill objects from fixture skill specs."""

    skills: List[Skill] = []
    for spec in skill_specs:
        skill_id = str(spec["skill_id"])
        name = str(spec.get("name", skill_id))
        display_name = str(spec.get("display_name", name.replace("_", " ")))
        skill_type = _skill_type(spec.get("skill_type")) or SkillType.ATOMIC
        meta_category = _meta_category(spec.get("meta_category"))
        if skill_type == SkillType.STRATEGIC and meta_category is None:
            raise ValueError(f"Strategic search eval Skill needs meta_category: {skill_id}")
        if skill_type != SkillType.STRATEGIC:
            meta_category = None
        skill = Skill(
            skill_id=skill_id,
            name=name,
            display_name=display_name,
            description=str(spec["description"]),
            tags=list(spec.get("tags", [])),
            domain=str(spec["domain"]),
            skill_type=skill_type,
            meta_category=meta_category,
            state=SkillState.RELEASED,
            implementation=SkillImplementation(
                prompt_template=f"Run the {name} Skill for the supplied input."
            ),
        )
        for _ in range(int(spec.get("successes", 8))):
            skill.record_execution(success=True, latency_ms=45)
        for _ in range(int(spec.get("failures", 0))):
            skill.record_execution(success=False, latency_ms=90)
        skills.append(skill)
    return skills


def run_search_eval(
    queries: Sequence[Dict[str, Any]] | Dict[str, Any],
    skills: Sequence[Skill] | None = None,
    *,
    top_k: int = 3,
    include_hybrid: bool = True,
) -> Dict[str, Any]:
    """Evaluate top-1/top-k retrieval hits for fixed queries."""

    query_rows = _queries_from_input(queries)
    fixture_skill_specs = queries.get("skills", []) if isinstance(queries, dict) else []
    if skills is not None:
        catalog = list(skills)
    elif fixture_skill_specs:
        catalog = build_catalog(_normalize_skill_specs(fixture_skill_specs))
    else:
        catalog = build_search_eval_catalog()
    cutoff = max(1, top_k)
    lexical_rows = [
        _evaluate_query(query, catalog, top_k=cutoff, mode="lexical")
        for query in query_rows
    ]
    hybrid_rows = [
        _evaluate_query(query, catalog, top_k=cutoff, mode="hybrid")
        for query in query_rows
    ] if include_hybrid else []
    rows = _combine_mode_rows(lexical_rows, hybrid_rows)
    lexical_summary = _summary_from_rows(lexical_rows)
    hybrid_summary = _summary_from_rows(hybrid_rows) if include_hybrid else None
    top1_hits = lexical_summary["top1_hits"]
    topk_hits = lexical_summary["top3_hits"]
    query_count = len(rows)
    payload: Dict[str, Any] = {
        "benchmark": "skill_search_eval",
        "schema_version": "search_eval.v0.2",
        "generated_at": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
                .replace("+00:00", "Z")
        ),
        "mode": "comparison" if include_hybrid else "rule",
        "retrieval_mode": "lexical_vs_hybrid" if include_hybrid else "rule_search_baseline",
        "baseline_mode": "lexical",
        "hybrid_mode": "local_hash_embedding" if include_hybrid else None,
        "top_k": cutoff,
        "catalog_size": len(catalog),
        "query_count": query_count,
        "summary": {
            "query_count": query_count,
            "top1": {
                "hits": top1_hits,
                "total": query_count,
                "hit_rate": lexical_summary["top1_hit_rate"],
            },
            "top3": {
                "hits": topk_hits,
                "total": query_count,
                "hit_rate": lexical_summary["top3_hit_rate"],
            },
            "top1_hits": top1_hits,
            "top1_hit_rate": lexical_summary["top1_hit_rate"],
            "top3_hits": topk_hits,
            "top3_hit_rate": lexical_summary["top3_hit_rate"],
            "lexical": lexical_summary,
            "hybrid": hybrid_summary,
        },
        "comparison": _comparison_summary(lexical_summary, hybrid_summary),
        "queries": rows,
        "results": rows,
    }
    return payload


def write_outputs(payload: Dict[str, Any], output_path: Path, *, markdown: bool = True) -> Dict[str, Path]:
    """Write JSON and optional Markdown outputs for later E-page consumption."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths = {"json": output_path}
    if markdown:
        markdown_path = output_path.with_suffix(".md")
        markdown_path.write_text(to_markdown(payload), encoding="utf-8")
        paths["markdown"] = markdown_path
    return paths


def write_eval_payload(payload: Dict[str, Any], output_path: Path) -> Dict[str, str]:
    """Write result and stable latest aliases for local evaluation checks."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path = output_path.with_suffix(".md")
    latest_json = output_path.parent / "search_eval_latest.json"
    latest_markdown = output_path.parent / "search_eval_latest.md"

    json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    markdown_text = to_markdown(payload)
    output_path.write_text(json_text, encoding="utf-8")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    latest_markdown.write_text(markdown_text, encoding="utf-8")

    return {
        "result": str(output_path),
        "markdown": str(markdown_path),
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_markdown),
    }


def to_markdown(payload: Dict[str, Any]) -> str:
    """Render a compact report readable by humans and frontend tooling."""

    summary = payload["summary"]
    hybrid = summary.get("hybrid") or {}
    lines = [
        "# SkillOS Search Evaluation Baseline",
        "",
        f"- Retrieval mode: `{payload['retrieval_mode']}`",
        f"- Query count: {payload['query_count']}",
        f"- Catalog size: {payload['catalog_size']}",
        f"- Lexical Top-1 hit rate: {summary['top1_hits']}/{payload['query_count']} ({summary['top1_hit_rate']:.2%})",
        f"- Lexical Top-3 hit rate: {summary['top3_hits']}/{payload['query_count']} ({summary['top3_hit_rate']:.2%})",
    ]
    if hybrid:
        lines.extend([
            f"- Hybrid Top-1 hit rate: {hybrid['top1_hits']}/{payload['query_count']} ({hybrid['top1_hit_rate']:.2%})",
            f"- Hybrid Top-3 hit rate: {hybrid['top3_hits']}/{payload['query_count']} ({hybrid['top3_hit_rate']:.2%})",
        ])
    lines.extend([
        "",
        "| Query ID | Query | Expected | Lexical top | Hybrid top | Lexical rank | Hybrid rank |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for row in payload["queries"]:
        lexical = row.get("lexical", row)
        hybrid_row = row.get("hybrid") or {}
        lexical_top = lexical["results"][0]["skill_id"] if lexical.get("results") else ""
        hybrid_top = hybrid_row["results"][0]["skill_id"] if hybrid_row.get("results") else ""
        expected = ", ".join(row["expected_skill_ids"])
        lexical_rank = lexical["best_rank"] if lexical.get("best_rank") is not None else ""
        hybrid_rank = hybrid_row["best_rank"] if hybrid_row.get("best_rank") is not None else ""
        lines.append(
            f"| {row['query_id']} | {row['query']} | {expected} | {lexical_top} | "
            f"{hybrid_top} | {lexical_rank} | {hybrid_rank} |"
        )
    return "\n".join(lines) + "\n"


def _evaluate_query(
    query: Dict[str, Any],
    skills: Sequence[Skill],
    *,
    top_k: int,
    mode: str,
) -> Dict[str, Any]:
    search_query = SearchQuery(
        text=str(query["query"]),
        tags=[str(tag) for tag in query.get("tags", [])],
        skill_type=_skill_type(query.get("skill_type")),
        domain=query.get("domain"),
        mode=mode,
        max_results=max(top_k, 10),
    )
    ranked = rank_search_results(skills, search_query)
    expected = set(query["expected_skill_ids"])
    result_rows = [
        {
            "rank": index + 1,
            "skill_id": result.skill.skill_id,
            "name": result.skill.name,
            "display_name": result.skill.display_name,
            "score": result.score,
            "match_reasons": result.match_reasons,
            "score_components": result.score_components,
            "explanation": {
                "lexical": result.score_components.get("lexical", 0.0),
                "semantic": result.score_components.get("semantic", 0.0),
                "health": result.score_components.get("health", 0.0),
            },
        }
        for index, result in enumerate(ranked)
    ]
    best_rank = next(
        (
            result["rank"]
            for result in result_rows
            if result["skill_id"] in expected
        ),
        None,
    )
    return {
        "query_id": query["query_id"],
        "query": query["query"],
        "mode": mode,
        "domain": query.get("domain"),
        "tags": query.get("tags", []),
        "expected_skill_ids": query["expected_skill_ids"],
        "best_rank": best_rank,
        "top1_hit": bool(result_rows and result_rows[0]["skill_id"] in expected),
        "topk_hit": bool(best_rank is not None and best_rank <= top_k),
        "results": result_rows[:top_k],
        "retrieved": result_rows[:top_k],
    }


def _combine_mode_rows(
    lexical_rows: Sequence[Dict[str, Any]],
    hybrid_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    hybrid_by_id = {row["query_id"]: row for row in hybrid_rows}
    rows: List[Dict[str, Any]] = []
    for lexical in lexical_rows:
        hybrid = hybrid_by_id.get(lexical["query_id"])
        row = {
            **lexical,
            "lexical": lexical,
            "hybrid": hybrid,
        }
        if hybrid:
            row["hybrid_top1_hit"] = hybrid["top1_hit"]
            row["hybrid_topk_hit"] = hybrid["topk_hit"]
            row["hybrid_best_rank"] = hybrid["best_rank"]
        rows.append(row)
    return rows


def _summary_from_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    query_count = len(rows)
    top1_hits = sum(1 for row in rows if row["top1_hit"])
    top3_hits = sum(1 for row in rows if row["topk_hit"])
    return {
        "query_count": query_count,
        "top1_hits": top1_hits,
        "top1_hit_rate": _rate(top1_hits, query_count),
        "top3_hits": top3_hits,
        "top3_hit_rate": _rate(top3_hits, query_count),
    }


def _comparison_summary(
    lexical: Dict[str, Any],
    hybrid: Dict[str, Any] | None,
) -> Dict[str, Any]:
    comparison = {
        "lexical": lexical,
        "hybrid": hybrid,
        "delta": None,
    }
    if hybrid:
        comparison["delta"] = {
            "top1_hit_rate": round(hybrid["top1_hit_rate"] - lexical["top1_hit_rate"], 4),
            "top3_hit_rate": round(hybrid["top3_hit_rate"] - lexical["top3_hit_rate"], 4),
        }
    return comparison


def _queries_from_input(queries: Sequence[Dict[str, Any]] | Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(queries, dict):
        query_rows = queries.get("queries", [])
    else:
        query_rows = queries
    return _normalize_query_rows(list(query_rows) if isinstance(query_rows, Sequence) else query_rows)


def _skill_type(value: Any) -> SkillType | None:
    if value is None or value == "":
        return None
    if isinstance(value, SkillType):
        return value
    return SkillType(str(value))


def _meta_category(value: Any) -> MetaSkillCategory | None:
    if value is None or value == "":
        return None
    if isinstance(value, MetaSkillCategory):
        return value
    return MetaSkillCategory(str(value))


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run SkillOS rule-search evaluation.")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--no-hybrid", action="store_true")
    parser.add_argument("--no-markdown", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    queries = load_fixture(args.queries)
    payload = run_search_eval(queries, top_k=args.top_k, include_hybrid=not args.no_hybrid)
    paths = write_outputs(payload, args.output, markdown=not args.no_markdown)
    print(f"Wrote search eval JSON: {paths['json']}")
    if "markdown" in paths:
        print(f"Wrote search eval Markdown: {paths['markdown']}")
    print(
        "Top-1: "
        f"{payload['summary']['top1_hits']}/{payload['query_count']} "
        f"({payload['summary']['top1_hit_rate']:.2%}); "
        "Top-3: "
        f"{payload['summary']['top3_hits']}/{payload['query_count']} "
        f"({payload['summary']['top3_hit_rate']:.2%})"
    )
    hybrid = payload["summary"].get("hybrid")
    if hybrid:
        print(
            "Hybrid Top-1: "
            f"{hybrid['top1_hits']}/{payload['query_count']} "
            f"({hybrid['top1_hit_rate']:.2%}); "
            "Hybrid Top-3: "
            f"{hybrid['top3_hits']}/{payload['query_count']} "
            f"({hybrid['top3_hit_rate']:.2%})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
