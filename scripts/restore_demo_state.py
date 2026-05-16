from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "docs" / "demo-fixtures"
RUNTIME_DIR = REPO_ROOT / "skillos-one-click-launcher" / "runtime" / "demo-state-runs"

APPROVED_PAST_SKILL_NAMES = {
    "brand_guidelines",
    "claude_api",
    "docx",
    "frontend_design",
}
SCRIPT_SKILL = "script_dry_run_analyzer"
LEGACY_SKILL = "legacy_login_flow_imported"


class ApiClient:
    def __init__(self, base_url: str, raw_dir: Path, timeout_s: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.raw_dir = raw_dir
        self.timeout_s = timeout_s
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def get(self, path: str, *, label: str, timeout_s: int | None = None) -> Any:
        return self._request("GET", path, None, label, timeout_s or self.timeout_s)

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        label: str,
        timeout_s: int | None = None,
    ) -> Any:
        return self._request("POST", path, payload, label, timeout_s or self.timeout_s)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        label: str,
        timeout_s: int,
    ) -> Any:
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except Exception as exc:  # pragma: no cover - useful for operator reports
            body = json.dumps({"error": str(exc)}, ensure_ascii=False)
            status = 0
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        parsed = _parse_body(body)
        envelope = {
            "label": label,
            "method": method,
            "url": url,
            "status_code": status,
            "elapsed_ms": elapsed_ms,
            "request_preview": _preview_payload(payload),
            "body": parsed,
        }
        (self.raw_dir / f"{_safe_filename(label)}.json").write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if isinstance(parsed, dict):
            parsed = dict(parsed)
            parsed["_http_status"] = status
            parsed["_elapsed_ms"] = elapsed_ms
            return parsed
        return {"_http_status": status, "_elapsed_ms": elapsed_ms, "body": parsed}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore a repeatable local SkillOS demo state through public backend APIs."
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8001/api/v1")
    parser.add_argument("--frontend-base", default="http://127.0.0.1:5174")
    parser.add_argument("--fixture-dir", type=Path, default=FIXTURE_DIR)
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--request-timeout", type=int, default=360)
    parser.add_argument("--harness-timeout", type=int, default=160)
    parser.add_argument("--skip-approved-import", action="store_true")
    parser.add_argument("--skip-harness", action="store_true")
    parser.add_argument("--skip-related-graph", action="store_true")
    args = parser.parse_args()

    fixture_dir = args.fixture_dir.resolve()
    if not fixture_dir.exists():
        raise SystemExit(f"Fixture directory not found: {fixture_dir}")

    run_dir = RUNTIME_DIR / f"restore-demo-state-{args.run_id}"
    raw_dir = run_dir / "raw"
    run_dir.mkdir(parents=True, exist_ok=True)
    client = ApiClient(args.api_base, raw_dir, args.request_timeout)

    summary: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "api_base": client.base_url,
        "frontend_base": args.frontend_base.rstrip("/"),
        "fixture_dir": str(fixture_dir),
        "run_dir": str(run_dir),
        "approved_import": {},
        "harness": {},
        "related_graph": {},
        "errors": [],
        "notes": [
            "This restore is intended for local memory-backend demos.",
            "All imported content stays in S1 Candidate unless harness verification promotes a repaired version to S3.",
            "The fixtures are synthetic and safe to commit; no API keys are stored here.",
        ],
    }

    if not args.skip_approved_import:
        summary["approved_import"] = restore_approved_import(client, fixture_dir)
    if not args.skip_harness:
        summary["harness"] = restore_harness_checks(client, fixture_dir, args.harness_timeout)
    if not args.skip_related_graph:
        summary["related_graph"] = restore_related_graph(client, fixture_dir)

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary["scores"] = score_summary(summary)
    write_report(run_dir, summary)
    print(json.dumps({
        "run_dir": str(run_dir),
        "scores": summary["scores"],
        "approved_import": _small_import_summary(summary.get("approved_import", {})),
        "harness": summary.get("harness", {}).get("scores", {}),
        "related_graph": summary.get("related_graph", {}).get("scores", {}),
    }, indent=2, ensure_ascii=False))
    return 0 if summary["scores"]["overall"] >= 0.99 else 1


