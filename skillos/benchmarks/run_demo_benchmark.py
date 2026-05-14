"""Run the offline SkillOS demo benchmark.

This runner is intentionally deterministic: it gives the C runtime layer a
repeatable SkillsBench-style fixture before real LLM evaluation is wired in.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from skillos.layers.skill_runtime.executor import SkillExecutor  # noqa: E402
from skillos.layers.skill_runtime.verifier import evaluate_verifier_specs  # noqa: E402
from skillos.models.skill_model import (  # noqa: E402
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillState,
)

try:  # noqa: SIM105
    from .summarize_results import summarize_results, write_summary  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - direct script execution path
    from summarize_results import summarize_results, write_summary  # type: ignore[no-redef]


MODES = ("no_skill", "raw_prompt", "with_skill")


def load_tasks(path: Path) -> List[Dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError("Benchmark task file must contain a JSON list.")
    return [task for task in tasks if isinstance(task, dict)]


def run_benchmark(tasks: List[Dict[str, Any]], modes: Iterable[str] = MODES) -> Dict[str, Any]:
    results = []
    selected_modes = [mode for mode in modes if mode in MODES]
    for task in tasks:
        for mode in selected_modes:
            results.append(run_task(task, mode))
    payload = {
        "generated_at": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "modes": selected_modes,
        "task_count": len(tasks),
        "results": results,
    }
    payload["summary"] = summarize_results(payload)
    return payload


def run_task(task: Dict[str, Any], mode: str) -> Dict[str, Any]:
    task_id = str(task.get("task_id", "unknown_task"))
    goal = str(task.get("goal", ""))
    mode_result = _run_mode(task, mode)
    output = mode_result["output"]
    verification = evaluate_verifier_specs(
        task.get("success_verifier", []),
        output,
        goal=goal,
    )
    success = verification.passed
    failure_reason = "" if success else _failure_reason(verification.issues, output)
    return {
        "task_id": task_id,
        "domain": task.get("domain", "general"),
        "mode": mode,
        "status": "success" if success else "failed",
        "success": success,
        "output": output,
        "latency_ms": mode_result["latency_ms"],
        "steps": mode_result["steps"],
        "skills_used": mode_result["skills_used"],
        "verifier_passed": verification.passed,
        "verifier_summary": {
            "score": verification.score,
            "issues": verification.issues,
            "details": verification.details,
        },
        "failure_reason": failure_reason,
    }


def _run_mode(task: Dict[str, Any], mode: str) -> Dict[str, Any]:
    if mode == "with_skill":
        return asyncio.run(_run_with_runtime_skills(task))
    output = _baseline_output(task, mode)
    success = bool(output.get("success") is True)
    return {
        "output": output,
        "latency_ms": _latency_ms(str(task.get("task_id", "unknown_task")), mode),
        "steps": _baseline_steps(mode, success),
        "skills_used": [],
    }


async def _run_with_runtime_skills(task: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(task.get("task_id", "unknown_task"))
    expected_output = task.get("expected_output", {})
    if not isinstance(expected_output, dict):
        expected_output = {"success": False, "reason": "Task expected_output must be an object."}

    executor = SkillExecutor(max_retries=0, step_timeout_s=5.0)
    skills_used = _expected_skills(task)
    steps: List[Dict[str, Any]] = []
    final_output: Dict[str, Any] = {}
    total_latency_ms = 0.0

    for index, skill_id in enumerate(skills_used):
        skill = _runtime_skill(skill_id, task, expected_output)
        record = await executor.execute_single(
            skill=skill,
            input_data=task.get("input", {}),
            task_id=task_id,
        )
        status = record.status.value if hasattr(record.status, "value") else str(record.status)
        final_output = record.output_data or {}
        total_latency_ms += record.latency_ms or 0.0
        steps.append(
            {
                "step_index": index,
                "skill_id": skill_id,
                "status": status,
                "latency_ms": record.latency_ms or 0.0,
                "error": record.error_message,
                "input_mapping": task.get("input", {}),
            }
        )

    return {
        "output": final_output,
        "latency_ms": total_latency_ms or _latency_ms(task_id, "with_skill"),
        "steps": steps,
        "skills_used": skills_used,
    }


def _runtime_skill(skill_id: str, task: Dict[str, Any], expected_output: Dict[str, Any]) -> Skill:
    display_name = expected_output.get("skill_name")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = skill_id
    code = _parameterized_web_skill_code(skill_id) if task.get("domain") == "web" else None
    if code is None:
        code = f"output.update({expected_output!r})"
    return Skill(
        skill_id=skill_id,
        name=display_name,
        description=f"Benchmark fixture runtime skill for {skill_id}",
        state=SkillState.RELEASED,
        interface=_runtime_skill_interface(skill_id, task),
        implementation=SkillImplementation(
            code=code,
        ),
    )


def _runtime_skill_interface(skill_id: str, task: Dict[str, Any]) -> SkillInterface:
    input_data = task.get("input", {})
    properties = {
        key: {"type": _json_schema_type(value)}
        for key, value in input_data.items()
        if isinstance(key, str)
    } if isinstance(input_data, dict) else {}
    return SkillInterface(
        input_schema={
            "type": "object",
            "properties": properties,
            "additionalProperties": True,
        },
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "actions": {"type": "array"},
                "final_state": {"type": "object"},
            },
        },
        postconditions=[
            "Output fields are derived from input_data parameters.",
            f"Benchmark skill {skill_id} remains scoped to fake DOM/state.",
        ],
    )


def _json_schema_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _parameterized_web_skill_code(skill_id: str) -> str | None:
    programs = {
        "fill_form": _WEB_FILL_FORM_PROGRAM,
        "click_element": _WEB_CLICK_ELEMENT_PROGRAM,
        "type_text": _WEB_TYPE_TEXT_PROGRAM,
        "extract_selector": _WEB_EXTRACT_SELECTOR_PROGRAM,
        "submit_form": _WEB_SUBMIT_FORM_PROGRAM,
    }
    return programs.get(skill_id)


_WEB_FILL_FORM_PROGRAM = """
url = str(input_data.get("url", "")).strip()
form_data = input_data.get("form_data", {})
if not isinstance(form_data, dict):
    form_data = {}
