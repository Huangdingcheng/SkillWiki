"""Runtime result verifier."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class VerificationResult:
    passed: bool
    score: float
    goal: str
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerifierSpecResult:
    spec: Dict[str, Any]
    passed: bool
    path: str = ""
    actual: Any = None
    expected: Any = None
    issue: str = ""


_VERIFY_PROMPT = """
Verify whether the runtime result satisfies the user goal.

Goal:
{goal}

Execution trace summary:
{trace_summary}

Final output:
{final_output}

Rules:
- Return JSON only. Do not include Markdown or commentary.
- passed must be true only when the result substantially satisfies the goal.
- score must be a number from 0 to 1.
- issues and suggestions must be arrays of short strings.
- Mention failed, skipped, timeout, missing skill, and error evidence when present.

Return this JSON shape:
{{
  "passed": true,
  "score": 0.85,
  "issues": ["missing expected field"],
  "suggestions": ["retry with a more specific skill"],
  "reasoning": "brief verification reasoning"
}}
"""


class VerifierAgent:
    """Verify whether runtime output satisfies a goal."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def verify(
        self,
        goal: str,
        final_output: Dict[str, Any],
        trace_summary: Optional[str] = None,
        verifier_specs: Optional[List[Dict[str, Any]]] = None,
    ) -> VerificationResult:
        """Verify an execution result."""

        if verifier_specs:
            return evaluate_verifier_specs(verifier_specs, final_output, goal=goal)

        prompt = _VERIFY_PROMPT.format(
            goal=goal,
            trace_summary=trace_summary or "(no trace summary)",
            final_output=json.dumps(final_output, ensure_ascii=False, indent=2)[:1000],
        )

        try:
            response = self._llm.chat([
                Message.system(
                    "You are the SkillOS Verifier Agent. Return strict JSON only."
                ),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                return _normalize_verification(data, goal)
        except Exception as exc:
            logger.warning("Verifier LLM failed: %s", exc)

        return _fallback_verification(goal, final_output, trace_summary or "")

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


def evaluate_verifier_specs(
    verifier_specs: Any,
    final_output: Dict[str, Any],
    goal: str = "",
) -> VerificationResult:
    """Evaluate deterministic verifier specs against a runtime output document."""

    specs = _normalize_specs(verifier_specs)
    if not specs:
        return VerificationResult(
            passed=False,
            score=0.0,
            goal=goal,
            issues=["No verifier specs provided."],
            suggestions=["Attach at least one deterministic verifier spec."],
            details={"verifier": "deterministic", "results": []},
        )

    document = _verifier_document(final_output)
    results = [_evaluate_spec(spec, document) for spec in specs]
    passed_count = sum(1 for result in results if result.passed)
    passed = passed_count == len(results)
    issues = [result.issue for result in results if result.issue]
    suggestions = [] if passed else ["Inspect deterministic verifier failures before marking the Skill valid."]

    return VerificationResult(
        passed=passed,
        score=passed_count / len(results),
        goal=goal,
        issues=issues,
        suggestions=suggestions,
        details={
            "verifier": "deterministic",
            "results": [asdict(result) for result in results],
        },
    )


def _normalize_specs(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(value)]
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _verifier_document(final_output: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(final_output, dict):
        return {"output": final_output, "final_state": final_output}
    if "output" in final_output:
        return final_output
    document = dict(final_output)
    document["output"] = final_output
    document.setdefault("final_state", final_output)
    return document


def _evaluate_spec(spec: Dict[str, Any], document: Dict[str, Any]) -> VerifierSpecResult:
    spec_type = str(spec.get("type", "")).strip()
    path = str(spec.get("path", "")).strip()
    expected = spec.get("value", spec.get("expected"))

    if not spec_type:
        return VerifierSpecResult(spec=spec, passed=False, issue="Verifier spec type is required.")

    if spec_type == "boolean_success":
        if path:
            actual = _resolve_path(document, path)
            if actual is _MISSING:
                return VerifierSpecResult(
                    spec=spec,
                    passed=False,
                    path=path,
                    issue=f"Path not found: {path}",
                )
            return VerifierSpecResult(
                spec=spec,
                passed=actual is True,
                path=path,
                actual=actual,
                expected=True,
                issue="" if actual is True else f"Expected {path} to be true.",
            )
        passed = bool(document) and not _has_false_success(document)
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            expected=True,
            issue="" if passed else "Output contains failure evidence.",
        )

    if not path:
        return VerifierSpecResult(
            spec=spec,
            passed=False,
            issue=f"{spec_type} verifier requires a path.",
        )

    actual = _resolve_path(document, path)
    if actual is _MISSING:
        return VerifierSpecResult(
            spec=spec,
            passed=False,
            path=path,
            expected=expected,
            issue=f"Path not found: {path}",
        )

    if spec_type == "json_exists":
        return VerifierSpecResult(spec=spec, passed=True, path=path, actual=actual)

    if spec_type == "json_nonempty":
        passed = _is_nonempty_value(actual)
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            path=path,
            actual=actual,
            issue="" if passed else f"Expected {path} to be non-empty.",
        )

    if spec_type == "json_equals":
        passed = actual == expected
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            path=path,
            actual=actual,
            expected=expected,
            issue="" if passed else f"Expected {path} == {expected!r}, got {actual!r}.",
        )

    if spec_type == "json_array":
        passed = isinstance(actual, list)
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            path=path,
            actual=actual,
            expected="array",
            issue="" if passed else f"Expected {path} to be an array.",
        )

    if spec_type == "json_array_nonempty":
        passed = isinstance(actual, list) and len(actual) > 0
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            path=path,
            actual=actual,
            expected="non-empty array",
            issue="" if passed else f"Expected {path} to be a non-empty array.",
        )

    if spec_type == "json_object":
        passed = isinstance(actual, dict)
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            path=path,
            actual=actual,
            expected="object",
            issue="" if passed else f"Expected {path} to be an object.",
        )

    if spec_type == "json_object_nonempty":
        passed = isinstance(actual, dict) and len(actual) > 0
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            path=path,
            actual=actual,
            expected="non-empty object",
            issue="" if passed else f"Expected {path} to be a non-empty object.",
        )

    if spec_type == "contains":
        passed = _contains_value(actual, expected)
        return VerifierSpecResult(
            spec=spec,
            passed=passed,
            path=path,
            actual=actual,
            expected=expected,
            issue="" if passed else f"Expected {path} to contain {expected!r}.",
        )

    return VerifierSpecResult(
        spec=spec,
        passed=False,
        path=path,
        expected=expected,
        issue=f"Unsupported verifier type: {spec_type}",
    )