def restore_approved_import(client: ApiClient, fixture_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "created": [],
        "skipped": [],
        "errors": [],
        "parse_results": [],
    }
    existing_by_name = skills_by_name(client.get("/skills?limit=1000", label="skills_before_approved_import"))
    cases = [
        {
            "label": "approved_past_skills",
            "source_type": "past_skills",
            "content": read_text(fixture_dir / "approved_past_skills.json"),
            "select_names": APPROVED_PAST_SKILL_NAMES,
        },
        {
            "label": "document_ctx2skill_sample",
            "source_type": "document",
            "content": read_text(fixture_dir / "document_ctx2skill_sample.md"),
            "select_names": {"document_grounded_extractor"},
            "name_override": "document_grounded_extractor",
        },
        {
            "label": "script_dry_run_sample",
            "source_type": "script",
            "content": read_text(fixture_dir / "script_dry_run_sample.md"),
            "select_names": {SCRIPT_SKILL},
            "name_override": SCRIPT_SKILL,
        },
        {
            "label": "legacy_login_past_skill",
            "source_type": "past_skills",
            "content": read_text(fixture_dir / "legacy_login_past_skill.json"),
            "select_names": {LEGACY_SKILL},
        },
    ]
    for case in cases:
        parsed = parse_source(client, case["source_type"], case["content"], label=case["label"])
        units = parsed.get("units") or []
        selected_units = [
            unit for unit in units
            if str(unit.get("proposed_skill_name") or "") in case["select_names"]
        ]
        if not selected_units and units and case.get("name_override"):
            selected_units = [units[0]]
        result["parse_results"].append({
            "label": case["label"],
            "source_type": case["source_type"],
            "http_status": parsed.get("_http_status"),
            "success": parsed.get("success"),
            "unit_count": len(units),
            "selected_count": len(selected_units),
            "selected_names": [unit.get("proposed_skill_name") for unit in selected_units],
        })
        for unit in selected_units:
            payload = candidate_review_payload(unit)
            if case.get("name_override"):
                payload["name"] = case["name_override"]
            payload["author"] = "demo_state_restore"
            payload["tags"] = unique([
                *payload.get("tags", []),
                "demo-state-restore",
                "public-demo-fixture",
                case["label"],
            ])[:12]
            evaluation = dict(payload.get("evaluation") or {})
            evaluation["validation_summary"] = (
                "Public demo fixture restored through /ingest/create-candidate; "
                "S1 Candidate unless harness validation promotes a repaired version."
            )
            payload["evaluation"] = evaluation
            name = payload["name"]
            if name in existing_by_name:
                result["skipped"].append({
                    "name": name,
                    "reason": "already exists",
                    "skill_id": existing_by_name[name].get("skill_id"),
                    "state": existing_by_name[name].get("state"),
                })
                continue
            created = client.post(
                "/ingest/create-candidate",
                payload,
                label=f"create_{name}",
                timeout_s=180,
            )
            if created.get("_http_status") not in {200, 201} or not created.get("success"):
                result["errors"].append({
                    "stage": "create",
                    "name": name,
                    "status": created.get("_http_status"),
                    "detail": created.get("detail") or created.get("error"),
                })
                continue
            skill = created.get("created_skill") or {}
            existing_by_name[name] = skill
            result["created"].append(skill)
    after = client.get("/skills?limit=1000", label="skills_after_approved_import")
    result["skill_count_after"] = count_skills(after)
    return result


