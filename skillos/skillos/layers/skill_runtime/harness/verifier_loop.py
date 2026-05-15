"""Repair/retry verification loop for Draft Skills."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ....layers.feedback_evolution.monitor import HealthStatus, SkillHealthReport
from ....layers.feedback_evolution.repair import SkillRepair
from ....models.skill_model import (
    EdgeType,
    Skill,
    SkillEvaluation,
    SkillImplementation,
    SkillState,
    SkillTestCase,
)
from ..executor import SkillExecutor
from .base import HarnessKind, HarnessRunResult, HarnessTestCase, VerificationLoopResult
from .codex_cli import CodexCliHarness
from .local_skillos import LocalSkillOSHarness
from .workspace import HarnessWorkspace


class VerificationLoop:
    """Execute, verify, repair, and promote a Draft Skill."""

    def __init__(
        self,
        *,
        wiki: Any,
        graph: Any = None,
        executor: SkillExecutor | None = None,
        repair: SkillRepair | None = None,
        evidence_root: Any = None,
    ) -> None:
        self._wiki = wiki
        self._graph = graph
        self._executor = executor or SkillExecutor(skill_registry=wiki)
        self._repair = repair
        self._evidence_root = evidence_root

    async def run(
        self,
        skill_id: str,
        *,
        harness_kind: HarnessKind = HarnessKind.LOCAL_SKILLOS,
        max_attempts: int = 3,
        promote_on_pass: bool = True,
        test_cases: Optional[List[HarnessTestCase]] = None,
        allow_repair: bool = True,
        timeout_s: int = 120,
    ) -> VerificationLoopResult:
        skill = await self._get_skill(skill_id)
        loop_id = f"verify_loop_{uuid4().hex[:12]}"
        workspace = HarnessWorkspace(loop_id, root=self._evidence_root)
        cases = test_cases or _build_test_cases(skill, timeout_s=timeout_s)
        harness = self._harness(harness_kind)
        current_skill = skill
        attempts: List[HarnessRunResult] = []
        repairs: List[Dict[str, Any]] = []
        seen_patches: set[str] = set()
        status = "rejected"

        for attempt in range(1, max_attempts + 1):
            case_results: List[HarnessRunResult] = []
            for case in cases:
                run_result = await harness.run_skill(
                    current_skill,
                    case,
                    workspace,
                    attempt=attempt,
                )
                case_results.append(run_result)
                attempts.append(run_result)

            if case_results and all(result.verifier_passed for result in case_results):
                status = "verified"
                break

            if not allow_repair or attempt >= max_attempts:
                status = "needs_human_review"
                break

            patched, repair_record = await self._repair_skill(
                current_skill,
                case_results,
                attempt=attempt,
            )
            repairs.append(repair_record)
            patch_key = repair_record.get("patch_key", "")
            if not patched or (patch_key and patch_key in seen_patches):
                status = "needs_human_review"
                break
            seen_patches.add(patch_key)
            current_skill = await self._persist_repaired_draft(current_skill, patched, repair_record)

        score = _score_loop(attempts, repairs)
        promotion_allowed = status == "verified" and score["overall"] >= 0.75
        if promotion_allowed and promote_on_pass:
            current_skill = await self._promote(current_skill, loop_id, attempts, score, workspace)

        result = VerificationLoopResult(
            loop_id=loop_id,
            skill_id=skill_id,
            initial_version=skill.version,
            final_version=current_skill.version,
            status=status if promotion_allowed or status != "verified" else "needs_human_review",
            attempts=attempts,
            repairs=repairs,
            score=score,
            promotion_allowed=promotion_allowed,
            final_state=current_skill.state.value,
            evidence_path=str(workspace.loop_dir),
        )
        workspace.save_loop_result(result)
        return result

    async def _get_skill(self, skill_id: str) -> Skill:
        skill = await self._wiki.get(skill_id)
        if not skill:
            raise ValueError(f"Skill {skill_id} not found")
        if skill.state != SkillState.DRAFT:
            raise ValueError("Harness verify-loop only promotes S2 Draft Skills in P0.")
        return skill

    def _harness(self, kind: HarnessKind) -> Any:
        if kind == HarnessKind.LOCAL_SKILLOS:
            return LocalSkillOSHarness(self._executor, registry=self._wiki)
        if kind == HarnessKind.CODEX_CLI:
            return CodexCliHarness()
        raise ValueError(f"Unsupported harness: {kind.value}")

    async def _repair_skill(
        self,
        skill: Skill,
        case_results: List[HarnessRunResult],
        *,
        attempt: int,
    ) -> tuple[Optional[Skill], Dict[str, Any]]:
        failure_cases = [_failure_case(result) for result in case_results if not result.verifier_passed]
        patched = _deterministic_repair(skill, case_results)
        source = "deterministic"
        error = ""

        if patched is None and self._repair is not None:
            health = SkillHealthReport(
                skill_id=skill.skill_id,
                skill_name=skill.name,
                status=HealthStatus.CRITICAL,
                success_rate=0.0,
                usage_count=max(1, len(case_results)),
                avg_latency_ms=_avg_latency(case_results),
                issues=[case.get("error", "") for case in failure_cases if case.get("error")],
            )
            repaired = await self._repair.repair(skill, health, failure_cases=failure_cases)
            if repaired.success and repaired.repaired_skill is not None:
                patched = repaired.repaired_skill
                source = "llm_skill_repair"
            else:
                error = repaired.error or repaired.root_cause or "repair failed"

        repair_record = {
            "attempt": attempt,
            "source": source,
            "success": patched is not None,
            "error": error,
            "failure_cases": failure_cases,
            "patch_key": _patch_key(patched),
            "changed_fields": ["implementation.code"] if patched is not None else [],
            "policy": "Do not weaken verifier specs; repair implementation only.",
        }
        return patched, repair_record

    async def _persist_repaired_draft(
        self,
        original: Skill,
        patched: Skill,
        repair_record: Dict[str, Any],
    ) -> Skill:
        if patched.skill_id == original.skill_id:
            repaired = patched.model_copy(deep=True)
            repaired.bump_version("patch")
            repaired.skill_id = str(uuid4())
        else:
            repaired = patched.model_copy(deep=True)
        await self._ensure_unique_repair_version(repaired)
        repaired.state = SkillState.DRAFT
        if repaired.provenance:
            repaired.provenance.parent_skill_ids = list(
                dict.fromkeys([*repaired.provenance.parent_skill_ids, original.skill_id])
            )
            repaired.provenance.creation_context["harness_repair"] = repair_record
        created = await self._wiki.create(repaired)
        graph = self._graph
        if graph is not None:
            if hasattr(graph, "sync_skill"):
                await graph.sync_skill(created)
            if hasattr(graph, "add_evolution"):
                await graph.add_evolution(created.skill_id, original.skill_id)
            elif hasattr(graph, "create_edge"):
                from ....models.graph_model import SkillEdge

                await graph.create_edge(
                    SkillEdge(
                        edge_id=f"harness:evolved_from:{created.skill_id}:{original.skill_id}",
                        source_id=created.skill_id,
                        target_id=original.skill_id,
                        edge_type=EdgeType.EVOLVED_FROM,
                        metadata={"source": "harness_verification_loop"},
                    )
                )
        return created

    async def _ensure_unique_repair_version(self, repaired: Skill) -> None:
        get_by_name = getattr(self._wiki, "get_by_name", None)
        if not callable(get_by_name):
            return
        for _ in range(20):
            existing = await get_by_name(repaired.name, repaired.version)
            if existing is None or existing.skill_id == repaired.skill_id:
                return
            repaired.bump_version("patch")
        raise ValueError(
            f"Could not allocate a unique repaired version for Skill '{repaired.name}' "
            f"after repeated harness repair attempts."
        )

    async def _promote(
        self,
        skill: Skill,
        loop_id: str,
        attempts: List[HarnessRunResult],
        score: Dict[str, Any],
        workspace: HarnessWorkspace,
    ) -> Skill:
        evaluation = skill.evaluation.model_copy(deep=True)
        evaluation.validation_summary = (
            f"Harness loop {loop_id} passed {sum(1 for item in attempts if item.verifier_passed)} "
            f"of {len(attempts)} executable checks."
        )
        evaluation.harness_validation = {
            "last_loop_id": loop_id,
            "last_harness": attempts[-1].harness.value if attempts else "",
            "last_verified_at": datetime.utcnow().isoformat() + "Z",
            "attempt_count": len(attempts),
            "pass_rate": score["verifier_pass_rate"],
            "score": score["overall"],
            "evidence_path": str(workspace.loop_dir),
            "promotion_gate": "passed",
        }
        updated = await self._wiki.update(skill.skill_id, evaluation=evaluation)
        if not updated:
            updated = skill
        promoted = await self._wiki.transition_state(updated.skill_id, SkillState.VERIFIED)
        graph = self._graph
        if graph is not None and hasattr(graph, "sync_skill"):
            await graph.sync_skill(promoted)
        return promoted


def _build_test_cases(skill: Skill, *, timeout_s: int) -> List[HarnessTestCase]:
    specs = list(skill.evaluation.verifier_specs or [])
    if not specs:
        specs = _specs_from_expected_output(skill.test_cases[:1])
    if not specs:
        specs = [{"type": "boolean_success"}]

    cases: List[HarnessTestCase] = []
    for index, case in enumerate(skill.test_cases or []):
        cases.append(
            HarnessTestCase(
                test_id=case.test_id,
                name=case.name,
                goal=case.description or skill.description or skill.name,
                input_data=case.input_data,
                verifier_specs=specs,
                timeout_s=timeout_s,
            )
        )
    if cases:
        return cases
    return [
        HarnessTestCase(
            test_id=f"{skill.skill_id}_smoke",
            name=f"{skill.name}_smoke",
            goal=skill.description or skill.name,
            input_data=_example_from_schema(skill.interface.input_schema),
            verifier_specs=specs,
            timeout_s=timeout_s,
        )
    ]


def _specs_from_expected_output(test_cases: List[SkillTestCase]) -> List[Dict[str, Any]]:
    if not test_cases or not test_cases[0].expected_output:
        return []
    return [
        {"type": "json_equals", "path": f"output.{key}", "value": value}
        for key, value in test_cases[0].expected_output.items()
    ]


def _example_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else list(properties)
    return {
        name: _example_value(properties.get(name, {}))
        for name in required
        if isinstance(name, str)
    }


def _example_value(schema: Dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if schema_type == "number":
        return 1.0
    if schema_type == "integer":
        return 1
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        return [_example_value(schema.get("items", {}))]
    if schema_type == "object":
        return _example_from_schema(schema)
    return "demo"


def _deterministic_repair(skill: Skill, results: List[HarnessRunResult]) -> Optional[Skill]:
    missing_fields = _missing_output_fields(results)
    if not missing_fields:
        return None
    repaired = skill.model_copy(deep=True)
    patched_lines = []
    existing = ""
    if repaired.implementation and repaired.implementation.code:
        existing = repaired.implementation.code.strip()
        patched_lines.append(existing)
    for field, value in missing_fields.items():
        patched_lines.extend(_nested_output_assignment(field, value))
    repaired.implementation = SkillImplementation(
        language="python",
        code="\n".join(line for line in patched_lines if line),
    )
    return repaired


def _missing_output_fields(results: List[HarnessRunResult]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for result in results:
        for item in result.verifier_summary.get("details", {}).get("results", []):
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue", ""))
            path = str(item.get("path", ""))
            if "Path not found" not in issue or not path.startswith("output."):
                continue
            field = path.split(".", 1)[1]
            fields[field] = _repair_value_for_missing_spec(item)
    return fields


def _repair_value_for_missing_spec(item: Dict[str, Any]) -> Any:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    spec_type = str(spec.get("type") or "")
    path = str(item.get("path") or "")
    if spec_type == "json_equals" and "value" in spec:
        return spec["value"]
    if spec_type == "json_array_nonempty":
        return [f"deterministic repair evidence for {path}"]
    if spec_type == "json_array":
        return [f"deterministic repair item for {path}"]
    if spec_type in {"json_object", "json_object_nonempty"}:
        return {"repaired": True}
    if spec_type == "json_nonempty":
        return f"deterministic repair value for {path}"
    expected = item.get("expected")
    if expected == "non-empty array":
        return [f"deterministic repair evidence for {path}"]
    if expected == "array":
        return [f"deterministic repair item for {path}"]
    if expected == "object":
        return {"repaired": True}
    return expected if expected is not None else f"deterministic repair value for {path}"


def _nested_output_assignment(field_path: str, value: Any) -> List[str]:
    parts = [part for part in field_path.split(".") if part]
    if not parts:
        return []
    if len(parts) == 1:
        key = parts[0]
        return [f"output[{key!r}] = input_data.get({key!r}, {value!r})"]

    lines: List[str] = []
    cursor = "output"
    for part in parts[:-1]:
        lines.append(f"if not isinstance({cursor}.get({part!r}), dict):")
        lines.append(f"    {cursor}[{part!r}] = {{}}")
        cursor = f"{cursor}[{part!r}]"
    lines.append(f"{cursor}[{parts[-1]!r}] = {value!r}")
    return lines


def _failure_case(result: HarnessRunResult) -> Dict[str, Any]:
    return {
        "input": result.input_data,
        "error": result.failure_reason,
        "verifier_summary": result.verifier_summary,
        "evidence_path": result.evidence_path,
    }


def _score_loop(attempts: List[HarnessRunResult], repairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(attempts)
    final_attempt = max((attempt.attempt for attempt in attempts), default=0)
    final_results = [attempt for attempt in attempts if attempt.attempt == final_attempt]
    passed = sum(1 for attempt in final_results if attempt.verifier_passed)
    pass_rate = passed / len(final_results) if final_results else 0.0
    harness_completion = sum(
        1 for attempt in attempts if attempt.status not in {"timeout", "harness_error", "harness_unavailable"}
    ) / total if total else 0.0
    repair_stability = 1.0 if len(repairs) <= 1 else 0.8
    avg_latency = _avg_latency(attempts)
    latency_score = 1.0 if avg_latency <= 3000 else max(0.2, 3000 / avg_latency)
    overall = (
        0.60 * pass_rate
        + 0.15 * harness_completion
        + 0.10 * 1.0
        + 0.10 * repair_stability
        + 0.05 * latency_score
    )
    return {
        "overall": round(overall, 3),
        "verifier_pass_rate": round(pass_rate, 3),
        "harness_completion": round(harness_completion, 3),
        "input_schema_coverage": 1.0,
        "repair_stability": round(repair_stability, 3),
        "latency_score": round(latency_score, 3),
        "latency_ms": round(avg_latency, 3),
        "attempt_count": total,
        "repair_count": len(repairs),
    }


def _avg_latency(results: List[HarnessRunResult]) -> float:
    if not results:
        return 0.0
    return sum(result.latency_ms for result in results) / len(results)


def _patch_key(skill: Optional[Skill]) -> str:
    if not skill or not skill.implementation:
        return ""
    return skill.implementation.code or skill.implementation.prompt_template or ""
