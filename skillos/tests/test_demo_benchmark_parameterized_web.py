from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from benchmarks.run_demo_benchmark import load_tasks, run_task


def _task_by_id(task_id: str) -> dict:
    tasks = load_tasks(Path(__file__).resolve().parents[1] / "benchmarks" / "skillos_demo_tasks.json")
    return next(task for task in tasks if task["task_id"] == task_id)


def test_web_fill_form_uses_url_and_form_data_parameters() -> None:
    task = _task_by_id("web_fill_login_form")
    task["input"]["url"] = "https://demo.local/login"
    task["input"]["form_data"] = {"username": "ada", "password": "lovelace"}

    result = run_task(task, "with_skill")

    assert result["status"] == "success"
    output = result["output"]
    assert output["page"] == "dashboard"
    assert output["final_state"]["fields"] == {"username": "ada", "password": "lovelace"}
    assert output["input_mapping"]["url"] == "https://demo.local/login"
    assert output["paper_method"] == "WebXSkill parameterized action program"
    assert result["steps"][0]["input_mapping"]["form_data"]["username"] == "ada"


def test_web_fill_form_fails_when_required_parameter_missing() -> None:
    task = deepcopy(_task_by_id("web_fill_login_form"))
    task["input"]["form_data"] = {"username": "ada"}

    result = run_task(task, "with_skill")

    assert result["status"] == "failed"
    assert result["output"]["success"] is False
    assert result["output"]["final_state"]["missing_fields"] == ["password"]
    assert "Expected output.success == True" in result["failure_reason"]


def test_web_click_and_type_actions_are_derived_from_parameters() -> None:
    task = deepcopy(_task_by_id("web_click_and_type"))
    task["input"] = {"selector": "#global-search", "text": "paper methods"}
    task["success_verifier"] = [
        {"type": "json_equals", "path": "output.success", "value": True},
        {"type": "contains", "path": "output.actions", "value": "click:#global-search"},
        {"type": "contains", "path": "output.actions", "value": "type:paper methods"},
    ]

    result = run_task(task, "with_skill")

    assert result["status"] == "success"
    assert result["output"]["actions"] == ["click:#global-search", "type:paper methods"]
    assert result["output"]["parameters_used"] == ["selector", "text"]


def test_web_extract_selector_uses_fake_dom_html() -> None:
    task = deepcopy(_task_by_id("web_extract_selector"))
    task["input"]["html"] = '<form id="login-form"><input name="email" /></form>'

    result = run_task(task, "with_skill")

    assert result["status"] == "success"
    assert result["output"]["selector"] == "#login-form input[name=email]"
    assert result["output"]["action_program"] == "extract_selector(html)"
