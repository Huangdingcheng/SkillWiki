"""Runtime result verifier."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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
    ) -> VerificationResult:
        """Verify an execution result."""

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
