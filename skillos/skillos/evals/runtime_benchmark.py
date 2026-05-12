"""Formal runtime benchmark for comparing SkillOS prompts and agent wiring."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from ..api.memory_store import MemorySearchEngine, MemoryWikiManager
from ..layers.skill_runtime import SkillExecutor, SkillPlanner, SkillRetriever, VerifierAgent
from ..models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState, SkillType
from ..utils.llm_client import LLMClient


@dataclass(frozen=True)
class RuntimeBenchmarkTask:
    task_id: str
    goal: str
    expected_skill_names: List[str]
    required_output_keys: List[str] = field(default_factory=list)
    max_steps: int = 4
    verifier_floor: float = 0.7


@dataclass
class RuntimeBenchmarkCaseResult:
    task_id: str
    goal: str
    selected_skill_names: List[str]
    plan_skill_names: List[str]
    status: str
    retrieval_score: float
    planning_score: float
    execution_score: float
    verification_score: float
    latency_ms: float
    token_count: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return round(
            0.30 * self.retrieval_score
            + 0.25 * self.planning_score
            + 0.30 * self.execution_score
            + 0.15 * self.verification_score,
            4,
        )


@dataclass
class RuntimeBenchmarkResult:
    model: str
    api_url: str
    cases: List[RuntimeBenchmarkCaseResult]
    started_at: float
    finished_at: float

    @property
    def score(self) -> float:
        if not self.cases:
            return 0.0
        return round(sum(case.total_score for case in self.cases) / len(self.cases) * 100, 2)

    @property
    def avg_latency_ms(self) -> float:
        if not self.cases:
            return 0.0
        return round(sum(case.latency_ms for case in self.cases) / len(self.cases), 2)

    @property
    def total_tokens(self) -> int:
        return sum(case.token_count for case in self.cases)

    def format_report(self) -> str:
        lines = [
            "SkillOS Runtime Benchmark",
            f"Model: {self.model}",
            f"API URL: {self.api_url}",
            f"Score: {self.score:.2f}/100",
            f"Cases: {len(self.cases)}",
            f"Avg latency: {self.avg_latency_ms:.2f} ms",
            f"LLM tokens observed: {self.total_tokens}",
            "",
            "Per-task scores:",
        ]
        for case in self.cases:
            lines.append(
                "- {task_id}: total={total:.2f}/100 retrieval={retrieval:.2f} "
                "planning={planning:.2f} execution={execution:.2f} "
                "verification={verification:.2f} status={status} latency={latency:.2f}ms".format(
                    task_id=case.task_id,
                    total=case.total_score * 100,
                    retrieval=case.retrieval_score * 100,
                    planning=case.planning_score * 100,
                    execution=case.execution_score * 100,
                    verification=case.verification_score * 100,
                    status=case.status,
                    latency=case.latency_ms,
                )
            )
            if case.notes:
                lines.append(f"  notes: {'; '.join(case.notes)}")
        return "\n".join(lines)


class RuntimeBenchmark:
    """Runs a fixed task set through Retriever, Planner, Executor, and Verifier."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        tasks: Optional[List[RuntimeBenchmarkTask]] = None,
    ) -> None:
        self._llm = llm_client
        self._tasks = tasks or default_runtime_tasks()
        self._token_count = 0

    async def run(self) -> RuntimeBenchmarkResult:
        started_at = time.time()
        wiki = MemoryWikiManager()
        for skill in benchmark_skills():
            await wiki.create(skill)

        search = MemorySearchEngine(wiki)
        metered_llm = self._metered_llm()
        retriever = SkillRetriever(metered_llm, search)
        planner = SkillPlanner(metered_llm)
        executor = SkillExecutor(skill_registry=wiki, llm_client=metered_llm, max_retries=0)
        verifier = VerifierAgent(metered_llm)

        cases: List[RuntimeBenchmarkCaseResult] = []
        for task in self._tasks:
            case_tokens_before = self._token_count
            case_start = time.perf_counter()
            notes: List[str] = []

            retrieval = await retriever.retrieve(task.goal)
            selected_skills = retrieval.skills
            plan = await planner.plan(task.goal, selected_skills)
            skill_map = {skill.skill_id: skill for skill in selected_skills}
            final_state = await executor.execute_plan(plan, skill_map, {})
            verification = verifier.verify(task.goal, final_state, str(plan.to_summary()))

            latency_ms = (time.perf_counter() - case_start) * 1000
            selected_names = [skill.name for skill in selected_skills]
            plan_names = [step.skill_name for step in plan.steps]
            retrieval_score = _coverage_score(selected_names, task.expected_skill_names)
            planning_score = _planning_score(plan_names, task.expected_skill_names, task.max_steps)
            execution_score = _execution_score(plan, final_state, task.required_output_keys)
            rule_verification_score = _rule_verification_score(
                plan,
                final_state,
                task.required_output_keys,
                task.verifier_floor,
            )
            llm_verification_score = max(
                0.0,
                min(1.0, float(getattr(verification, "score", 0.0))),
            )
            verification_score = max(rule_verification_score, llm_verification_score)
            status = _plan_status(plan)

            if retrieval.needs_generation:
                notes.append("retriever requested generation")
            if not plan.steps:
                notes.append("planner returned no steps")
            if not getattr(verification, "passed", False):
                notes.append(
                    "LLM verifier did not pass output; rule-based verifier floor applied"
                    if rule_verification_score > llm_verification_score
                    else "verifier did not pass output"
                )

            cases.append(
                RuntimeBenchmarkCaseResult(
                    task_id=task.task_id,
                    goal=task.goal,
                    selected_skill_names=selected_names,
                    plan_skill_names=plan_names,
                    status=status,
                    retrieval_score=retrieval_score,
                    planning_score=planning_score,
                    execution_score=execution_score,
                    verification_score=verification_score,
                    latency_ms=latency_ms,
                    token_count=self._token_count - case_tokens_before,
                    notes=notes,
                )
            )

        return RuntimeBenchmarkResult(
            model=self._llm._cfg.model,
            api_url=self._llm._cfg.api_url,
            cases=cases,
            started_at=started_at,
            finished_at=time.time(),
        )

    def _metered_llm(self) -> LLMClient:
        parent = self

        class MeteredLLM:
            def chat(self, *args: Any, **kwargs: Any) -> Any:
                response = parent._llm.chat(*args, **kwargs)
                parent._token_count += int(getattr(response, "total_tokens", 0) or 0)
                return response

        return MeteredLLM()  # type: ignore[return-value]


