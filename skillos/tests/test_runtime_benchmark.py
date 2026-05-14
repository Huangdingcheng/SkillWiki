from __future__ import annotations

import json
from types import SimpleNamespace

from skillos.evals.runtime_benchmark import RuntimeBenchmark, run_runtime_benchmark
from skillos.config.llm_config import LLMConfig
from skillos.utils.llm_client import LLMClient


class FakeLLM:
    def __init__(self) -> None:
        self._cfg = SimpleNamespace(model="fake-model", api_url="memory://fake")
        self._calls = 0

    def chat(self, messages: object) -> SimpleNamespace:
        self._calls += 1
        text = messages[-1].content
        if "Select the best SkillOS retrieval strategy" in text:
            selected = _select_skill_ids(text)
            group = _skill_group_payload(text, selected)
            return SimpleNamespace(
                content=json.dumps({
                    "strategy": "compose" if len(selected) > 1 else "reuse",
                    "selected_skill_ids": selected,
                    "execution_order": selected,
                    "confidence": 0.9,
                    "rationale": "matched task keywords",
                    "parameter_mapping": {},
                    "skill_group": group,
                }),
                total_tokens=10,
            )
        if "Create a SkillOS execution plan" in text:
            selected = _select_skill_ids(text)
            id_to_name = _candidate_id_to_name(text)
            steps = [
                {
                    "step_index": index,
                    "skill_id": skill_id,
                    "skill_name": id_to_name.get(skill_id, skill_id),
                    "description": f"Run {skill_id}",
                    "input_mapping": {},
                    "depends_on": [str(index - 1)] if index else [],
                }
                for index, skill_id in enumerate(selected)
            ]
            return SimpleNamespace(
                content=json.dumps({"steps": steps, "plan_rationale": "ordered by task"}),
                total_tokens=10,
            )
        return SimpleNamespace(
            content=json.dumps({
                **_verification_payload(text),
            }),
            total_tokens=10,
        )


class FailingVerifierLLM(FakeLLM):
    def chat(self, messages: object) -> SimpleNamespace:
        text = messages[-1].content
        if (
            "Select the best SkillOS retrieval strategy" in text
            or "Create a SkillOS execution plan" in text
        ):
            return super().chat(messages)
        return SimpleNamespace(
            content=json.dumps({
                "passed": False,
                "score": 0.0,
                "issues": ["strict verifier rejected simulated output"],
                "suggestions": [],
                "reasoning": "simulated output lacks external evidence",
            }),
            total_tokens=10,
        )


def test_runtime_benchmark_prints_score_report():
    result = run_runtime_benchmark(FakeLLM())  # type: ignore[arg-type]
    report = result.format_report()

    assert result.score > 80
    assert "Score:" in report
    assert "composition=" in report
    assert "recovery=" in report
    assert "web_form_login" in report


def test_runtime_benchmark_applies_rule_based_verifier_floor():
    result = run_runtime_benchmark(FailingVerifierLLM())  # type: ignore[arg-type]

    assert result.score > 80
    assert all(
        case.verification_score >= 0.7
        for case in result.cases
        if case.status == "success"
    )
    assert any("rule-based verifier floor applied" in note for case in result.cases for note in case.notes)


def test_runtime_benchmark_scores_new_runtime_dimensions():
    result = run_runtime_benchmark(FakeLLM())  # type: ignore[arg-type]
    cases = {case.task_id: case for case in result.cases}

    grouped = cases["support_start_check_flow"]
    assert grouped.skill_group_score == 1.0
    assert grouped.composition_score == 1.0
    assert grouped.memory_score == 1.0

    missing = cases["missing_skill_recovery_route"]
    assert missing.recovery_score == 1.0
    assert missing.status == "failed"


def test_deepseek_chat_url_uses_official_path():
    client = LLMClient(
        LLMConfig(
            api_key="test-key",
            api_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
        )
    )

    assert client._chat_completions_url() == "https://api.deepseek.com/chat/completions"