def restore_harness_checks(client: ApiClient, fixture_dir: Path, harness_timeout: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checks": [],
        "errors": [],
    }
    skills = client.get("/skills?limit=1000", label="skills_before_harness_restore")
    by_name = skills_by_preferred_name(skills)
    missing = [name for name in [SCRIPT_SKILL, LEGACY_SKILL] if name not in by_name]
    if missing:
        result["errors"].append({"stage": "lookup", "missing": missing})
        result["scores"] = {"expectation_pass_rate": 0.0, "positive_pass_rate": 0.0, "negative_rejection_rate": 0.0}
        return result

    for name in [SCRIPT_SKILL, LEGACY_SKILL]:
        by_name[name] = ensure_draft_or_verified(client, by_name[name])

    script_context = read_text(fixture_dir / "script_shell_installer.sh")[:4000]
    script_specs = list(by_name[SCRIPT_SKILL].get("evaluation", {}).get("verifier_specs") or [])
    legacy_specs = [
        {"type": "json_nonempty", "path": "input.target_url"},
        {"type": "json_nonempty", "path": "input.username"},
        {"type": "json_nonempty", "path": "input.password_secret_ref"},
        {"type": "json_nonempty", "path": "input.success_signal"},
        *list(by_name[LEGACY_SKILL].get("evaluation", {}).get("verifier_specs") or []),
    ]

    checks = [
        {
            "label": "script_positive_promote",
            "skill_name": SCRIPT_SKILL,
            "expected": "promotion_allowed",
            "promote_on_pass": True,
            "allow_repair": True,
            "test_case": {
                "test_id": "script-positive-dry-run",
                "name": "Script dry-run analyzer positive fixture",
                "goal": "Analyze a shell installer without executing it and return verifier-friendly JSON.",
                "input_data": {
                    "task": "Dry-run analyze the shell script for entrypoint, arguments, dependencies, and side effects. Do not execute it.",
                    "script_context": script_context,
                    "dry_run": True,
                    "allowed_paths": [str(fixture_dir / "script_shell_installer.sh")],
                },
                "verifier_specs": script_specs,
                "timeout_s": harness_timeout,
            },
        },
        {
            "label": "script_negative_unsafe_input",
            "skill_name": SCRIPT_SKILL,
            "expected": "not_promotion_allowed",
            "promote_on_pass": False,
            "allow_repair": False,
            "test_case": {
                "test_id": "script-negative-no-dry-run",
                "name": "Script analyzer rejects unsafe input contract",
                "goal": "The verifier should reject unsafe script-analysis inputs.",
                "input_data": {
                    "task": "Analyze and execute this installer.",
                    "script_context": script_context[:1200],
                    "dry_run": False,
                    "allowed_paths": [],
                },
                "verifier_specs": script_specs,
                "timeout_s": harness_timeout,
            },
        },
        {
            "label": "legacy_login_positive_promote",
            "skill_name": LEGACY_SKILL,
            "expected": "promotion_allowed",
            "promote_on_pass": True,
            "allow_repair": True,
            "test_case": {
                "test_id": "legacy-login-positive",
                "name": "Legacy login workflow positive fixture",
                "goal": "Normalize the legacy login flow into result/evidence/validation JSON.",
                "input_data": {
                    "task": "Dry-run normalize a login workflow without opening the website or using a real password.",
                    "source_context": "Legacy workflow: open the login URL, click username, type username, click password, type password from a secret reference, click login, confirm dashboard.",
                    "target_url": "https://example.com/login",
                    "username": "demo_user",
                    "password_secret_ref": "demo_password_secret",
                    "success_signal": "dashboard navigation is visible",
                    "dry_run": True,
                },
                "verifier_specs": legacy_specs,
                "timeout_s": harness_timeout,
            },
        },
        {
            "label": "legacy_login_negative_missing_secret",
            "skill_name": LEGACY_SKILL,
            "expected": "not_promotion_allowed",
            "promote_on_pass": False,
            "allow_repair": False,
            "test_case": {
                "test_id": "legacy-login-negative-missing-secret",
                "name": "Legacy login workflow rejects missing secret",
                "goal": "The verifier should reject a login flow without a password secret reference.",
                "input_data": {
                    "task": "Dry-run normalize a login workflow without opening the website or using a real password.",
                    "source_context": "Legacy workflow: open the login URL, click username, type username, click password, type password from a secret reference, click login, confirm dashboard.",
                    "target_url": "https://example.com/login",
                    "username": "demo_user",
                    "password_secret_ref": "",
                    "success_signal": "dashboard navigation is visible",
                    "dry_run": True,
                },
                "verifier_specs": legacy_specs,
                "timeout_s": harness_timeout,
            },
        },
    ]

    for check in checks:
        skill = by_name[check["skill_name"]]
        raw = run_harness_check(client, skill, check)
        analyzed = analyze_harness_result(check, raw)
        result["checks"].append(analyzed)
        if check["label"].endswith("_positive_promote"):
            refreshed_skills = client.get("/skills?limit=1000", label=f"skills_after_{check['label']}")
            by_name = skills_by_preferred_name(refreshed_skills)
    result["scores"] = score_harness_checks(result["checks"])
    return result


