"""Runtime reflection agent.

Reflection turns execution traces and verification results into feedback that
can be consumed by D-side maintenance agents. It does not mutate Skills.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)

_ALLOWED_ACTIONS = {"repair", "deprecate", "review", "no_action"}


@dataclass
class Feedback:
    task_id: str
    goal: str
    success: bool
    root_cause: str = ""
    failure_type: str = ""
    recovery_route: str = ""
    failed_skill_ids: List[str] = field(default_factory=list)
    improvement_suggestions: List[str] = field(default_factory=list)
    skill_update_proposals: List[Dict[str, Any]] = field(default_factory=list)
    experience_summary: str = ""


_REFLECT_PROMPT = """
Analyze a SkillOS runtime execution and produce maintenance-oriented feedback.

Goal:
{goal}

Execution success:
{success}

Execution trace:
{trace}

Verification result:
{verification}

Rules:
- Return JSON only. Do not include Markdown or commentary.
- Do not claim that a Skill was repaired. Only propose follow-up actions.
- failed_skill_ids must only include concrete skill ids from the trace.
- recommended_action must be one of: repair, deprecate, review, no_action.
- failure_type should align with the verifier when provided.
- recovery_route should describe the runtime recovery strategy.
- Use repair for implementation/prompt/runtime failures.
- Use review when the issue is uncertain or needs human inspection.