def _select_skill_ids(prompt: str) -> list[str]:
    name_to_id = {name: skill_id for skill_id, name in _candidate_id_to_name(prompt).items()}
    lowered = _task_text(prompt).lower()
    if "login form" in lowered or "email and password" in lowered:
        return [name_to_id["fill_form"]]
    if "checkout button" in lowered:
        return [name_to_id["locate_element"], name_to_id["click_element"]]
    if "http api" in lowered or "json payload" in lowered:
        return [name_to_id["post_json_api"]]
    if "summarize" in lowered:
        return [name_to_id["summarize_text"]]
    if "prepare customer data" in lowered or "process the order" in lowered:
        return [
            name_to_id["prepare_customer_data"],
            name_to_id["process_order"],
            name_to_id["validate_order"],
        ]
    if "unavailable payment capture" in lowered:
        return []
    return [name_to_id["fill_form"]]


def _skill_group_payload(prompt: str, selected: list[str]) -> dict:
    name_to_id = {name: skill_id for skill_id, name in _candidate_id_to_name(prompt).items()}
    lowered = _task_text(prompt).lower()
    if "checkout button" in lowered:
        return {
            "anchor_skill_id": name_to_id["click_element"],
            "start_skill_ids": [name_to_id["click_element"]],
            "support_skill_ids": [name_to_id["locate_element"]],
            "check_skill_ids": [],
            "avoid_skill_ids": [],
            "rationale": "locate before click",
        }
    if "prepare customer data" in lowered or "process the order" in lowered:
        return {
            "anchor_skill_id": name_to_id["process_order"],
            "start_skill_ids": [name_to_id["process_order"]],
            "support_skill_ids": [name_to_id["prepare_customer_data"]],
            "check_skill_ids": [name_to_id["validate_order"]],
            "avoid_skill_ids": [],
            "rationale": "prepare, process, validate",
        }
    if not selected:
        return {
            "anchor_skill_id": "",
            "start_skill_ids": [],
            "support_skill_ids": [],
            "check_skill_ids": [],
            "avoid_skill_ids": [name_to_id["legacy_payment_lookup"]],
            "rationale": "available payment skill is not suitable",
        }
    return {
        "anchor_skill_id": selected[0],
        "start_skill_ids": [selected[0]],
        "support_skill_ids": [],
        "check_skill_ids": [],
        "avoid_skill_ids": [],
        "rationale": "single skill group",
    }


def _verification_payload(prompt: str) -> dict:
    lowered = prompt.lower()
    if "unavailable payment capture" in lowered:
        return {
            "passed": False,
            "score": 0.2,
            "issues": ["missing skill"],
            "suggestions": ["retrieve alternative skill"],
            "failure_type": "missing_skill",
            "recovery_route": "retrieve_alternative_skill",
            "reasoning": "no executable payment capture skill was available",
        }
    return {
        "passed": True,
        "score": 0.9,
        "issues": [],
        "suggestions": [],
        "failure_type": "none",
        "recovery_route": "none",
        "reasoning": "output satisfies benchmark task",
    }


def _task_text(prompt: str) -> str:
    if "Task:" not in prompt:
        return prompt
    after_task = prompt.split("Task:", 1)[1]
    for marker in ("Current state:", "Available skills:"):
        if marker in after_task:
            return after_task.split(marker, 1)[0]
    return after_task


def _candidate_id_to_name(prompt: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in prompt.splitlines():
        line = line.strip()
        if "[" not in line or "]" not in line:
            continue
        skill_id = line.split("[", 1)[1].split("]", 1)[0]
        after = line.split("]", 1)[1].strip()
        if after and after[0].isdigit() and ". [" in line:
            after = after.split("]", 1)[1].strip()
        name = after.split(":", 1)[0].split("(", 1)[0].strip()
        if skill_id and name:
            mapping[skill_id] = name
    return mapping