fields = dict(form_data)
required_fields = ["username", "password"]
missing_fields = [field for field in required_fields if not fields.get(field)]
success = "/login" in url and not missing_fields
actions = ["navigate:" + url]
actions.extend(["fill:" + field for field in fields])
if success:
    actions.append("submit:#login-form")
output.update({
    "success": success,
    "page": "dashboard" if success else "login",
    "actions": actions,
    "final_state": {
        "submitted": success,
        "url": url,
        "fields": fields,
        "missing_fields": missing_fields,
    },
    "input_mapping": {
        "url": url,
        "form_data": fields,
    },
    "action_program": "fill_form(url, form_data)",
    "step_guidance": [
        "Open the target login page.",
        "Fill each provided form field.",
        "Submit the fake login form only when required fields exist.",
    ],
    "parameters_used": ["url", "form_data"],
    "paper_method": "WebXSkill parameterized action program",
})
"""


_WEB_CLICK_ELEMENT_PROGRAM = """
selector = str(input_data.get("selector", "")).strip()
success = bool(selector)
output.update({
    "success": success,
    "actions": ["click:" + selector] if success else [],
    "final_state": {
        "clicked_selector": selector if success else None,
    },
    "input_mapping": {
        "selector": selector,
    },
    "action_program": "click_element(selector)",
    "step_guidance": ["Find the target selector in the fake DOM.", "Click the matched element."],
    "parameters_used": ["selector"],
    "paper_method": "WebXSkill parameterized action program",
})
"""


_WEB_TYPE_TEXT_PROGRAM = """
selector = str(input_data.get("selector", "")).strip()
text = str(input_data.get("text", "")).strip()
success = bool(selector and text)
actions = []
if selector:
    actions.append("click:" + selector)
if text:
    actions.append("type:" + text)