def run_runtime_benchmark(llm_client: LLMClient) -> RuntimeBenchmarkResult:
    return asyncio.run(RuntimeBenchmark(llm_client).run())


def default_runtime_tasks() -> List[RuntimeBenchmarkTask]:
    return [
        RuntimeBenchmarkTask(
            task_id="web_form_login",
            goal="Fill an email and password into a login form, then submit it.",
            expected_skill_names=["fill_form"],
            required_output_keys=["submitted"],
            max_steps=2,
        ),
        RuntimeBenchmarkTask(
            task_id="web_click_button",
            goal="Find the checkout button on a web page and click it.",
            expected_skill_names=["locate_element", "click_element"],
            required_output_keys=["success"],
            max_steps=3,
        ),
        RuntimeBenchmarkTask(
            task_id="text_summary",
            goal="Summarize a long product review into three concise bullet points.",
            expected_skill_names=["summarize_text"],
            required_output_keys=["summary"],
            max_steps=2,
        ),
        RuntimeBenchmarkTask(
            task_id="api_post_json",
            goal="Send a JSON payload to an HTTP API endpoint and report the status code.",
            expected_skill_names=["post_json_api"],
            required_output_keys=["status_code"],
            max_steps=2,
        ),
    ]


def benchmark_skills() -> List[Skill]:
    return [
        _skill(
            "click_element",
            "Click one target element on a web page.",
            ["web", "ui", "click"],
            {"success": True},
            inputs={"selector": "string"},
            outputs={"success": "boolean"},
        ),
        _skill(
            "locate_element",
            "Locate a web page element and return a CSS selector.",
            ["web", "ui", "selector"],
            {"selector": "#target"},
            inputs={"description": "string"},
            outputs={"selector": "string"},
        ),
        _skill(
            "fill_form",
            "Fill form fields and submit the form.",
            ["web", "form", "submit"],
            {"submitted": True, "success": True},
            inputs={"form_data": "object"},
            outputs={"submitted": "boolean", "success": "boolean"},
            skill_type=SkillType.FUNCTIONAL,
        ),
        _skill(
            "summarize_text",
            "Summarize long text into concise bullet points.",
            ["text", "summary", "nlp"],
            {"summary": "Summary generated."},
            inputs={"text": "string"},
            outputs={"summary": "string"},
        ),
        _skill(
            "post_json_api",
            "Send JSON to an HTTP API endpoint and return response status.",
            ["api", "http", "json"],
            {"status_code": 200, "success": True},
            inputs={"url": "string", "payload": "object"},
            outputs={"status_code": "integer", "success": "boolean"},
        ),
    ]


