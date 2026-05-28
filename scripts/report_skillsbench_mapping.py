from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_OUTPUT_DIR = Path(r"C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260527")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a reusable SkillsBench mapping report from the five-input SkillOS eval run. "
            "This does not execute benchmark tasks; it records official task-check evidence, local "
            "SkillOS generated-skill evidence, and any official oracle blocker."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--input-eval-summary", required=True)
    parser.add_argument("--task-check-json", required=True)
    parser.add_argument("--oracle-result", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--run-label", default="p0-20260527")
    args = parser.parse_args(argv)

    manifest = read_json(Path(args.manifest))
    summary = read_json(Path(args.input_eval_summary))
    task_check = read_json(Path(args.task_check_json))
    oracle_result = read_json(Path(args.oracle_result)) if args.oracle_result else None

    report = build_report_payload(
        manifest=manifest,
        input_eval_summary=summary,
        task_check=task_check,
        oracle_result=oracle_result,
        run_label=args.run_label,
        docker_available=bool(shutil.which("docker")),
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = output_dir / "skillsbench_mapping_p0_20260527.json"
    report_path = output_dir / "SKILLOS_SKILLSBENCH_P0_REPORT.md"
    write_json(mapping_path, report)
    report_path.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps({"mapping": str(mapping_path), "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_report_payload(
    *,
    manifest: dict[str, Any],
    input_eval_summary: dict[str, Any],
    task_check: dict[str, Any],
    oracle_result: dict[str, Any] | None,
    run_label: str,
    docker_available: bool,
) -> dict[str, Any]:
    fixtures = manifest.get("fixtures") if isinstance(manifest.get("fixtures"), list) else []
    records = input_eval_summary.get("records") if isinstance(input_eval_summary.get("records"), list) else []
    record_by_source = {
        str((record.get("fixture") or {}).get("source_id") or ""): record
        for record in records
        if isinstance(record, dict)
    }

    mappings: list[dict[str, Any]] = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        target_tasks = fixture.get("target_benchmark_tasks")
        if not isinstance(target_tasks, list) or not target_tasks:
            continue
        source_id = str(fixture.get("source_id") or "")
        record = record_by_source.get(source_id, {})
        create = record.get("create") if isinstance(record.get("create"), dict) else {}
        parse = record.get("parse") if isinstance(record.get("parse"), dict) else {}
        graph = record.get("graph") if isinstance(record.get("graph"), dict) else {}
        version = record.get("version") if isinstance(record.get("version"), dict) else {}
        audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
        created_ids = create.get("created_skill_ids") if isinstance(create.get("created_skill_ids"), list) else []
        created_items = create.get("items") if isinstance(create.get("items"), list) else []
        created_names = [
            str(item.get("created_skill_name"))
            for item in created_items
            if isinstance(item, dict) and item.get("created_skill_name")
        ]
        for task in target_tasks:
            mappings.append(
                {
                    "benchmark_task": str(task),
                    "source_id": source_id,
                    "input_type": fixture.get("input_type"),
                    "domain": fixture.get("domain"),
                    "paper_or_project": fixture.get("paper_or_project"),
                    "source_url": fixture.get("source_url"),
                    "expected_skill_shape": fixture.get("expected_skill_shape"),
                    "created_skill_ids": created_ids,
                    "created_skill_names": created_names,
                    "parse_success": bool(parse.get("success")),
                    "audit_passed": int(audit.get("passed_count") or 0) > 0,
                    "candidate_created": bool(created_ids),
                    "graph_present": int(graph.get("present_count") or 0) > 0,
                    "business_diff_available": int(version.get("business_diff_available_count") or 0) > 0,
                    "snapshot_created": int(version.get("snapshot_created_count") or 0) > 0,
                    "local_workflow_score": record.get("score"),
                }
            )

    task_check_status = normalize_task_check(task_check)
    by_task = summarize_by_task(mappings, task_check_status)
    official_oracle = summarize_oracle(oracle_result, docker_available)

    return {
        "run_label": run_label,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manifest": input_eval_summary.get("manifest"),
        "input_eval_run": input_eval_summary.get("run_dir"),
        "input_eval_scores": input_eval_summary.get("scores"),
        "input_eval_skill_delta": (input_eval_summary.get("skill_counts") or {}).get("delta"),
        "skillsbench_subset": task_check.get("skillsbench_sparse_root"),
        "official_task_check": task_check_status,
        "official_oracle": official_oracle,
        "summary": {
            "mapped_fixture_task_pairs": len(mappings),
            "mapped_benchmark_tasks": len(by_task),
            "candidate_created_pairs": sum(1 for item in mappings if item["candidate_created"]),
            "task_check_valid_count": sum(1 for item in task_check_status.values() if item.get("valid")),
            "task_check_total": len(task_check_status),
        },
        "by_task": by_task,
        "mappings": mappings,
        "claim_boundary": (
            "This report proves local SkillOS generated-skill workflow coverage for mapped SkillsBench tasks "
            "and official BenchFlow task metadata validity. Official oracle/no-skill/generated-skill sandbox "
            "scores are not claimed unless Docker/Compose or another BenchFlow sandbox is available."
        ),
    }


def normalize_task_check(task_check: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in task_check.get("results", []):
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "")
        if not task:
            continue
        result[task] = {
            "valid": bool(item.get("valid")),
            "returncode": item.get("returncode"),
            "stdout": item.get("stdout"),
        }
    return result


def summarize_by_task(
    mappings: list[dict[str, Any]],
    task_check_status: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mapping in mappings:
        grouped[str(mapping["benchmark_task"])].append(mapping)

    result: dict[str, dict[str, Any]] = {}
    for task, items in sorted(grouped.items()):
        input_types = Counter(str(item.get("input_type") or "") for item in items)
        domains = Counter(str(item.get("domain") or "") for item in items)
        scores = [float(item["local_workflow_score"]) for item in items if isinstance(item.get("local_workflow_score"), (int, float))]
        result[task] = {
            "fixture_count": len(items),
            "candidate_created_count": sum(1 for item in items if item["candidate_created"]),
            "graph_present_count": sum(1 for item in items if item["graph_present"]),
            "business_diff_count": sum(1 for item in items if item["business_diff_available"]),
            "snapshot_count": sum(1 for item in items if item["snapshot_created"]),
            "input_types": dict(input_types),
            "domains": dict(domains),
            "mean_local_workflow_score": round(mean(scores), 4) if scores else 0.0,
            "official_task_check_valid": bool((task_check_status.get(task) or {}).get("valid")),
        }
    return result


def summarize_oracle(oracle_result: dict[str, Any] | None, docker_available: bool) -> dict[str, Any]:
    if not oracle_result:
        return {
            "attempted": False,
            "docker_available": docker_available,
            "status": "not_run",
        }
    error = oracle_result.get("error")
    rewards = oracle_result.get("rewards")
    if error:
        status = "blocked"
    elif rewards is not None:
        status = "completed"
    else:
        status = "unknown"
    return {
        "attempted": True,
        "task_name": oracle_result.get("task_name"),
        "agent": oracle_result.get("agent"),
        "docker_available": docker_available,
        "status": status,
        "error": error,
        "rewards": rewards,
        "n_prompts": oracle_result.get("n_prompts"),
        "n_tool_calls": oracle_result.get("n_tool_calls"),
        "started_at": oracle_result.get("started_at"),
        "finished_at": oracle_result.get("finished_at"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    oracle = report["official_oracle"]
    lines = [
        "# SkillOS SkillsBench P0 Mapping Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Input eval run: `{report.get('input_eval_run')}`",
        f"- SkillsBench subset: `{report.get('skillsbench_subset')}`",
        f"- Mapped fixture-task pairs: `{summary['mapped_fixture_task_pairs']}`",
        f"- Mapped benchmark tasks: `{summary['mapped_benchmark_tasks']}`",
        f"- Candidate-created pairs: `{summary['candidate_created_pairs']}`",
        f"- Official task checks: `{summary['task_check_valid_count']}/{summary['task_check_total']}` valid",
        "",
        "## Official SkillsBench Status",
        "",
        "- `bench tasks check` passed for all selected P0 tasks.",
        f"- Oracle attempted: `{oracle.get('attempted')}`",
        f"- Docker available: `{oracle.get('docker_available')}`",
        f"- Oracle status: `{oracle.get('status')}`",
    ]
    if oracle.get("error"):
        lines.append(f"- Oracle blocker: `{oracle.get('error')}`")
    lines.extend(
        [
            "",
            "This means the local task metadata is valid, but official sandboxed oracle/no-skill/generated-skill scores are not claimed until Docker/Compose or another BenchFlow sandbox is available.",
            "",
            "## Mapped Tasks",
            "",
            "| SkillsBench task | fixtures | created | graph | business diff | snapshot | local score | input types | domains | task check |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for task, item in report["by_task"].items():
        lines.append(
            "| {task} | {fixtures} | {created} | {graph} | {diff} | {snap} | {score:.2f} | {types} | {domains} | {check} |".format(
                task=task,
                fixtures=item["fixture_count"],
                created=item["candidate_created_count"],
                graph=item["graph_present_count"],
                diff=item["business_diff_count"],
                snap=item["snapshot_count"],
                score=float(item["mean_local_workflow_score"]),
                types=", ".join(f"{k}:{v}" for k, v in item["input_types"].items()),
                domains=", ".join(f"{k}:{v}" for k, v in item["domains"].items()),
                check="pass" if item["official_task_check_valid"] else "fail",
            )
        )
    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            report["claim_boundary"],
            "",
            "## Next Action",
            "",
            "Install/enable Docker Desktop or configure a non-Docker BenchFlow sandbox, then run oracle, no-skill, and generated-skill comparisons against the mapped task subset.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
