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
            return SimpleNamespace(
                content=json.dumps({
                    "strategy": "compose" if len(selected) > 1 else "reuse",
                    "selected_skill_ids": selected,
                    "execution_order": selected,
                    "confidence": 0.9,
                    "rationale": "matched task keywords",
                    "parameter_mapping": {},
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
                "passed": True,
                "score": 0.9,
                "issues": [],
                "suggestions": [],
                "reasoning": "output satisfies benchmark task",
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
    assert "web_form_login" in report


def test_runtime_benchmark_applies_rule_based_verifier_floor():
    result = run_runtime_benchmark(FailingVerifierLLM())  # type: ignore[arg-type]

    assert result.score > 90
    assert all(case.verification_score >= 0.7 for case in result.cases)
    assert any("rule-based verifier floor applied" in note for case in result.cases for note in case.notes)


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
    return [name_to_id["fill_form"]]


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
