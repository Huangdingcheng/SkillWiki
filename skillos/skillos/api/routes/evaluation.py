"""Evaluation artifact routes for paper-demo evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

RESULTS_DIR = Path(__file__).resolve().parents[3] / "benchmarks" / "results"
DEMO_SUMMARY_FILE = "latest_summary.json"
DEMO_RESULTS_FILE = "demo_benchmark_latest.json"
SEARCH_EVAL_FILE = "search_eval_latest.json"
LLM_EVAL_FILE = "llm_eval_latest.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    except OSError:
        return None


def _read_json(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not path.exists():
        return None, f"Missing evaluation artifact: {path.name}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON in {path.name}: {exc}"
    if not isinstance(raw, dict):
        return None, f"Unexpected JSON root in {path.name}: expected object"
    return raw, None


def _artifact_base(source_file: str, payload: Optional[Dict[str, Any]], warning: Optional[str]) -> Dict[str, Any]:
    path = RESULTS_DIR / source_file
    return {
        "available": payload is not None,
        "source_file": source_file,
        "generated_at": payload.get("generated_at") if payload else None,
        "updated_at": _mtime_iso(path) if path.exists() else None,
        "error": warning,
    }


def _mode_totals(summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not summary:
        return {}
    totals = summary.get("mode_totals", {})
    return totals if isinstance(totals, dict) else {}


def _index_results_by_task(results: Iterable[Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    indexed: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        mode = str(item.get("mode") or "").strip()
        if not task_id or not mode:
            continue
        indexed.setdefault(task_id, {})[mode] = item
    return indexed


def _first_domain(group: Dict[str, Dict[str, Any]]) -> str:
    for item in group.values():
        domain = item.get("domain")
        if isinstance(domain, str) and domain:
            return domain
    return "unknown"


def _latency(group: Dict[str, Dict[str, Any]], mode: str) -> Optional[float]:
    value = group.get(mode, {}).get("latency_ms")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _verifier(group: Dict[str, Dict[str, Any]], mode: str) -> Optional[bool]:
    value = group.get(mode, {}).get("verifier_passed")
    return value if isinstance(value, bool) else None


def _summarize_demo_artifact(
    summary_payload: Optional[Dict[str, Any]],
    result_payload: Optional[Dict[str, Any]],
    warning: Optional[str],
) -> Dict[str, Any]:
    artifact = _artifact_base(DEMO_SUMMARY_FILE, summary_payload, warning)
    summary = summary_payload or {}
    result_payload = result_payload or {}
    results = result_payload.get("results", [])
    result_index = _index_results_by_task(results if isinstance(results, list) else [])
    summary_rows = summary.get("rows", [])
    rows: List[Dict[str, Any]] = []
    if isinstance(summary_rows, list):
        for item in summary_rows:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "").strip()
            group = result_index.get(task_id, {})
            rows.append(
                {
                    "task_id": task_id,
                    "domain": _first_domain(group),
                    "no_skill": item.get("no_skill"),
                    "raw_prompt": item.get("raw_prompt"),
                    "with_skill": item.get("with_skill"),
                    "winner": item.get("winner"),
                    "failure_reason": item.get("failure_reason"),
                    "no_skill_latency_ms": _latency(group, "no_skill"),
                    "raw_prompt_latency_ms": _latency(group, "raw_prompt"),
                    "with_skill_latency_ms": _latency(group, "with_skill"),
                    "no_skill_verifier_passed": _verifier(group, "no_skill"),
                    "raw_prompt_verifier_passed": _verifier(group, "raw_prompt"),
                    "with_skill_verifier_passed": _verifier(group, "with_skill"),
                }
            )
    artifact.update(
        {
            "task_count": summary.get("task_count") or result_payload.get("task_count") or len(rows),
            "mode_totals": _mode_totals(summary),
            "rows": rows,
            "raw_result_file": DEMO_RESULTS_FILE if result_payload else None,
        }
    )
    return artifact


def _top_skill(row: Dict[str, Any]) -> Optional[str]:
    results = row.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            skill_id = first.get("skill_id")
            return str(skill_id) if skill_id is not None else None
    return None


def _rank(row: Dict[str, Any]) -> Optional[int]:
    value = row.get("best_rank")
    return int(value) if isinstance(value, int) else None


def _hit(row: Dict[str, Any], key: str) -> Optional[bool]:
    value = row.get(key)
    return value if isinstance(value, bool) else None


def _summarize_search_artifact(payload: Optional[Dict[str, Any]], warning: Optional[str]) -> Dict[str, Any]:
    artifact = _artifact_base(SEARCH_EVAL_FILE, payload, warning)
    payload = payload or {}
    raw_rows = payload.get("results", [])
    rows: List[Dict[str, Any]] = []
    if isinstance(raw_rows, list):
        for item in raw_rows:
            if not isinstance(item, dict):
                continue
            lexical = item.get("lexical") if isinstance(item.get("lexical"), dict) else item
            hybrid = item.get("hybrid") if isinstance(item.get("hybrid"), dict) else {}
            expected = item.get("expected_skill_ids")
            rows.append(
                {
                    "query_id": item.get("query_id"),
                    "query": item.get("query"),
                    "domain": item.get("domain"),
                    "expected_skill_ids": expected if isinstance(expected, list) else [],
                    "lexical_top_skill": _top_skill(lexical),
                    "hybrid_top_skill": _top_skill(hybrid),
                    "lexical_best_rank": _rank(lexical),
                    "hybrid_best_rank": _rank(hybrid),
                    "lexical_top1_hit": _hit(lexical, "top1_hit"),
                    "hybrid_top1_hit": _hit(hybrid, "top1_hit"),
                    "hybrid_topk_hit": _hit(item, "hybrid_topk_hit"),
                }
            )
    artifact.update(
        {
            "benchmark": payload.get("benchmark"),
            "schema_version": payload.get("schema_version"),
            "query_count": payload.get("query_count") or len(rows),
            "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
            "comparison": payload.get("comparison") if isinstance(payload.get("comparison"), dict) else {},
            "rows": rows,
        }
    )
    return artifact


def _summarize_llm_artifact(payload: Optional[Dict[str, Any]], warning: Optional[str]) -> Dict[str, Any]:
    artifact = _artifact_base(LLM_EVAL_FILE, payload, warning)
    payload = payload or {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = summary.get("rows", [])
    artifact.update(
        {
            "benchmark": payload.get("benchmark"),
            "task_count": summary.get("task_count") or payload.get("task_count") or 0,
            "mode_totals": _mode_totals(summary),
            "rows": rows if isinstance(rows, list) else [],
        }
    )
    return artifact


@router.get("/latest", response_model=Dict[str, Any])
async def get_latest_evaluation() -> Dict[str, Any]:
    """Return the latest local benchmark artifacts in a UI-friendly shape."""
    warnings: List[str] = []
    demo_summary, demo_warning = _read_json(RESULTS_DIR / DEMO_SUMMARY_FILE)
    demo_results, demo_results_warning = _read_json(RESULTS_DIR / DEMO_RESULTS_FILE)
    search_eval, search_warning = _read_json(RESULTS_DIR / SEARCH_EVAL_FILE)
    llm_eval, llm_warning = _read_json(RESULTS_DIR / LLM_EVAL_FILE)

    for warning in [demo_warning, demo_results_warning, search_warning, llm_warning]:
        if warning:
            warnings.append(warning)

    return {
        "generated_at": _now_iso(),
        "results_dir_present": RESULTS_DIR.exists(),
        "warnings": warnings,
        "artifacts": {
            "demo_benchmark": _summarize_demo_artifact(demo_summary, demo_results, demo_warning),
            "search_eval": _summarize_search_artifact(search_eval, search_warning),
            "llm_planner": _summarize_llm_artifact(llm_eval, llm_warning),
        },
    }


@router.get("/dashboard", response_model=Dict[str, Any])
async def get_evaluation_dashboard() -> Dict[str, Any]:
    """Compatibility alias for the frontend evaluation dashboard."""
    return await get_latest_evaluation()
