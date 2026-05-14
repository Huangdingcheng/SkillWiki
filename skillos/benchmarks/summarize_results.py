"""Summarize SkillOS demo benchmark results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


MODES = ("no_skill", "raw_prompt", "with_skill")


def summarize_results(payload: Dict[str, Any]) -> Dict[str, Any]:
    results = payload.get("results", [])
    by_task: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for item in results:
        task_id = str(item.get("task_id", ""))
        mode = str(item.get("mode", ""))
        if not task_id or not mode:
            continue
        by_task.setdefault(task_id, {})[mode] = item

    rows: List[Dict[str, Any]] = []
    for task_id in sorted(by_task):
        modes = by_task[task_id]
        row: Dict[str, Any] = {"task_id": task_id}
        failure_reasons = []
        for mode in MODES:
            item = modes.get(mode, {})
            status = str(item.get("status", "missing"))
            row[mode] = status
            reason = item.get("failure_reason")
            if reason:
                failure_reasons.append(f"{mode}: {reason}")
        row["winner"] = _winner(modes)
        row["failure_reason"] = "; ".join(failure_reasons)
        rows.append(row)

    mode_totals = {
        mode: {
            "success": sum(1 for row in rows if row.get(mode) == "success"),
            "total": len(rows),
            "success_rate": (
                sum(1 for row in rows if row.get(mode) == "success") / len(rows)
                if rows
                else 0.0
            ),
        }
        for mode in MODES
    }

    return {
        "task_count": len(rows),
        "mode_totals": mode_totals,
        "rows": rows,
    }


def to_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# SkillOS Demo Benchmark Summary",
        "",
        "| task_id | no_skill | raw_prompt | with_skill | winner | failure reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary.get("rows", []):
        lines.append(
            "| {task_id} | {no_skill} | {raw_prompt} | {with_skill} | {winner} | {failure_reason} |".format(
                task_id=_cell(row.get("task_id", "")),
                no_skill=_cell(row.get("no_skill", "")),
                raw_prompt=_cell(row.get("raw_prompt", "")),
                with_skill=_cell(row.get("with_skill", "")),
                winner=_cell(row.get("winner", "")),
                failure_reason=_cell(row.get("failure_reason", "")),
            )
        )
    lines.extend(["", "## Mode Totals", ""])
    for mode, data in summary.get("mode_totals", {}).items():
        lines.append(
            f"- `{mode}`: {data.get('success', 0)}/{data.get('total', 0)} "
            f"({data.get('success_rate', 0.0):.2%})"
        )
    lines.append("")
    return "\n".join(lines)


def write_summary(summary: Dict[str, Any], output_dir: Path) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest_summary.json"
    md_path = output_dir / "latest_summary.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(summary), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _winner(modes: Dict[str, Dict[str, Any]]) -> str:
    successful = [
        mode
        for mode in MODES
        if modes.get(mode, {}).get("status") == "success"
    ]
    if not successful:
        return "none"
    if "with_skill" in successful:
        return "with_skill"
    return successful[0]


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "results")
    args = parser.parse_args(list(argv) if argv is not None else None)

    summary = summarize_results(_load_json(args.result_file))
    paths = write_summary(summary, args.output_dir)
    print(json.dumps(paths, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