Return this JSON shape:
{{
  "root_cause": "brief root cause",
  "failure_type": "runtime_error",
  "recovery_route": "repair_skill",
  "failed_skill_ids": ["skill_id_1"],
  "improvement_suggestions": ["short suggestion"],
  "skill_update_proposals": [
    {{
      "skill_id": "skill_id_1",
      "issue": "what failed",
      "proposed_fix": "what D Maintainer should inspect or repair",
      "recommended_action": "repair",
      "evidence": ["step failed with timeout"]
    }}
  ],
  "experience_summary": "brief reusable experience summary"
}}
"""


class ReflectionAgent:
    """Analyze execution traces and produce Skill improvement feedback."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def reflect(
        self,
        task_id: str,
        goal: str,
        trace: Dict[str, Any],
        verification_result: Optional[Any] = None,
    ) -> Feedback:
        """Reflect on an execution result."""

        success = verification_result.passed if verification_result else bool(trace)
        trace_str = json.dumps(trace, ensure_ascii=False, indent=2)[:1200]
        verify_str = _format_verification(verification_result)

        prompt = _REFLECT_PROMPT.format(
            goal=goal,
            success=success,
            trace=trace_str,
            verification=verify_str,
        )

        try:
            response = self._llm.chat([
                Message.system(
                    "You are the SkillOS Reflection Agent. Return strict JSON only."
                ),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                return _normalize_feedback(data, task_id, goal, success)
        except Exception as exc:
            logger.warning("Reflection LLM failed: %s", exc)

        return _fallback_feedback(task_id, goal, trace, verification_result, success)

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None


def _format_verification(verification_result: Optional[Any]) -> str:
    if not verification_result:
        return "(no verification result)"
    return json.dumps(
        {
            "passed": bool(getattr(verification_result, "passed", False)),
            "score": getattr(verification_result, "score", 0.0),
            "issues": getattr(verification_result, "issues", []),
            "suggestions": getattr(verification_result, "suggestions", []),
        },
        ensure_ascii=False,
    )


def _normalize_feedback(
    data: Dict[str, Any],
    task_id: str,
    goal: str,
    success: bool,
) -> Feedback:
    proposals = _normalize_proposals(data.get("skill_update_proposals", []))
    return Feedback(
        task_id=task_id,
        goal=goal,
        success=success,
        root_cause=str(data.get("root_cause", "")),
        failure_type=str(data.get("failure_type", "")),
        recovery_route=str(data.get("recovery_route", "")),
        failed_skill_ids=_string_list(data.get("failed_skill_ids", [])),
        improvement_suggestions=_string_list(data.get("improvement_suggestions", [])),
        skill_update_proposals=proposals,
        experience_summary=str(data.get("experience_summary", "")),
    )


def _fallback_feedback(
    task_id: str,
    goal: str,
    trace: Dict[str, Any],
    verification_result: Optional[Any],
    success: bool,
) -> Feedback:
    issues = _string_list(getattr(verification_result, "issues", []))
    failed_skill_ids = _extract_failed_skill_ids(trace)

    if success:
        return Feedback(
            task_id=task_id,
            goal=goal,
            success=True,
            failure_type="none",
            recovery_route="none",
            experience_summary="Task completed successfully.",
        )

    evidence = issues or _extract_failure_evidence(trace)
    root_cause = evidence[0] if evidence else "Runtime execution did not satisfy the goal."
    failure_type = str(getattr(verification_result, "failure_type", "") or _classify_trace_failure(trace, root_cause))
    recovery_route = str(getattr(verification_result, "recovery_route", "") or _route_for_failure(failure_type))
    suggestions = _string_list(getattr(verification_result, "suggestions", []))
    if not suggestions:
        suggestions = ["Review failed runtime steps and repair the related skill if needed."]

    proposals = [
        {
            "skill_id": skill_id,
            "issue": root_cause,
            "proposed_fix": (
                _proposal_fix_for_route(recovery_route)
            ),
            "recommended_action": "repair",
            "failure_type": failure_type,
            "recovery_route": recovery_route,
            "evidence": evidence,
        }
        for skill_id in failed_skill_ids
    ]

    return Feedback(
        task_id=task_id,
        goal=goal,
        success=False,
        root_cause=root_cause,
        failure_type=failure_type,
        recovery_route=recovery_route,
        failed_skill_ids=failed_skill_ids,
        improvement_suggestions=suggestions,
        skill_update_proposals=proposals,
        experience_summary="Task failed; runtime reflection generated repair guidance.",
    )


def _normalize_proposals(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    proposals: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id", "")).strip()
        if not skill_id:
            continue
        action = str(item.get("recommended_action", "review")).strip()
        if action not in _ALLOWED_ACTIONS:
            action = "review"
        proposals.append(
            {
                "skill_id": skill_id,
                "issue": str(item.get("issue", "")),
                "proposed_fix": str(item.get("proposed_fix", "")),
                "recommended_action": action,
                "failure_type": str(item.get("failure_type", "")),
                "recovery_route": str(item.get("recovery_route", "")),
                "evidence": _string_list(item.get("evidence", [])),
            }
        )
    return proposals


def _extract_failed_skill_ids(trace: Any) -> List[str]:
    ids: List[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            status = str(value.get("status", "")).lower()
            has_error = any(key in value and value[key] for key in ("error", "error_message"))
            if status in {"failed", "timeout"} or has_error:
                skill_id = value.get("skill_id")
                if skill_id:
                    ids.append(str(skill_id))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(trace)
    return list(dict.fromkeys(ids))


def _extract_failure_evidence(trace: Any) -> List[str]:
    evidence: List[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            status = str(value.get("status", "")).lower()
            error = value.get("error") or value.get("error_message")
            if status in {"failed", "skipped", "timeout"}:
                evidence.append(f"step status: {status}")
            if error:
                evidence.append(str(error))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(trace)
    return list(dict.fromkeys(evidence))


def _classify_trace_failure(trace: Any, root_cause: str) -> str:
    text = f"{json.dumps(trace, ensure_ascii=False)}\n{root_cause}".lower()
    if "skill not found" in text or "missing skill" in text:
        return "missing_skill"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "skipped" in text or "dependency failed" in text:
        return "dependency_failed"
    if "exception" in text or "runtimeerror" in text or "error" in text:
        return "runtime_error"
    return "unknown"


def _route_for_failure(failure_type: str) -> str:
    routes = {
        "missing_skill": "retrieve_alternative_skill",
        "timeout": "retry_with_timeout_adjustment",
        "runtime_error": "repair_skill",
        "dependency_failed": "replan_dependencies",
        "postcondition_failed": "add_postcondition_check",
        "bad_output": "inspect_output",
    }
    return routes.get(failure_type, "review")


def _proposal_fix_for_route(route: str) -> str:
    fixes = {
        "retrieve_alternative_skill": "Review retrieval candidates or create a missing Skill through the maintenance flow.",
        "retry_with_timeout_adjustment": "Review timeout behavior and consider a lighter implementation or adjusted timeout.",
        "repair_skill": "Review runtime failure and repair the skill implementation or prompt.",
        "replan_dependencies": "Review failed dependencies and adjust the execution plan or skill prerequisites.",
        "add_postcondition_check": "Add or strengthen postcondition checks for this skill.",
        "inspect_output": "Inspect the output contract and repair schema or result formatting.",
    }
    return fixes.get(route, "Review runtime evidence and decide whether repair is required.")


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item)]