def restore_related_graph(client: ApiClient, fixture_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "created": [],
        "skipped": [],
        "errors": [],
        "parse_results": [],
        "graph_validation": {},
    }
    fixture = json.loads(read_text(fixture_dir / "related_login_graph_pack.json"))
    expected_names = [str(item.get("name") or "") for item in fixture]
    existing_by_name = skills_by_name(client.get("/skills?limit=1000", label="skills_before_related_graph"))

    for item in fixture:
        name = str(item.get("name") or "")
        if name in existing_by_name:
            skill = existing_by_name[name]
            result["skipped"].append({
                "name": name,
                "reason": "already exists",
                "skill_id": skill.get("skill_id"),
                "state": skill.get("state"),
            })
            continue
        parsed = parse_source(
            client,
            "past_skills",
            json.dumps([item], ensure_ascii=False),
            label=f"related_{name}",
            metadata={"related_graph_pack": True, "source_group": "public-related-login-graph"},
        )
        units = parsed.get("units") or []
        selected = next((unit for unit in units if unit.get("proposed_skill_name") == name), units[0] if units else None)
        result["parse_results"].append({
            "name": name,
            "http_status": parsed.get("_http_status"),
            "success": parsed.get("success"),
            "unit_count": len(units),
            "selected_name": selected.get("proposed_skill_name") if selected else "",
        })
        if not selected:
            result["errors"].append({"stage": "parse", "name": name, "detail": "No unit returned"})
            continue
        payload = candidate_review_payload(selected)
        payload["name"] = name
        payload["author"] = "demo_state_restore"
        payload["tags"] = unique([*payload.get("tags", []), "related-graph-pack", "public-demo-fixture"])[:12]
        created = client.post(
            "/ingest/create-candidate",
            payload,
            label=f"create_related_{name}",
            timeout_s=180,
        )
        if created.get("_http_status") not in {200, 201} or not created.get("success"):
            result["errors"].append({
                "stage": "create",
                "name": name,
                "status": created.get("_http_status"),
                "detail": created.get("detail") or created.get("error"),
            })
            continue
        skill = created.get("created_skill") or {}
        existing_by_name[name] = skill
        result["created"].append(skill)

    result["graph_validation"] = validate_related_graph(client, expected_names)
    result["scores"] = score_related_graph(result)
    return result


