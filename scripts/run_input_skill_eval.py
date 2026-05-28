from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ROOT = REPO_ROOT / "artifacts" / "input-skill-eval-runs"
ALLOWED_INPUT_TYPES = {"trajectory", "document", "api_doc", "script", "past_skills"}


@dataclass(frozen=True)
class FixtureCase:
    source_id: str
    input_type: str
    domain: str
    source_url: str
    paper_or_project: str
    expected_skill_shape: str
    license_note: str
    local_path: Path
    content: str
    content_sha256: str
    manifest: dict[str, Any]
    truncated: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run reusable SkillOS five-input evaluation from a manifest. "
            "Raw fixture files are read-only; all outputs go under an isolated run directory."
        )
    )
    parser.add_argument("--manifest", required=True, help="JSON manifest containing a fixtures array.")
    parser.add_argument("--fixture-root", default="", help="Root for relative fixture content_file paths.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8001/api/v1")
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--limit-per-type", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=14000)
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--create-candidates", action="store_true")
    parser.add_argument("--max-candidates-per-fixture", type=int, default=1)
    parser.add_argument("--snapshot", action="store_true", help="Create a version snapshot for created candidates.")
    parser.add_argument("--fail-under", type=float, default=0.0)
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    fixture_root = Path(args.fixture_root).resolve() if args.fixture_root else manifest_path.parent
    run_dir = Path(args.run_root).resolve() / f"input-skill-eval-{args.run_id}"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    cases = load_manifest(manifest_path, fixture_root=fixture_root, max_chars=args.max_chars)
    cases = limit_cases(cases, args.limit_per_type)
    client = ApiClient(args.api_base.rstrip("/"), raw_dir)
    summary: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "api_base": client.base_url,
        "manifest": str(manifest_path),
        "fixture_root": str(fixture_root),
        "run_dir": str(run_dir),
        "run_id": args.run_id,
        "isolation": {
            "raw_fixtures_read_only": True,
            "created_candidates": bool(args.create_candidates),
            "outputs_are_run_scoped": True,
        },
        "options": {
            "limit_per_type": args.limit_per_type,
            "max_chars": args.max_chars,
            "create_candidates": args.create_candidates,
            "max_candidates_per_fixture": args.max_candidates_per_fixture,
            "snapshot": args.snapshot,
        },
        "connectivity": {},
        "fixture_count": len(cases),
        "records": [],
        "scores": {},
        "notes": [],
    }

    summary["connectivity"]["skills_before"] = client.get("/skills?limit=1000", label="skills_before_eval")
    before_count = count_skills(summary["connectivity"]["skills_before"])

    for case in cases:
        record = run_fixture_case(
            case,
            client,
            run_id=args.run_id,
            request_timeout=args.request_timeout,
            create_candidates=args.create_candidates,
            max_candidates_per_fixture=args.max_candidates_per_fixture,
            snapshot=args.snapshot,
        )
        record["score"] = score_fixture_record(record)
        summary["records"].append(record)

    summary["connectivity"]["skills_after"] = client.get("/skills?limit=1000", label="skills_after_eval")
    after_count = count_skills(summary["connectivity"]["skills_after"])
    summary["skill_counts"] = {
        "before": before_count,
        "after": after_count,
        "delta": after_count - before_count,
    }
    summary["scores"] = summarize_scores(summary["records"])
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")

    write_json(run_dir / "summary.json", summary)
    (run_dir / "REPORT.md").write_text(render_report(summary), encoding="utf-8")
    print(json.dumps({
        "run_dir": str(run_dir),
        "fixture_count": len(cases),
        "overall": summary["scores"]["overall"],
        "by_input_type": summary["scores"]["by_input_type"],
        "skill_delta": summary["skill_counts"]["delta"],
    }, indent=2, ensure_ascii=False))
    if args.fail_under and summary["scores"]["overall"] < args.fail_under:
        return 1
    return 0


