"""Formal runtime benchmark for comparing SkillOS prompts and agent wiring."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from ..api.memory_store import MemorySearchEngine, MemoryWikiManager
from ..layers.skill_runtime import (
    CompositionAgent,
    SkillExecutor,
    SkillPlanner,
    SkillRetriever,
    VerifierAgent,
)
from ..models.skill_model import Skill, SkillImplementation, SkillInterface, SkillState, SkillType
from ..utils.llm_client import LLMClient


@dataclass(frozen=True)
class RuntimeBenchmarkTask:
    task_id: str
    goal: str
    expected_skill_names: List[str]
    required_output_keys: List[str] = field(default_factory=list)
    expected_start_skill_names: List[str] = field(default_factory=list)
    expected_support_skill_names: List[str] = field(default_factory=list)
    expected_check_skill_names: List[str] = field(default_factory=list)
    expected_avoid_skill_names: List[str] = field(default_factory=list)
    expected_edges: List[tuple[str, str]] = field(default_factory=list)
    expected_failure_type: str = ""
    expected_recovery_route: str = ""
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
    skill_group_score: float
    composition_score: float
    execution_score: float
    verification_score: float
    recovery_score: float
    memory_score: float
    latency_ms: float
    token_count: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return round(
            0.15 * self.skill_group_score
            + 0.15 * self.planning_score
            + 0.20 * self.composition_score
            + 0.25 * self.execution_score
            + 0.10 * self.verification_score
            + 0.10 * self.recovery_score
            + 0.05 * self.memory_score,
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
                "skill_group={skill_group:.2f} planning={planning:.2f} "
                "composition={composition:.2f} execution={execution:.2f} "
                "verification={verification:.2f} recovery={recovery:.2f} "
                "memory={memory:.2f} status={status} latency={latency:.2f}ms".format(
                    task_id=case.task_id,
                    total=case.total_score * 100,
                    retrieval=case.retrieval_score * 100,
                    skill_group=case.skill_group_score * 100,
                    planning=case.planning_score * 100,
                    composition=case.composition_score * 100,
                    execution=case.execution_score * 100,
                    verification=case.verification_score * 100,
                    recovery=case.recovery_score * 100,
                    memory=case.memory_score * 100,
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
        composer = CompositionAgent(metered_llm)
        executor = SkillExecutor(skill_registry=wiki, llm_client=metered_llm, max_retries=0)
        verifier = VerifierAgent(metered_llm)

        cases: List[RuntimeBenchmarkCaseResult] = []
        for task in self._tasks:
            case_tokens_before = self._token_count
            case_start = time.perf_counter()
            notes: List[str] = []

            retrieval = await retriever.retrieve(task.goal)
            selected_skills = retrieval.skills
            graph = composer.compose(
                selected_skills,
                task.goal,
                skill_group=retrieval.skill_group,
            )
            plan = await planner.plan(task.goal, selected_skills)
            skill_map = {skill.skill_id: skill for skill in selected_skills}
            final_state = await executor.execute_plan(plan, skill_map, {})
            verification = verifier.verify(task.goal, final_state, str(plan.to_summary()))

            latency_ms = (time.perf_counter() - case_start) * 1000
            selected_names = [skill.name for skill in selected_skills]
            plan_names = [step.skill_name for step in plan.steps]
            id_to_name = {skill.skill_id: skill.name for skill in selected_skills}
            retrieval_score = _coverage_score(selected_names, task.expected_skill_names)
            skill_group_score = _skill_group_score(retrieval.skill_group, id_to_name, task)
            planning_score = _planning_score(plan_names, task.expected_skill_names, task.max_steps)
            composition_score = _composition_score(graph, id_to_name, task)
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
            recovery_score = _recovery_score(verification, task)
            memory_score = _memory_score(executor.last_runtime_memory, task)
            status = _plan_status(plan)

            if retrieval.needs_generation:
                notes.append("retriever requested generation")
            if not plan.steps:
                notes.append("planner returned no steps")
            if composition_score < 1.0:
                notes.append("composition did not fully match expected DAG")
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
                    skill_group_score=skill_group_score,
                    composition_score=composition_score,
                    execution_score=execution_score,
                    verification_score=verification_score,
                    recovery_score=recovery_score,
                    memory_score=memory_score,
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
            expected_start_skill_names=["click_element"],
            expected_support_skill_names=["locate_element"],
            expected_edges=[("locate_element", "click_element")],
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
        RuntimeBenchmarkTask(
            task_id="support_start_check_flow",
            goal="Prepare customer data, process the order, and validate the processed order.",
            expected_skill_names=["prepare_customer_data", "process_order", "validate_order"],
            expected_start_skill_names=["process_order"],
            expected_support_skill_names=["prepare_customer_data"],
            expected_check_skill_names=["validate_order"],
            expected_edges=[
                ("prepare_customer_data", "process_order"),
                ("process_order", "validate_order"),
            ],
            required_output_keys=["validated"],
            max_steps=4,
        ),
        RuntimeBenchmarkTask(
            task_id="missing_skill_recovery_route",
            goal="Use the unavailable payment capture skill and report the missing capability.",
            expected_skill_names=[],
            expected_failure_type="missing_skill",
            expected_recovery_route="retrieve_alternative_skill",
            max_steps=0,
            verifier_floor=0.0,
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
        _skill(
            "prepare_customer_data",
            "Prepare customer data and return normalized customer_data.",
            ["customer", "prepare", "support"],
            {"customer_data": {"id": "customer-1"}},
            inputs={"raw_customer": "object"},
            outputs={"customer_data": "object"},
        ),
        _skill(
            "process_order",
            "Process an order from customer_data and return order_result.",
            ["order", "process", "start"],
            {"order_result": {"status": "processed"}},
            inputs={"customer_data": "object"},
            outputs={"order_result": "object"},
        ),
        _skill(
            "validate_order",
            "Validate order_result and return validated.",
            ["order", "validate", "check"],
            {"validated": True, "success": True},
            inputs={"order_result": "object"},
            outputs={"validated": "boolean", "success": "boolean"},
        ),
        _skill(
            "legacy_payment_lookup",
            "Look up legacy payment records but cannot capture unavailable payments.",
            ["payment", "legacy", "avoid"],
            {"legacy": True},
            inputs={"payment_id": "string"},
            outputs={"legacy": "boolean"},
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


def _skill_group_score(skill_group: Any, id_to_name: Dict[str, str], task: RuntimeBenchmarkTask) -> float:
    if not (
        task.expected_start_skill_names
        or task.expected_support_skill_names
        or task.expected_check_skill_names
        or task.expected_avoid_skill_names
    ):
        return 1.0
    if not skill_group:
        return 0.0

    def names(ids: Iterable[str]) -> List[str]:
        return [id_to_name[skill_id] for skill_id in ids if skill_id in id_to_name]

    start_score = _coverage_score(names(skill_group.start_skill_ids), task.expected_start_skill_names)
    support_score = _coverage_score(names(skill_group.support_skill_ids), task.expected_support_skill_names)
    check_score = _coverage_score(names(skill_group.check_skill_ids), task.expected_check_skill_names)
    avoid_score = _coverage_score(names(skill_group.avoid_skill_ids), task.expected_avoid_skill_names)
    return round(0.35 * start_score + 0.25 * support_score + 0.25 * check_score + 0.15 * avoid_score, 4)


def _composition_score(graph: Any, id_to_name: Dict[str, str], task: RuntimeBenchmarkTask) -> float:
    if not task.expected_edges:
        return 1.0 if _dag_valid(graph) else 0.0
    actual_edges = {
        (id_to_name.get(edge.source_id, edge.source_id), id_to_name.get(edge.target_id, edge.target_id))
        for edge in getattr(graph, "edges", [])
        if getattr(edge, "edge_type", "sequence") != "parallel"
    }
    expected_edges = set(task.expected_edges)
    edge_score = len(actual_edges & expected_edges) / len(expected_edges)
    validity = 1.0 if _dag_valid(graph) else 0.0
    parallel_score = 1.0 if getattr(graph, "parallel_groups", None) is not None else 0.0
    return round(0.55 * edge_score + 0.35 * validity + 0.10 * parallel_score, 4)


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


def _recovery_score(verification: Any, task: RuntimeBenchmarkTask) -> float:
    expected_type = task.expected_failure_type
    expected_route = task.expected_recovery_route
    if not expected_type and not expected_route:
        return 1.0
    failure_type = str(getattr(verification, "failure_type", ""))
    recovery_route = str(getattr(verification, "recovery_route", ""))
    type_score = 1.0 if not expected_type or failure_type == expected_type else 0.0
    route_score = 1.0 if not expected_route or recovery_route == expected_route else 0.0
    return round(0.6 * type_score + 0.4 * route_score, 4)


def _memory_score(runtime_memory: Any, task: RuntimeBenchmarkTask) -> float:
    if runtime_memory is None:
        return 0.0
    summary = runtime_memory.to_summary()
    selected = set(summary.get("selected_skills", []))
    step_count = int(summary.get("step_count", 0) or 0)
    event_count = int(summary.get("events", 0) or 0)
    selected_score = 1.0 if selected or not task.expected_skill_names else 0.0
    step_score = 1.0 if step_count or not task.expected_skill_names else 0.0
    event_score = 1.0 if event_count else 0.0
    return round(0.35 * selected_score + 0.35 * step_score + 0.30 * event_score, 4)


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


def _dag_valid(graph: Any) -> bool:
    node_ids = {node.skill_id for node in getattr(graph, "nodes", [])}
    seen = set()
    for edge in getattr(graph, "edges", []):
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            return False
        if edge.source_id == edge.target_id:
            return False
        key = (edge.source_id, edge.target_id, edge.edge_type)
        if key in seen:
            return False
        seen.add(key)
    return len(getattr(graph, "execution_order", [])) == len(node_ids)


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
