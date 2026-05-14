from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.run_llm_planner_eval import (
    PlannerEvalConfig,
    run_planner_eval,
    write_eval_payload,
)
from skillos.config.llm_config import LLMConfig
from skillos.layers.skill_runtime.planner import SkillPlanner
from skillos.models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState
from skillos.utils.llm_client import LLMRateLimitError


def test_llm_planner_eval_compares_fallback_and_llm_without_api_key_leak() -> None:
    tasks = [
        _task("web_fill_login_form", ["fill_form"]),
        _task("web_submit_form", ["submit_form"]),
    ]

    payload = run_planner_eval(
        tasks,
        PlannerEvalConfig(api_key="team-key", model="fixed-model", temperature=0.0, seed=42),
        llm_client_factory=lambda cfg: FakeLLM(
            [
                _planner_payload("fill_form"),
                _planner_payload("submit_form"),
            ],
            cfg=cfg,
        ),
    )

    by_mode = {
        item["mode"]: item
        for item in payload["results"]
        if item["task_id"] == "web_fill_login_form"
    }
    assert by_mode["fallback"]["status"] == "functional_failure"
    assert by_mode["llm"]["status"] == "success"
    assert by_mode["llm"]["llm_model"] == "fixed-model"
    assert by_mode["llm"]["llm_usage"] == {"total_tokens": 12}
    assert by_mode["llm"]["llm_finish_reason"] == "stop"
    assert by_mode["llm"]["llm_request"]["seed"] == 42
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "team-key" not in serialized
    assert payload["summary"]["mode_totals"]["llm"]["success_rate_excluding_api_failures"] == 1.0


def test_llm_planner_eval_records_rate_limit_separately_from_functional_failure() -> None:
    task = _task("web_fill_login_form", ["fill_form"])

    payload = run_planner_eval(
        [task],
        PlannerEvalConfig(api_key="team-key"),
        modes=["llm"],
        llm_client_factory=lambda cfg: RateLimitedLLM(cfg),
    )

    item = payload["results"][0]
    assert item["status"] == "api_failure"
    assert item["success"] is None
    assert item["api_failure"] is True
    assert item["api_error_type"] == "rate_limit"
    assert item["failure_reason"] == ""
    totals = payload["summary"]["mode_totals"]["llm"]
    assert totals["api_failure"] == 1
    assert totals["functional_failure"] == 0
    assert totals["success_rate_excluding_api_failures"] == 0.0


def test_llm_planner_eval_skips_llm_mode_when_key_missing() -> None:
    payload = run_planner_eval(
        [_task("web_fill_login_form", ["fill_form"])],
        PlannerEvalConfig(api_key=""),
        modes=["llm"],
    )

    item = payload["results"][0]
    assert item["status"] == "skipped"
    assert item["api_error_type"] == "missing_api_key"


def test_llm_planner_eval_writes_timestamped_payload_and_latest(tmp_path: Path) -> None:
    payload = {
        "benchmark": "llm_planner_eval",
        "config": {"api_key_provided": True},
        "results": [],
    }

    paths = write_eval_payload(payload, tmp_path / "llm_eval_results_test.json")

    result_path = Path(paths["result"])
    latest_path = Path(paths["latest"])
    assert result_path.name == "llm_eval_results_test.json"
    assert latest_path.name == "llm_eval_latest.json"
    assert json.loads(result_path.read_text(encoding="utf-8"))["benchmark"] == "llm_planner_eval"
    assert json.loads(latest_path.read_text(encoding="utf-8"))["benchmark"] == "llm_planner_eval"


@pytest.mark.asyncio
async def test_planner_strict_mode_propagates_llm_errors() -> None:
    planner = SkillPlanner(RateLimitedLLM(LLMConfig(api_key="team-key")))

    with pytest.raises(LLMRateLimitError):
        await planner.plan(
            "fill form",
            [_skill("fill_form")],
            force_llm=True,
            fallback_on_llm_error=False,
        )


@pytest.mark.asyncio
async def test_planner_strict_mode_keeps_invalid_llm_response_as_llm_failure() -> None:
    planner = SkillPlanner(FakeLLM("not json"))

    plan = await planner.plan(
        "fill form",
        [_skill("fill_form")],
        force_llm=True,
        fallback_on_invalid_response=False,
    )

    assert plan.steps == []
    assert plan.metadata["source"] == "llm"
    assert plan.metadata["invalid_response"] is True


def _task(task_id: str, expected_skills: list[str]) -> dict:
    return {
        "task_id": task_id,
        "domain": "web",
        "goal": "Fill a login form.",
        "input": {
            "url": "https://demo.local/login",
            "form_data": {"username": "demo", "password": "secret"},
            "selector": "#submit",
            "text": "SkillOS",
        },
        "raw_context": "Login page.",
        "expected_skills": expected_skills,
    }


def _skill(skill_id: str) -> Skill:
    return Skill(
        skill_id=skill_id,
        name=skill_id,
        description=f"{skill_id} test skill",
        state=SkillState.RELEASED,
        interface=SkillInterface(),
        implementation=SkillImplementation(code="output['ok'] = True"),
    )


def _planner_payload(skill_id: str) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "step_index": 0,
                    "skill_id": skill_id,
                    "skill_name": skill_id,
                    "description": f"Use {skill_id}.",
                    "input_mapping": {},
                    "depends_on": [],
                }
            ],
            "plan_rationale": f"Goal maps to {skill_id}.",
        }
    )


class FakeLLM:
    def __init__(self, content: str | list[str], cfg: LLMConfig | None = None) -> None:
        self.contents = content if isinstance(content, list) else [content]
        self._cfg = cfg or LLMConfig(api_key="team-key")
        self.calls: list[dict] = []

    def chat(self, messages: object, **kwargs: object) -> SimpleNamespace:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        content = self.contents[min(len(self.calls) - 1, len(self.contents) - 1)]
        return SimpleNamespace(
            content=content,
            model=self._cfg.model,
            usage={"total_tokens": 12},
            finish_reason="stop",
        )


class RateLimitedLLM:
    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg

    def chat(self, messages: object, **kwargs: object) -> SimpleNamespace:
        raise LLMRateLimitError("rate limit")