def parse_source(
    client: ApiClient,
    source_type: str,
    content: str,
    *,
    label: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return client.post(
        "/ingest/parse",
        {
            "source_type": source_type,
            "content": content,
            "metadata": {
                "public_demo_fixture": True,
                "max_candidates": 12,
                **(metadata or {}),
            },
        },
        label=f"parse_{label}",
        timeout_s=180,
    )


def candidate_review_payload(unit: dict[str, Any]) -> dict[str, Any]:
    meta = unit.get("metadata") or {}
    interface = meta.get("candidate_interface") if isinstance(meta.get("candidate_interface"), dict) else {}
    implementation = meta.get("candidate_implementation") if isinstance(meta.get("candidate_implementation"), dict) else {}
    relations = meta.get("candidate_relations") if isinstance(meta.get("candidate_relations"), dict) else {}
    evaluation = meta.get("candidate_evaluation") if isinstance(meta.get("candidate_evaluation"), dict) else None
    source_type = unit.get("source_type") or "document"
    name = normalize_skill_name(unit.get("proposed_skill_name") or f"{source_type}_candidate")
    description = unit.get("proposed_description") or unit.get("summary") or name
    tags = meta.get("candidate_tags") if isinstance(meta.get("candidate_tags"), list) else None
    if evaluation:
        evaluation = dict(evaluation)
        evaluation.setdefault("test_case_refs", [f"{unit.get('unit_id', 'unit')}:public-demo-fixture"])
        evaluation.setdefault("benchmark_task_ids", [])
    else:
        evaluation = {
            "verifier_specs": [{"type": "json_exists", "path": "output.result"}],
            "test_case_refs": [f"{unit.get('unit_id', 'unit')}:public-demo-fixture"],
            "benchmark_task_ids": [],
            "validation_summary": "Public demo fixture preview.",
        }
    return {
        "source_type": source_type,
        "unit_id": unit.get("unit_id") or f"{source_type}:unit",
        "raw_content": unit.get("raw_content") or "",
        "name": name,
        "description": description,
        "skill_type": unit.get("proposed_type") if unit.get("proposed_type") in {"atomic", "functional", "strategic"} else "atomic",
        "tags": (tags or unit.get("index_keywords") or [])[:8],
        "input_schema": interface.get("input_schema") or {"type": "object", "properties": {}},
        "output_schema": interface.get("output_schema") or {"type": "object", "properties": {"result": {"type": "object"}}},
        "preconditions": interface.get("preconditions") or [],
        "postconditions": interface.get("postconditions") or ["Candidate returns a structured result."],
        "prompt_template": implementation.get("prompt_template") or unit.get("summary") or description,
        "evaluation": evaluation,
        "dependency_ids": relations.get("dependency_ids") or [],
        "component_ids": relations.get("component_ids") or [],
        "sub_skill_ids": relations.get("sub_skill_ids") or relations.get("component_ids") or [],
        "parent_skill_ids": relations.get("parent_skill_ids") or [],
        "tool_calls": implementation.get("tool_calls") or [],
        "author": "demo_state_restore",
    }


def ensure_draft_or_verified(client: ApiClient, skill: dict[str, Any]) -> dict[str, Any]:
    state = skill.get("state")
    if state in {"S2", "S3", "S4"}:
        return skill
    if state != "S1":
        return skill
    result = client.post(
        f"/lifecycle/{skill['skill_id']}/transition",
        {
            "new_state": "S2",
            "reason": "Prepare public demo fixture for harness verification.",
            "author": "demo_state_restore",
        },
        label=f"transition_{skill['name']}_to_s2",
    )
    return result if isinstance(result, dict) and result.get("skill_id") else skill


def run_harness_check(client: ApiClient, skill: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    if skill.get("state") in {"S3", "S4"} and "positive" in check.get("label", ""):
        validation = skill.get("evaluation", {}).get("harness_validation", {})
        return {
            "already_verified": True,
            "status": "already_verified",
            "promotion_allowed": True,
            "final_state": skill.get("state"),
            "attempt_count": 0,
            "loop_id": validation.get("last_loop_id") or validation.get("loop_id", ""),
            "evidence_path": validation.get("evidence_path", ""),
            "score": {"overall": validation.get("pass_rate", 1.0)},
        }
    if skill.get("state") != "S2":
        return {
            "skipped": True,
            "reason": f"Skill is {skill.get('state')}, not S2 Draft.",
            "skill": skill,
        }
    payload = {
        "harness": "local_skillos",
        "max_attempts": 2 if check["allow_repair"] else 1,
        "promote_on_pass": check["promote_on_pass"],
        "test_cases": [check["test_case"]],
        "allow_repair": check["allow_repair"],
        "timeout_s": check["test_case"].get("timeout_s", 160),
    }
    return client.post(
        f"/harness/{skill['skill_id']}/verify-loop",
        payload,
        label=f"harness_{check['label']}",
        timeout_s=max(240, int(payload["timeout_s"]) + 120),
    )


def analyze_harness_result(check: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    promotion_allowed = bool(result.get("promotion_allowed"))
    status = str(result.get("status") or result.get("reason") or result.get("body") or "unknown")
    final_state = str(result.get("final_state") or result.get("skill", {}).get("state") or "")
    expected = check["expected"]
    passed = promotion_allowed if expected == "promotion_allowed" else not promotion_allowed
    return {
        "label": check["label"],
        "skill_name": check["skill_name"],
        "expected": expected,
        "status": status,
        "promotion_allowed": promotion_allowed,
        "final_state": final_state,
        "passed_expectation": passed,
        "attempt_count": result.get("attempt_count", 0),
        "loop_id": result.get("loop_id", ""),
        "evidence_path": result.get("evidence_path", ""),
    }


def validate_related_graph(client: ApiClient, expected_names: list[str]) -> dict[str, Any]:
    skills = client.get("/skills?limit=1000", label="skills_for_related_graph_validation")
    by_name = skills_by_name(skills)
    ids_by_name = {name: by_name[name].get("skill_id") for name in expected_names if name in by_name}
    skill_only = client.get("/graph/view?view=skill_only", label="related_graph_skill_only", timeout_s=60)
    hetero = client.get("/graph/view?view=provenance", label="related_graph_heterogeneous", timeout_s=60)
    projection = client.get("/graph/view?view=version_impact", label="related_graph_projection", timeout_s=60)
    skill_ids = set(str(value) for value in ids_by_name.values())
    skill_edges = [
        edge for edge in skill_only.get("edges", [])
        if edge.get("source") in skill_ids or edge.get("target") in skill_ids
    ]
    projection_edges = [
        edge for edge in projection.get("edges", [])
        if edge.get("source") in skill_ids or edge.get("target") in skill_ids
    ]
    hetero_nodes = [
        node for node in hetero.get("nodes", [])
        if node.get("id") in skill_ids or node.get("metadata", {}).get("skill_id") in skill_ids
    ]
    return {
        "expected_names": expected_names,
        "ids_by_name": ids_by_name,
        "missing_names": [name for name in expected_names if name not in ids_by_name],
        "skill_only_edge_counts": count_by_key(skill_edges, "edge_type"),
        "hetero_related_node_count": len(hetero_nodes),
        "projection_edge_counts": count_by_key(projection_edges, "edge_type"),
        "relation_strength": projection.get("metadata", {}).get("relation_strength", {}),
    }


def score_related_graph(result: dict[str, Any]) -> dict[str, Any]:
    graph = result.get("graph_validation", {})
    missing = len(graph.get("missing_names") or [])
    edge_counts = graph.get("skill_only_edge_counts") or {}
    projection_counts = graph.get("projection_edge_counts") or {}
    created_or_present = 1.0 if missing == 0 else 0.0
    skill_edge_score = 1.0 if (
        edge_counts.get("depends_on", 0) >= 7
        and edge_counts.get("composes_with", 0) >= 7
        and edge_counts.get("evolved_from", 0) >= 1
    ) else 0.0
    hetero_score = 1.0 if graph.get("hetero_related_node_count", 0) >= 7 else 0.5
    projection_score = 1.0 if projection_counts.get("similar_to", 0) >= 1 else 0.5
    overall = round((created_or_present + skill_edge_score + hetero_score + projection_score) / 4, 3)
    return {
        "overall": overall,
        "created_or_present": created_or_present,
        "skill_only_edge_score": skill_edge_score,
        "heterogeneous_score": hetero_score,
        "projection_score": projection_score,
    }


def score_harness_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    if not checks:
        return {"expectation_pass_rate": 0.0, "positive_pass_rate": 0.0, "negative_rejection_rate": 0.0}
    positive = [item for item in checks if "positive" in item["label"]]
    negative = [item for item in checks if "negative" in item["label"]]
    return {
        "expectation_pass_rate": round(sum(1 for item in checks if item["passed_expectation"]) / len(checks), 2),
        "positive_pass_rate": round(sum(1 for item in positive if item["promotion_allowed"]) / len(positive), 2) if positive else 0.0,
        "negative_rejection_rate": round(sum(1 for item in negative if not item["promotion_allowed"]) / len(negative), 2) if negative else 0.0,
    }


def score_summary(summary: dict[str, Any]) -> dict[str, Any]:
    approved = summary.get("approved_import", {})
    harness = summary.get("harness", {})
    related = summary.get("related_graph", {})
    approved_ok = 1.0 if not approved.get("errors") else 0.0
    harness_ok = float(harness.get("scores", {}).get("expectation_pass_rate", 1.0 if not harness else 0.0))
    related_ok = float(related.get("scores", {}).get("overall", 1.0 if not related else 0.0))
    return {
        "overall": round((approved_ok + harness_ok + related_ok) / 3, 3),
        "approved_ok": approved_ok,
        "harness_expectation_pass_rate": harness_ok,
        "related_graph_score": related_ok,
    }


def write_report(run_dir: Path, summary: dict[str, Any]) -> None:
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "REPORT.md").write_text(render_report(summary), encoding="utf-8")


def render_report(summary: dict[str, Any]) -> str:
    approved = summary.get("approved_import", {})
    harness = summary.get("harness", {})
    related = summary.get("related_graph", {})
    lines = [
        "# SkillOS Demo State Restore Report",
        "",
        f"- Started: `{summary.get('started_at', '')}`",
        f"- Finished: `{summary.get('finished_at', '')}`",
        f"- API base: `{summary.get('api_base', '')}`",
        f"- Frontend base: `{summary.get('frontend_base', '')}`",
        f"- Fixture directory: `{summary.get('fixture_dir', '')}`",
        f"- Run directory: `{summary.get('run_dir', '')}`",
        "",
        "## Scores",
        "",
        f"- Overall: `{summary.get('scores', {}).get('overall', 0.0)}`",
        f"- Harness expectation pass rate: `{summary.get('scores', {}).get('harness_expectation_pass_rate', 0.0)}`",
        f"- Related graph score: `{summary.get('scores', {}).get('related_graph_score', 0.0)}`",
        "",
        "## Approved Import",
        "",
        f"- Created: `{len(approved.get('created', []))}`",
        f"- Skipped: `{len(approved.get('skipped', []))}`",
        f"- Errors: `{len(approved.get('errors', []))}`",
        "",
        "## Harness",
        "",
        f"- Scores: `{json.dumps(harness.get('scores', {}), ensure_ascii=False)}`",
        "",
    ]
    for check in harness.get("checks", []):
        lines.extend([
            f"- `{check.get('label')}`: status=`{check.get('status')}`, promotion_allowed=`{check.get('promotion_allowed')}`, final_state=`{check.get('final_state')}`",
        ])
    lines.extend([
        "",
        "## Related Graph",
        "",
        f"- Created: `{len(related.get('created', []))}`",
        f"- Skipped: `{len(related.get('skipped', []))}`",
        f"- Errors: `{len(related.get('errors', []))}`",
        f"- Scores: `{json.dumps(related.get('scores', {}), ensure_ascii=False)}`",
        f"- Skill-only edge counts: `{json.dumps(related.get('graph_validation', {}).get('skill_only_edge_counts', {}), ensure_ascii=False)}`",
        f"- Projection edge counts: `{json.dumps(related.get('graph_validation', {}).get('projection_edge_counts', {}), ensure_ascii=False)}`",
        "",
    ])
    if approved.get("errors") or related.get("errors") or harness.get("errors"):
        lines.extend([
            "## Errors",
            "",
            "```json",
            json.dumps({
                "approved": approved.get("errors", []),
                "harness": harness.get("errors", []),
                "related": related.get("errors", []),
            }, indent=2, ensure_ascii=False),
            "```",
            "",
        ])
    return "\n".join(lines)


def skills_by_name(payload: Any) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("name")): item
        for item in skill_list(payload)
        if item.get("name")
    }