def load_manifest(manifest_path: Path, *, fixture_root: Path, max_chars: int = 0) -> list[FixtureCase]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = data.get("fixtures") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("Manifest must be a JSON array or an object with a fixtures array.")
    cases: list[FixtureCase] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Fixture entry {idx} must be an object.")
        input_type = str(entry.get("input_type") or entry.get("source_type") or "").strip()
        if input_type not in ALLOWED_INPUT_TYPES:
            raise ValueError(f"Fixture {idx} has unsupported input_type {input_type!r}.")
        source_id = str(entry.get("source_id") or f"{input_type}-{idx + 1}").strip()
        path_text = str(entry.get("content_file") or entry.get("local_path") or entry.get("path") or "").strip()
        if not path_text:
            raise ValueError(f"Fixture {source_id} must provide content_file/local_path/path.")
        local_path = Path(path_text)
        if not local_path.is_absolute():
            local_path = fixture_root / local_path
        content, truncated = read_fixture_text(local_path, max_chars=max_chars)
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cases.append(FixtureCase(
            source_id=source_id,
            input_type=input_type,
            domain=str(entry.get("domain") or "other"),
            source_url=str(entry.get("source_url") or ""),
            paper_or_project=str(entry.get("paper_or_project") or ""),
            expected_skill_shape=str(entry.get("expected_skill_shape") or ""),
            license_note=str(entry.get("license_note") or ""),
            local_path=local_path,
            content=content,
            content_sha256=sha,
            manifest=dict(entry),
            truncated=truncated,
        ))
    return cases


def limit_cases(cases: list[FixtureCase], limit_per_type: int) -> list[FixtureCase]:
    if limit_per_type <= 0:
        return cases
    counts: dict[str, int] = {}
    selected: list[FixtureCase] = []
    for case in cases:
        count = counts.get(case.input_type, 0)
        if count >= limit_per_type:
            continue
        selected.append(case)
        counts[case.input_type] = count + 1
    return selected


def read_fixture_text(path: Path, *, max_chars: int) -> tuple[str, bool]:
    content = path.read_text(encoding="utf-8", errors="replace")
    if max_chars <= 0 or len(content) <= max_chars:
        return content, False
    marker = (
        "\n\n[Fixture truncated for repeatable local evaluation. "
        f"Original file: {path.name}; retained_chars={max_chars}.]\n"
    )
    keep = max(1000, max_chars - len(marker))
    return content[:keep] + marker, True


def run_fixture_case(
    case: FixtureCase,
    client: "ApiClient",
    *,
    run_id: str,
    request_timeout: int,
    create_candidates: bool,
    max_candidates_per_fixture: int,
    snapshot: bool,
) -> dict[str, Any]:
    label_base = safe_filename(f"{case.input_type}_{case.source_id}")
    record: dict[str, Any] = {
        "fixture": fixture_case_summary(case),
        "parse": {},
        "audit": {"status": "not_run"},
        "create": {"status": "not_run"},
        "graph": {"status": "not_run"},
        "version": {"status": "not_run"},
        "harness": {"status": "not_run"},
        "skillsbench": {
            "status": "not_run",
            "note": "Reserved for SkillsBench/BenchFlow adapter. This runner keeps the field explicit instead of faking benchmark execution.",
        },
    }
    parse_result = client.post(
        "/ingest/parse",
        {
            "source_type": case.input_type,
            "content": case.content,
            "metadata": {
                "eval_run_id": run_id,
                "source_id": case.source_id,
                "input_type": case.input_type,
                "domain": case.domain,
                "source_url": case.source_url,
                "paper_or_project": case.paper_or_project,
                "expected_skill_shape": case.expected_skill_shape,
                "license_note": case.license_note,
                "content_sha256": case.content_sha256,
                "origin_file": str(case.local_path),
                "ephemeral_preview_only": not create_candidates,
                "max_candidates": max(1, max_candidates_per_fixture),
            },
        },
        label=f"parse_{label_base}",
        timeout_s=request_timeout,
    )
    record["parse"] = analyze_parse(case, parse_result)

    units = parse_result.get("units") if isinstance(parse_result, dict) else []
    if not create_candidates or not units:
        return record

    created: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    graph_checks: list[dict[str, Any]] = []
    version_checks: list[dict[str, Any]] = []
    for idx, unit in enumerate(units[:max(1, max_candidates_per_fixture)]):
        payload = candidate_review_payload(unit, case, run_id=run_id)
        audit_result = client.post(
            "/ingest/audit-candidate",
            payload,
            label=f"audit_{label_base}_{idx + 1}",
            timeout_s=request_timeout,
        )
        audits.append(analyze_audit(payload, audit_result))
        create_result = client.post(
            "/ingest/create-candidate",
            payload,
            label=f"create_{label_base}_{idx + 1}",
            timeout_s=request_timeout,
        )
        created_info = analyze_create(create_result)
        created.append(created_info)
        skill_id = created_info.get("created_skill_id")
        if not skill_id:
            continue
        graph_checks.append(check_graph_for_skill(client, str(skill_id), label=f"graph_{label_base}_{idx + 1}"))
        version_checks.append(check_version_for_skill(
            client,
            str(skill_id),
            label=f"version_{label_base}_{idx + 1}",
            snapshot=snapshot,
        ))

    record["audit"] = summarize_audits(audits)
    record["create"] = summarize_creates(created)
    record["graph"] = summarize_graph_checks(graph_checks)
    record["version"] = summarize_version_checks(version_checks)
    return record