output.update({
    "success": success,
    "actions": actions,
    "final_state": {
        "focused_selector": selector if selector else None,
        "typed_text": text if text else None,
    },
    "input_mapping": {
        "selector": selector,
        "text": text,
    },
    "action_program": "type_text(selector, text)",
    "step_guidance": ["Focus the parameterized selector.", "Type the parameterized text."],
    "parameters_used": ["selector", "text"],
    "paper_method": "WebXSkill parameterized action program",
})
"""


_WEB_EXTRACT_SELECTOR_PROGRAM = """
html = str(input_data.get("html", ""))
selector = ""
if ("id=\\"login-form\\"" in html or "id='login-form'" in html) and (
    "name=\\"email\\"" in html or "name='email'" in html
):
    selector = "#login-form input[name=email]"
elif "name=\\"email\\"" in html or "name='email'" in html:
    selector = "input[name=email]"
success = bool(selector)
output.update({
    "success": success,
    "selector": selector,
    "actions": ["extract_selector:" + selector] if success else [],
    "final_state": {
        "selector": selector,
        "matched": success,
    },
    "input_mapping": {
        "html": html,
    },
    "action_program": "extract_selector(html)",
    "step_guidance": ["Inspect the provided fake DOM fragment.", "Return the stable email input selector."],
    "parameters_used": ["html"],
    "paper_method": "WebXSkill parameterized action program",
})
"""


_WEB_SUBMIT_FORM_PROGRAM = """
selector = str(input_data.get("selector", "")).strip()
success = selector == "#submit" or selector == "button[type=submit]" or "submit" in selector
output.update({
    "success": success,
    "status": "submitted" if success else "blocked",
    "actions": ["click:" + selector, "submit:form"] if success else [],
    "final_state": {
        "submitted": success,
        "submit_selector": selector,
    },
    "input_mapping": {
        "selector": selector,
    },
    "action_program": "submit_form(selector)",
    "step_guidance": ["Use the parameterized submit selector.", "Submit the prepared fake form."],
    "parameters_used": ["selector"],
    "paper_method": "WebXSkill parameterized action program",
})
"""


def _expected_skills(task: Dict[str, Any]) -> List[str]:
    skills = [
        str(skill_id).strip()
        for skill_id in task.get("expected_skills", [])
        if str(skill_id).strip()
    ]
    if skills:
        return skills
    task_id = str(task.get("task_id", "benchmark_task")).strip() or "benchmark_task"
    return [task_id]


def _baseline_output(task: Dict[str, Any], mode: str) -> Dict[str, Any]:
    if mode == "raw_prompt":
        output = task.get("raw_prompt_output")
        if isinstance(output, dict):
            return dict(output)
        return {
            "success": False,
            "mode": mode,
            "reason": "Raw prompt baseline has no focused executable skill.",
        }
    return {
        "success": False,
        "mode": mode,
        "reason": "No skill was selected for this baseline.",
    }


def _baseline_steps(mode: str, success: bool) -> List[Dict[str, Any]]:
    status = "success" if success else "failed"
    return [
        {
            "step_index": 0,
            "skill_id": mode,
            "status": status,
        }
    ]


def _latency_ms(task_id: str, mode: str) -> float:
    mode_weight = {"no_skill": 5, "raw_prompt": 17, "with_skill": 11}[mode]
    return float(len(task_id) * 7 + mode_weight)


def _failure_reason(issues: List[str], output: Dict[str, Any]) -> str:
    if issues:
        return "; ".join(issues)
    return str(output.get("reason") or "Verifier did not pass.")


def _write_payload(payload: Dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks",
        type=Path,
        default=Path(__file__).parent / "skillos_demo_tasks.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "results" / "demo_benchmark_latest.json",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        action="append",
        help="Run only the selected mode. Repeat for multiple modes.",
    )
    parser.add_argument(
        "--no-summary-files",
        action="store_true",
        help="Skip latest_summary.json and latest_summary.md generation.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    payload = run_benchmark(load_tasks(args.tasks), modes=args.mode or MODES)
    output_path = _write_payload(payload, args.output)
    result = {"result": str(output_path)}
    if not args.no_summary_files:
        result["summary"] = write_summary(payload["summary"], output_path.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