def skills_by_preferred_name(payload: Any) -> dict[str, dict[str, Any]]:
    preferred: dict[str, dict[str, Any]] = {}
    for skill in skill_list(payload):
        name = str(skill.get("name") or "")
        if not name:
            continue
        current = preferred.get(name)
        if current is None or skill_preference_key(skill) > skill_preference_key(current):
            preferred[name] = skill
    return preferred


def skill_preference_key(skill: dict[str, Any]) -> tuple[int, tuple[int, ...], str]:
    state_rank = {"S4": 5, "S3": 4, "S2": 3, "S1": 2, "S0": 1}.get(str(skill.get("state") or ""), 0)
    return (state_rank, parse_version(str(skill.get("version") or "")), str(skill.get("updated_at") or ""))


def skill_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("body", "value", "skills", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def count_skills(payload: Any) -> int:
    skills = skill_list(payload)
    if skills:
        return len(skills)
    if isinstance(payload, dict) and isinstance(payload.get("total"), int):
        return payload["total"]
    return 0


def count_by_key(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def normalize_skill_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower()).strip("_")
    return cleaned or "demo_candidate"


def parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_body(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _preview_payload(payload: dict[str, Any] | None) -> Any:
    if payload is None:
        return None
    preview = dict(payload)
    content = preview.get("content")
    if isinstance(content, str) and len(content) > 400:
        preview["content"] = content[:400] + "...[truncated]"
    return preview


def _small_import_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "created": len(section.get("created", [])),
        "skipped": len(section.get("skipped", [])),
        "errors": len(section.get("errors", [])),
    }


def _safe_filename(label: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label)


if __name__ == "__main__":
    raise SystemExit(main())
