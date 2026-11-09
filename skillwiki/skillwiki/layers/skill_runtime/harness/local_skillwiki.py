"""Local SkillWiki executor harness."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from time import perf_counter
from typing import Any, Dict
from uuid import uuid4

from ....models.experience_model import ExecutionStatus
from ....models.skill_model import Skill
from ..executor import SkillExecutor
from ..verifier import evaluate_verifier_specs
from .base import HarnessKind, HarnessRunResult, HarnessTestCase
from .workspace import HarnessWorkspace


class LocalSkillWikiHarness:
    """Execute a Skill with the existing in-process SkillExecutor."""

    kind = HarnessKind.LOCAL_SKILLWIKI

    def __init__(self, executor: SkillExecutor | None = None, registry: Any = None) -> None:
        self._executor = executor or SkillExecutor(skill_registry=registry)

    async def run_skill(
        self,
        skill: Skill,
        test_case: HarnessTestCase,
        workspace: HarnessWorkspace,
        *,
        attempt: int = 1,
    ) -> HarnessRunResult:
        run_id = f"harness_run_{uuid4().hex[:12]}"
        attempt_path = workspace.attempt_dir(attempt) / run_id
        attempt_path.mkdir(parents=True, exist_ok=False)
        workspace.write_json(attempt_path / "input.json", test_case.input_data)
        workspace.write_json(
            attempt_path / "verifier_specs.json",
            {"verifier_specs": test_case.verifier_specs},
        )

        start = perf_counter()
        previous_timeout = getattr(self._executor, "_step_timeout", None)
        if previous_timeout is not None:
            self._executor._step_timeout = float(test_case.timeout_s)
        try:
            record = await self._executor.execute_single(
                skill,
                test_case.input_data,
                task_id=f"{workspace.loop_id}:{test_case.test_id}",
                initial_state=test_case.context,
            )
        finally:
            if previous_timeout is not None:
                self._executor._step_timeout = previous_timeout
        latency_ms = (perf_counter() - start) * 1000

        output = record.output_data or {}
        payload = {
            "input": test_case.input_data,
            "output": output,
            "final_state": record.state_after,
            "record": _jsonable(record),
        }
        if record.status != ExecutionStatus.SUCCESS:
            verifier_passed = False
            verifier_summary: Dict[str, Any] = {
                "passed": False,
                "score": 0.0,
                "issues": [record.error_message or "Skill execution failed."],
                "details": {"record_status": record.status.value},
            }
            failure_reason = record.error_message or record.status.value
            status = "failed"
        elif not test_case.verifier_specs:
            verifier_passed = False
            verifier_summary = {'passed': False, 'score': 0.0, 'issues': ['No evaluation contract provided.'], 'details': {'evaluation_status': 'not_evaluated'}}
            failure_reason = 'Execution completed; task correctness was not evaluated.'
            status = 'not_evaluated'
        else:
            verification = evaluate_verifier_specs(
                test_case.verifier_specs,
                payload,
                goal=test_case.goal,
            )
            verifier_passed = verification.passed
            verifier_summary = _verification_summary(verification)
            failure_reason = "; ".join(verification.issues)
            status = "passed" if verification.passed else "failed"

        result = HarnessRunResult(
            run_id=run_id,
            skill_id=skill.skill_id,
            attempt=attempt,
            harness=self.kind,
            status=status,
            input_data=test_case.input_data,
            output=payload,
            artifacts=[],
            verifier_passed=verifier_passed,
            verifier_summary=verifier_summary,
            latency_ms=record.latency_ms or latency_ms,
            failure_reason=failure_reason,
            evidence_path=str(attempt_path),
        )
        workspace.write_json(attempt_path / "output.json", payload)
        workspace.write_json(attempt_path / "result.json", result.model_dump(mode="json"))
        # Preserve the historical single-case evidence location as a stable
        # index; individual run directories remain authoritative for batches.
        attempt_index = attempt_path.parent / "result.json"
        if not attempt_index.exists():
            workspace.write_json(attempt_index, result.model_dump(mode="json"))
        return result


def _verification_summary(verification: Any) -> Dict[str, Any]:
    return {
        "passed": verification.passed,
        "score": verification.score,
        "issues": list(verification.issues),
        "suggestions": list(verification.suggestions),
        "details": dict(verification.details),
    }


def _jsonable(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    return {}