def analyze_parse(case: FixtureCase, result: dict[str, Any]) -> dict[str, Any]:
    units = result.get("units") or []
    unit_infos = [analyze_unit(unit) for unit in units if isinstance(unit, dict)]
    return {
        "http_status": result.get("_http_status"),
        "success": bool(result.get("success")),
        "unit_count": int(result.get("unit_count") or len(unit_infos)),
        "error_count": len(result.get("errors") or []),
        "errors": result.get("errors") or [],
        "elapsed_ms": result.get("_elapsed_ms"),
        "schema_completeness": average([unit["schema_completeness"] for unit in unit_infos]),
        "ctx2skill_evidence_completeness": average([unit["ctx2skill_evidence_completeness"] for unit in unit_infos]),
        "layer_correctness": expected_layer_score(case, unit_infos),
        "graph_relation_preview_count": sum(unit["graph_relation_preview_count"] for unit in unit_infos),
        "units": unit_infos,
    }


def analyze_unit(unit: dict[str, Any]) -> dict[str, Any]:
    meta = unit.get("metadata") or {}
    interface = meta.get("candidate_interface") if isinstance(meta.get("candidate_interface"), dict) else {}
    evidence = meta.get("ctx2skill_evidence") if isinstance(meta.get("ctx2skill_evidence"), dict) else {}
    relations = meta.get("candidate_relations") if isinstance(meta.get("candidate_relations"), dict) else {}
    graph_preview = meta.get("graph_relation_preview") or []
    input_schema = interface.get("input_schema") if isinstance(interface.get("input_schema"), dict) else {}
    output_schema = interface.get("output_schema") if isinstance(interface.get("output_schema"), dict) else {}
    schema_bits = [
        bool(input_schema),
        bool(output_schema),
        bool(interface.get("preconditions")),
        bool(interface.get("postconditions")),
    ]
    evidence_bits = [
        bool(evidence),
        bool(evidence.get("challenges")),
        bool(evidence.get("judge_results")),
        bool(evidence.get("selected_candidate") or evidence.get("selected_reason")),
    ]
    return {
        "unit_id": unit.get("unit_id"),
        "proposed_skill_name": unit.get("proposed_skill_name"),
        "proposed_type": unit.get("proposed_type"),
        "confidence": unit.get("confidence"),
        "schema_completeness": round(sum(1 for bit in schema_bits if bit) / len(schema_bits), 2),
        "ctx2skill_evidence_completeness": round(sum(1 for bit in evidence_bits if bit) / len(evidence_bits), 2),
        "challenge_count": len(evidence.get("challenges") or []),
        "judge_result_count": len(evidence.get("judge_results") or []),
        "candidate_score_count": len(evidence.get("candidate_scores") or []),
        "layering_reason_present": bool(meta.get("layering_reason")),
        "graph_relation_preview_count": len(graph_preview) if isinstance(graph_preview, list) else 0,
        "dependency_count": len(relations.get("dependency_ids") or []),
        "component_count": len(relations.get("component_ids") or []),
        "parent_count": len(relations.get("parent_skill_ids") or []),
    }