def _skill(
    name: str,
    description: str,
    tags: List[str],
    output: Dict[str, Any],
    *,
    inputs: Dict[str, str],
    outputs: Dict[str, str],
    skill_type: SkillType = SkillType.ATOMIC,
) -> Skill:
    code_lines = [f"output[{key!r}] = {value!r}" for key, value in output.items()]
    skill = Skill(
        name=name,
        description=description,
        tags=tags,
        skill_type=skill_type,
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {key: {"type": value} for key, value in inputs.items()},
            },
            output_schema={
                "type": "object",
                "properties": {key: {"type": value} for key, value in outputs.items()},
            },
        ),
        implementation=SkillImplementation(code="\n".join(code_lines)),
    )
    for _ in range(5):
        skill.record_execution(success=True, latency_ms=100.0)
    return skill


def _coverage_score(actual_names: Iterable[str], expected_names: Iterable[str]) -> float:
    actual = set(actual_names)
    expected = set(expected_names)
    if not expected:
        return 1.0
    return len(actual & expected) / len(expected)


def _planning_score(plan_names: List[str], expected_names: List[str], max_steps: int) -> float:
    if not plan_names:
        return 0.0
    coverage = _coverage_score(plan_names, expected_names)
    brevity = 1.0 if len(plan_names) <= max_steps else max(0.0, max_steps / len(plan_names))
    order = 1.0
    positions = [plan_names.index(name) for name in expected_names if name in plan_names]
    if len(positions) >= 2 and positions != sorted(positions):
        order = 0.5
    return round(0.65 * coverage + 0.25 * brevity + 0.10 * order, 4)


def _execution_score(plan: Any, final_state: Dict[str, Any], required_keys: List[str]) -> float:
    if not plan.steps:
        return 0.0
    success_ratio = sum(1 for step in plan.steps if step.status.value == "success") / len(plan.steps)
    if not required_keys:
        key_score = 1.0
    else:
        key_score = sum(
            1 for key in required_keys if _output_key_present(plan, final_state, key)
        ) / len(required_keys)
    return round(0.70 * success_ratio + 0.30 * key_score, 4)


def _rule_verification_score(
    plan: Any,
    final_state: Dict[str, Any],
    required_keys: List[str],
    verifier_floor: float,
) -> float:
    if _plan_status(plan) != "success":
        return 0.0
    if not required_keys:
        return verifier_floor
    if all(_output_key_present(plan, final_state, key) for key in required_keys):
        return verifier_floor
    return 0.0


def _output_key_present(plan: Any, final_state: Dict[str, Any], key: str) -> bool:
    if key in final_state:
        return True
    for step in getattr(plan, "steps", []):
        result = getattr(step, "result", None)
        if isinstance(result, dict) and key in result:
            return True
    return False


def _plan_status(plan: Any) -> str:
    if not plan.steps:
        return "failed"
    success_count = sum(1 for step in plan.steps if step.status.value == "success")
    failed_count = sum(1 for step in plan.steps if step.status.value in {"failed", "skipped"})
    if success_count == len(plan.steps):
        return "success"
    if success_count > 0 and failed_count > 0:
        return "partial"
    return "failed"
