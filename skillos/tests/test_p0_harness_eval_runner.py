from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_p0_harness_eval.py"
    spec = importlib.util.spec_from_file_location("run_p0_harness_eval", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_select_records_takes_three_per_input_type() -> None:
    module = _load_module()
    records = []
    for input_type in ["document", "script"]:
        for index in range(5):
            records.append(
                {
                    "fixture": {"input_type": input_type, "source_id": f"{input_type}-{index}"},
                    "create": {"created_skill_ids": [f"skill-{input_type}-{index}"]},
                }
            )

    selected = module.select_records({"records": records}, per_type=3)

    assert len(selected) == 6
    assert [item["fixture"]["source_id"] for item in selected[:3]] == ["document-0", "document-1", "document-2"]
    assert [item["fixture"]["source_id"] for item in selected[3:]] == ["script-0", "script-1", "script-2"]


def test_render_contract_echo_code_satisfies_nested_output_specs() -> None:
    module = _load_module()
    specs = [
        {"type": "json_nonempty", "path": "output.result.answer"},
        {"type": "json_array_nonempty", "path": "output.evidence"},
        {"type": "json_equals", "path": "output.verifier.passed", "value": True},
    ]

    code = module.render_contract_echo_code(specs, input_type="document")

    assert "output['result']" in code
    assert "output['result']['answer']" in code
    assert "output['verifier']['passed'] = True" in code


def test_negative_case_invalidates_first_required_field() -> None:
    module = _load_module()
    skill = {
        "interface": {
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "document_context": {"type": "string"},
                    "allowed_operations": {"type": "array"},
                },
                "required": ["task", "document_context", "allowed_operations"],
            }
        },
        "evaluation": {"verifier_specs": [{"type": "json_nonempty", "path": "input.task"}]},
    }
    fixture = {"input_type": "document", "source_id": "doc-1"}

    case = module.build_negative_test_case(skill, fixture, timeout_s=10)

    assert case["input_data"]["task"] == ""
    assert case["verifier_specs"][0]["path"] == "input.task"


def test_negative_case_adds_input_spec_when_original_specs_only_check_output() -> None:
    module = _load_module()
    skill = {
        "interface": {
            "input_schema": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            }
        },
        "evaluation": {"verifier_specs": [{"type": "json_exists", "path": "output.result"}]},
    }
    fixture = {"input_type": "trajectory", "source_id": "trajectory-1"}

    case = module.build_negative_test_case(skill, fixture, timeout_s=10)

    assert case["input_data"]["task"] == ""
    assert case["verifier_specs"][0] == {"type": "json_nonempty", "path": "input.task"}


def test_score_checks_requires_three_samples_per_all_five_types() -> None:
    module = _load_module()
    checks = []
    for input_type in ["trajectory", "document", "api_doc", "script", "past_skills"]:
        for index in range(3):
            checks.append({"input_type": input_type, "positive_pass": True, "negative_rejected": True})

    scores = module.score_checks(checks)

    assert scores["sample_count"] == 15
    assert scores["positive_pass_rate"] == 1.0
    assert scores["negative_rejection_rate"] == 1.0
    assert scores["input_types_meeting_minimum"] == 5


def test_parse_version_orders_patch_versions() -> None:
    module = _load_module()

    assert module.parse_version("1.0.10") > module.parse_version("1.0.2")
    assert module.parse_version("1.0.x") == (1, 0, 0)