def expected_layer_score(case: FixtureCase, units: list[dict[str, Any]]) -> float:
    if not units:
        return 0.0
    expected = case.expected_skill_shape.strip().lower()
    if expected not in {"atomic", "functional", "strategic"}:
        return 0.5
    matches = sum(1 for unit in units if unit.get("proposed_type") == expected)
    return round(matches / len(units), 2)


def candidate_review_payload(unit: dict[str, Any], case: FixtureCase, *, run_id: str) -> dict[str, Any]:
    meta = unit.get("metadata") or {}
    interface = meta.get("candidate_interface") if isinstance(meta.get("candidate_interface"), dict) else {}
    implementation = meta.get("candidate_implementation") if isinstance(meta.get("candidate_implementation"), dict) else {}
    relations = meta.get("candidate_relations") if isinstance(meta.get("candidate_relations"), dict) else {}
    evaluation = meta.get("candidate_evaluation") if isinstance(meta.get("candidate_evaluation"), dict) else None
    source_type = unit.get("source_type") or case.input_type
    base_name = normalize_skill_name(unit.get("proposed_skill_name") or f"{case.source_id}_{source_type}_candidate")
    source_suffix = normalize_skill_name(case.source_id)
    if source_suffix and source_suffix not in base_name:
        name = normalize_skill_name(f"{base_name}_{source_suffix}")[:120]
    else:
        name = base_name
    description = unit.get("proposed_description") or unit.get("summary") or name
    if evaluation:
        evaluation = dict(evaluation)
        evaluation.setdefault("test_case_refs", [f"{case.source_id}:{unit.get('unit_id', 'unit')}"])
        evaluation.setdefault("benchmark_task_ids", list(case.manifest.get("target_benchmark_tasks") or []))
        evaluation.setdefault("validation_summary", "Created by isolated input-skill evaluation runner.")
    else:
        evaluation = {
            "verifier_specs": [{"type": "json_exists", "path": "output.result"}],
            "test_case_refs": [f"{case.source_id}:{unit.get('unit_id', 'unit')}"],
            "benchmark_task_ids": list(case.manifest.get("target_benchmark_tasks") or []),
            "validation_summary": "Created by isolated input-skill evaluation runner.",
        }
    tags = meta.get("candidate_tags") if isinstance(meta.get("candidate_tags"), list) else []
    return {
        "source_type": source_type,
        "unit_id": unit.get("unit_id") or f"{case.source_id}:unit",
        "raw_content": unit.get("raw_content") or "",
        "name": name,
        "description": description,
        "skill_type": unit.get("proposed_type") if unit.get("proposed_type") in {"atomic", "functional", "strategic"} else "atomic",
        "tags": unique_strings([
            *tags,
            *(unit.get("index_keywords") or [])[:4],
            "input-skill-eval",
            f"eval-run:{run_id}",
            f"source-id:{case.source_id}",
            f"domain:{case.domain}",
        ])[:12],
        "input_schema": interface.get("input_schema") or {"type": "object", "properties": {}},
        "output_schema": interface.get("output_schema") or {"type": "object", "properties": {"result": {"type": "object"}}},
        "preconditions": interface.get("preconditions") or [],
        "postconditions": interface.get("postconditions") or ["Candidate returns a structured result."],
        "prompt_template": implementation.get("prompt_template") or unit.get("summary") or description,
        "provenance": {
            "source_type": source_type,
            "source_ids": unique_strings([case.source_id, unit.get("unit_id") or "unit"]),
            "parent_skill_ids": relations.get("parent_skill_ids") or [],
            "created_by_agent": "input_skill_eval_runner",
            "creation_context": {
                "eval_run_id": run_id,
                "fixture_import_mode": "isolated_eval_candidate",
                "source_id": case.source_id,
                "input_type": case.input_type,
                "domain": case.domain,
                "source_url": case.source_url,
                "paper_or_project": case.paper_or_project,
                "expected_skill_shape": case.expected_skill_shape,
                "license_note": case.license_note,
                "local_path": str(case.local_path),
                "content_sha256": case.content_sha256,
                "ctx2skill_evidence": meta.get("ctx2skill_evidence"),
            },
        },
        "evaluation": evaluation,
        "dependency_ids": relations.get("dependency_ids") or [],
        "component_ids": relations.get("component_ids") or [],
        "sub_skill_ids": relations.get("sub_skill_ids") or relations.get("component_ids") or [],
        "parent_skill_ids": relations.get("parent_skill_ids") or [],
        "tool_calls": implementation.get("tool_calls") or [],
        "author": "input_skill_eval_runner",
    }