_MISSING = object()


def _resolve_path(document: Any, path: str) -> Any:
    current = document
    normalized = path.strip()
    if normalized in {"", "$"}:
        return current
    if normalized.startswith("$."):
        normalized = normalized[2:]
    elif normalized.startswith("."):
        normalized = normalized[1:]

    for raw_token in normalized.split("."):
        token = raw_token.strip()
        if not token:
            return _MISSING
        current = _resolve_token(current, token)
        if current is _MISSING:
            return _MISSING
    return current


def _resolve_token(current: Any, token: str) -> Any:
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)(?:\[(\d+)\])?", token)
    if match:
        key = match.group(1)
        index = match.group(2)
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return _MISSING
        if index is not None:
            if isinstance(current, list) and int(index) < len(current):
                return current[int(index)]
            return _MISSING
        return current

    if isinstance(current, list) and token.isdigit():
        index = int(token)
        return current[index] if index < len(current) else _MISSING
    if isinstance(current, dict) and token in current:
        return current[token]
    return _MISSING


def _contains_value(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str):
        return str(expected) in actual
    if isinstance(actual, list):
        return expected in actual
    if isinstance(actual, dict):
        return any(key == expected for key in actual) or any(
            value == expected for value in actual.values()
        )
    return str(expected) in str(actual)


def _is_nonempty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _normalize_verification(data: Dict[str, Any], goal: str) -> VerificationResult:
    score = _clamp_float(data.get("score", 0.0))
    passed = bool(data.get("passed", score >= 0.6))
    issues = _string_list(data.get("issues", []))
    suggestions = _string_list(data.get("suggestions", []))
    return VerificationResult(
        passed=passed,
        score=score,
        goal=goal,
        issues=issues,
        suggestions=suggestions,
        details={"reasoning": str(data.get("reasoning", ""))},
    )


def _fallback_verification(
    goal: str,
    final_output: Dict[str, Any],
    trace_summary: str,
) -> VerificationResult:
    issues: List[str] = []
    suggestions: List[str] = []

    if not final_output:
        issues.append("Execution output is empty.")

    output_text = json.dumps(final_output, ensure_ascii=False).lower()
    trace_text = trace_summary.lower()
    combined = f"{output_text}\n{trace_text}"

    if _has_false_success(final_output):
        issues.append("Execution output reports success=false or ok=false.")
    if any(token in combined for token in ("error", "exception", "failed", "timeout")):
        issues.append("Execution trace or output contains failure evidence.")
    if "skipped" in combined:
        issues.append("Execution trace contains skipped steps.")

    passed = bool(final_output) and not issues
    score = 0.65 if passed else 0.2
    if not passed:
        suggestions.append("Inspect failed runtime steps and repair the related skill if needed.")

    return VerificationResult(
        passed=passed,
        score=score,
        goal=goal,
        issues=issues,
        suggestions=suggestions,
        details={"reasoning": "Rule-based fallback verification."},
    )


def _has_false_success(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower()
            if key_lower in {"success", "ok"} and item is False:
                return True
            if key_lower == "status" and str(item).lower() in {
                "cancelled",
                "error",
                "failed",
                "failure",
                "skipped",
                "timeout",
            }:
                return True
            if key_lower in {"error", "errors", "exception"} and item:
                return True
            if _has_false_success(item):
                return True
    elif isinstance(value, list):
        return any(_has_false_success(item) for item in value)
    return False


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item)]
