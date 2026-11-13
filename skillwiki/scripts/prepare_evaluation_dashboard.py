"""Generate the Evaluation page's local fixture checks without model calls.

Existing benchmark runners retain their own task sets, scores and output schemas.
This command never overwrites previously generated or imported results.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New results directory; must be empty or absent.")
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory is not empty; choose a new directory to preserve previous evidence.")
    output.mkdir(parents=True, exist_ok=True)
    commands = [
        ["run_demo_benchmark.py", "--output", str(output / "demo_benchmark_latest.json")],
        ["run_search_eval.py", "--output", str(output / "search_eval_latest.json")],
        ["run_llm_planner_eval.py", "--mode", "fallback", "--output", str(output / "llm_eval_latest.json")],
    ]
    context = {
        "evidence_kind": "local_fixture_checks", "status": "running", "model_calls": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Demo fixtures, local lexical/hybrid search and fallback planner only.",
        "runs": [],
    }
    context_file = output / "dashboard_context.json"
    context_file.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    for command in commands:
        invocation = [sys.executable, str(BACKEND_ROOT / "benchmarks" / command[0]), *command[1:]]
        run = subprocess.run(invocation, cwd=BACKEND_ROOT, capture_output=True, text=True)
        (output / (command[0] + ".stdout.txt")).write_text(run.stdout, encoding="utf-8")
        (output / (command[0] + ".stderr.txt")).write_text(run.stderr, encoding="utf-8")
        context["runs"].append({"runner": command[0], "arguments": command[1:], "exit_code": run.returncode})
        context["status"] = "failed" if run.returncode else "running"
        context_file.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
        if run.returncode:
            print(run.stderr, file=sys.stderr)
            return run.returncode
    context["status"] = "completed"
    context_file.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"results_dir": str(output), "model_calls": 0, "status": "completed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