def analyze_audit(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_name": payload.get("name"),
        "http_status": result.get("_http_status"),
        "passed": bool(result.get("passed")),
        "audit_score": result.get("audit_score"),
        "issues": result.get("issues") or [],
        "warnings": result.get("warnings") or [],
    }


def analyze_create(result: dict[str, Any]) -> dict[str, Any]:
    created = result.get("created_skill") if isinstance(result.get("created_skill"), dict) else {}
    return {
        "http_status": result.get("_http_status"),
        "success": bool(result.get("success")),
        "created_skill_id": created.get("skill_id"),
        "created_skill_name": created.get("name"),
        "state": created.get("state"),
        "audit_passed": (result.get("audit") or {}).get("passed") if isinstance(result.get("audit"), dict) else None,
    }


def check_graph_for_skill(client: "ApiClient", skill_id: str, *, label: str) -> dict[str, Any]:
    result = client.get(f"/graph/view?view=skill_only&limit=500", label=label)
    nodes = result.get("nodes") or []
    edges = result.get("edges") or []
    skill_present = any(str(node.get("skill_id") or node.get("id") or "").endswith(skill_id) for node in nodes)
    relation_count = sum(
        1 for edge in edges
        if skill_id in str(edge.get("source") or edge.get("source_id") or "")
        or skill_id in str(edge.get("target") or edge.get("target_id") or "")
    )
    return {
        "skill_id": skill_id,
        "http_status": result.get("_http_status"),
        "skill_present": skill_present,
        "relation_count": relation_count,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def check_version_for_skill(client: "ApiClient", skill_id: str, *, label: str, snapshot: bool) -> dict[str, Any]:
    diff = client.get(f"/lifecycle/{skill_id}/diff", label=f"{label}_diff")
    result = {
        "skill_id": skill_id,
        "http_status": diff.get("_http_status"),
        "business_diff_available": "business_diff" in diff or "business_summary" in diff or "history" in diff,
        "raw_diff_available": "raw_diff" in diff or "changes" in diff or "snapshot_diff" in diff or "history" in diff,
        "snapshot_created": False,
    }
    if snapshot:
        snap = client.post(
            f"/lifecycle/{skill_id}/snapshot",
            {
                "description": "Input-skill eval snapshot.",
                "author": "input_skill_eval_runner",
            },
            label=f"{label}_snapshot",
            timeout_s=120,
        )
        result["snapshot_http_status"] = snap.get("_http_status")
        result["snapshot_created"] = snap.get("_http_status") in {200, 201} and not snap.get("detail")
    return result


def summarize_audits(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"status": "not_run"}
    return {
        "status": "completed",
        "count": len(items),
        "passed_count": sum(1 for item in items if item.get("passed")),
        "average_audit_score": average([
            float(item.get("audit_score") or 0.0)
            for item in items
            if isinstance(item.get("audit_score"), (int, float))
        ]),
        "items": items,
    }


def summarize_creates(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"status": "not_run"}
    created_ids = [item.get("created_skill_id") for item in items if item.get("created_skill_id")]
    return {
        "status": "completed",
        "count": len(items),
        "success_count": sum(1 for item in items if item.get("success")),
        "created_skill_ids": created_ids,
        "http_statuses": [item.get("http_status") for item in items],
        "items": items,
    }


def summarize_graph_checks(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"status": "not_run"}
    return {
        "status": "completed",
        "count": len(items),
        "present_count": sum(1 for item in items if item.get("skill_present")),
        "relation_count": sum(int(item.get("relation_count") or 0) for item in items),
        "items": items,
    }


def summarize_version_checks(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"status": "not_run"}
    return {
        "status": "completed",
        "count": len(items),
        "business_diff_available_count": sum(1 for item in items if item.get("business_diff_available")),
        "snapshot_created_count": sum(1 for item in items if item.get("snapshot_created")),
        "items": items,
    }


def score_fixture_record(record: dict[str, Any]) -> float:
    parse = record.get("parse") or {}
    score = 0.0
    if parse.get("http_status") == 200 and parse.get("success"):
        score += 0.18
    if parse.get("unit_count", 0) > 0:
        score += 0.10
    score += 0.08 * clamp01(float(parse.get("schema_completeness") or 0.0))
    score += 0.08 * clamp01(float(parse.get("ctx2skill_evidence_completeness") or 0.0))
    score += 0.06 * clamp01(float(parse.get("layer_correctness") or 0.0))

    audit = record.get("audit") or {}
    if audit.get("status") == "completed":
        score += 0.10 * ratio(audit.get("passed_count"), audit.get("count"))
    elif audit.get("http_status") == 200 and audit.get("passed"):
        score += 0.10

    create = record.get("create") or {}
    if create.get("status") == "completed":
        score += 0.12 * ratio(create.get("success_count"), create.get("count"))
    elif create.get("http_status") in {200, 201} and create.get("success"):
        score += 0.12

    graph = record.get("graph") or {}
    if graph.get("status") == "completed":
        score += 0.10 * ratio(graph.get("present_count"), graph.get("count"))
        if int(graph.get("relation_count") or 0) > 0:
            score += 0.05
    elif graph.get("http_status") == 200 and graph.get("skill_present"):
        score += 0.10
        if int(graph.get("relation_count") or 0) > 0:
            score += 0.05

    version = record.get("version") or {}
    if version.get("status") == "completed":
        score += 0.06 * ratio(version.get("business_diff_available_count"), version.get("count"))
        if int(version.get("snapshot_created_count") or 0) > 0:
            score += 0.03
    elif version.get("http_status") == 200:
        if version.get("business_diff_available"):
            score += 0.06
        if version.get("snapshot_created"):
            score += 0.03

    harness = record.get("harness") or {}
    if harness.get("status") == "verified":
        score += 0.12
    elif harness.get("positive_pass") or harness.get("negative_rejected"):
        score += 0.06

    skillsbench = record.get("skillsbench") or {}
    if skillsbench.get("status") in {"mapped", "completed"} and skillsbench.get("verifier_ran"):
        score += 0.10
        if skillsbench.get("generated_pass"):
            score += 0.05

    return round(min(score, 1.0), 3)


def summarize_scores(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_type_values: dict[str, list[float]] = {}
    by_domain_values: dict[str, list[float]] = {}
    for record in records:
        score = float(record.get("score") or 0.0)
        fixture = record.get("fixture") or {}
        by_type_values.setdefault(str(fixture.get("input_type") or "unknown"), []).append(score)
        by_domain_values.setdefault(str(fixture.get("domain") or "unknown"), []).append(score)
    return {
        "overall": average([float(record.get("score") or 0.0) for record in records]),
        "by_input_type": {key: average(values) for key, values in by_type_values.items()},
        "by_domain": {key: average(values) for key, values in by_domain_values.items()},
        "sample_count": len(records),
    }


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# SkillOS Input-to-Skill Evaluation Report",
        "",
        f"- Started: `{summary['started_at']}`",
        f"- Finished: `{summary.get('finished_at', '')}`",
        f"- API base: `{summary['api_base']}`",
        f"- Manifest: `{summary['manifest']}`",
        f"- Fixture root: `{summary['fixture_root']}`",
        f"- Run directory: `{summary['run_dir']}`",
        f"- Created candidates: `{summary['options']['create_candidates']}`",
        "",
        "## Isolation",
        "",
        "- Raw fixture files are read only.",
        "- API responses and reports are written only under this run directory.",
        "- Candidate creation is disabled unless `--create-candidates` is passed.",
        "- SkillsBench results are explicit fields and remain `not_run` until the benchmark adapter executes.",
        "",
        "## Scores",
        "",
        f"- Overall: `{summary['scores']['overall']}`",
        f"- By input type: `{json.dumps(summary['scores']['by_input_type'], ensure_ascii=False)}`",
        f"- By domain: `{json.dumps(summary['scores']['by_domain'], ensure_ascii=False)}`",
        f"- Skill delta: `{summary['skill_counts']['delta']}`",
        "",
        "## Fixtures",
        "",
    ]
    for record in summary["records"]:
        fixture = record["fixture"]
        parse = record["parse"]
        lines.extend([
            f"### {fixture['source_id']} - {record['score']}",
            f"- Input/domain: `{fixture['input_type']}` / `{fixture['domain']}`",
            f"- Source: `{fixture.get('source_url', '')}`",
            f"- Paper/project: `{fixture.get('paper_or_project', '')}`",
            f"- Local file: `{fixture['local_path']}`",
            f"- Parse: http=`{parse.get('http_status')}`, success=`{parse.get('success')}`, "
            f"units=`{parse.get('unit_count')}`, schema=`{parse.get('schema_completeness')}`, "
            f"ctx2skill=`{parse.get('ctx2skill_evidence_completeness')}`, layer=`{parse.get('layer_correctness')}`",
            f"- Audit: `{record.get('audit', {}).get('status')}`",
            f"- Create: `{record.get('create', {}).get('status')}`",
            f"- Graph: `{record.get('graph', {}).get('status')}`",
            f"- Version: `{record.get('version', {}).get('status')}`",
            f"- Harness: `{record.get('harness', {}).get('status')}`",
            f"- SkillsBench: `{record.get('skillsbench', {}).get('status')}`",
            "",
        ])
    lines.append("Raw API envelopes are saved under `raw/` next to this report.")
    lines.append("")
    return "\n".join(lines)


class ApiClient:
    def __init__(self, base_url: str, raw_dir: Path) -> None:
        self.base_url = base_url
        self.raw_dir = raw_dir

    def get(self, path: str, *, label: str, timeout_s: int = 30) -> dict[str, Any]:
        return self._request("GET", self.base_url + path, None, label, timeout_s)

    def post(self, path: str, payload: dict[str, Any], *, label: str, timeout_s: int = 120) -> dict[str, Any]:
        return self._request("POST", self.base_url + path, payload, label, timeout_s)

    def _request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        label: str,
        timeout_s: int,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except Exception as exc:
            body = json.dumps({"error": str(exc)}, ensure_ascii=False)
            status = 0
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        parsed = parse_body(body)
        envelope = {
            "label": label,
            "method": method,
            "url": url,
            "status_code": status,
            "elapsed_ms": elapsed_ms,
            "request_preview": preview_payload(payload),
            "body": parsed,
        }
        write_json(self.raw_dir / f"{safe_filename(label)}.json", envelope)
        if isinstance(parsed, dict):
            result = dict(parsed)
            result["_http_status"] = status
            result["_elapsed_ms"] = elapsed_ms
            return result
        return {"_http_status": status, "_elapsed_ms": elapsed_ms, "body": parsed}


def fixture_case_summary(case: FixtureCase) -> dict[str, Any]:
    return {
        "source_id": case.source_id,
        "input_type": case.input_type,
        "domain": case.domain,
        "source_url": case.source_url,
        "paper_or_project": case.paper_or_project,
        "expected_skill_shape": case.expected_skill_shape,
        "license_note": case.license_note,
        "local_path": str(case.local_path),
        "content_sha256": case.content_sha256,
        "truncated": case.truncated,
    }


def count_skills(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        body = payload.get("body")
        if isinstance(body, list):
            return len(body)
        for key in ("value", "skills", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        if isinstance(payload.get("total"), int):
            return int(payload["total"])
    return 0


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_body(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def preview_payload(payload: dict[str, Any] | None) -> Any:
    if payload is None:
        return None
    preview = dict(payload)
    content = preview.get("content")
    if isinstance(content, str):
        preview["content"] = f"{content[:500]}... [chars={len(content)}]"
    return preview


def normalize_skill_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:120] or "input_skill_eval_candidate"


def safe_filename(value: str) -> str:
    normalized = normalize_skill_name(value).replace("/", "_")
    if len(normalized) <= 96:
        return normalized
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:84].rstrip('_')}_{digest}"


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def average(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def ratio(numerator: Any, denominator: Any) -> float:
    try:
        denom = float(denominator or 0)
        if denom <= 0:
            return 0.0
        return clamp01(float(numerator or 0) / denom)
    except (TypeError, ValueError):
        return 0.0


def clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


if __name__ == "__main__":
    sys.exit(main())
