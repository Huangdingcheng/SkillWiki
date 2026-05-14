from __future__ import annotations

import json
from types import SimpleNamespace

from skillos.layers.skill_runtime.reflection import ReflectionAgent, _REFLECT_PROMPT
from skillos.layers.skill_runtime.verifier import (
    VerificationResult,
    VerifierAgent,
    _VERIFY_PROMPT,
)
from skillos.models.maintenance_model import (
    MaintenanceRecommendedAction,
    MaintenanceTrigger,
)


def test_runtime_verifier_and_reflection_prompts_are_ascii():
    _VERIFY_PROMPT.encode("ascii")
    _REFLECT_PROMPT.encode("ascii")


def test_verifier_normalizes_llm_output():
    payload = {
        "passed": True,
        "score": 2.5,
        "issues": "bad shape",
        "suggestions": ["keep going", None],
        "reasoning": "looks okay",
    }
    verifier = VerifierAgent(FakeLLM(json.dumps(payload)))

    result = verifier.verify("finish task", {"ok": True})

    assert result.passed is True
    assert result.score == 1.0
    assert result.issues == []
    assert result.suggestions == ["keep going"]
    assert result.details["reasoning"] == "looks okay"


def test_verifier_fallback_detects_failure_output():
    verifier = VerifierAgent(FakeLLM("not json"))

    result = verifier.verify(
        "finish task",
        {"success": False, "error": "step failed"},
        "step failed with timeout",
    )

    assert result.passed is False
    assert result.score == 0.2
    assert result.issues
    assert result.suggestions


def test_verifier_fallback_passes_non_empty_output_without_failure_evidence():
    verifier = VerifierAgent(FakeLLM("not json"))

    result = verifier.verify("finish task", {"result": "done"}, "all steps completed")

    assert result.passed is True
    assert result.score == 0.65
    assert result.issues == []


def test_reflection_normalizes_d_compatible_proposals():
    payload = {
        "root_cause": "timeout",
        "failed_skill_ids": ["skill_a", None],
        "improvement_suggestions": ["repair prompt", ""],
        "skill_update_proposals": [
            {
                "skill_id": "skill_a",
                "issue": "timeout",
                "proposed_fix": "add timeout handling",
                "recommended_action": "unknown",
                "evidence": ["step failed", None],
                "targets_to_fix": ["timeout branch"],
                "invariants_to_preserve": ["successful retry behavior"],
                "validation_plan": ["replay failed task", ""],
            },
            "bad",
            {"skill_id": "", "recommended_action": "repair"},
        ],
        "experience_summary": "timeout during execution",
    }
    reflector = ReflectionAgent(FakeLLM(json.dumps(payload)))
    verification = VerificationResult(
        passed=False,
        score=0.2,
        goal="finish task",
        issues=["timeout"],
    )

    feedback = reflector.reflect("task-1", "finish task", {}, verification)

    assert feedback.success is False
    assert feedback.failed_skill_ids == ["skill_a"]
    assert feedback.improvement_suggestions == ["repair prompt"]
    assert feedback.skill_update_proposals == [
        {
            "skill_id": "skill_a",
            "issue": "timeout",
            "proposed_fix": "add timeout handling",
            "recommended_action": "review",
            "evidence": ["step failed"],
            "targets_to_fix": ["timeout branch"],
            "invariants_to_preserve": ["successful retry behavior"],
            "validation_plan": ["replay failed task"],
        }
    ]


def test_reflection_fallback_generates_repair_proposal_for_failed_skill():
    reflector = ReflectionAgent(FakeLLM("not json"))
    verification = VerificationResult(
        passed=False,
        score=0.2,
        goal="finish task",
        issues=["Execution trace contains skipped steps."],
        suggestions=["Repair the failed skill."],
    )
    trace = {
        "steps": [
            {
                "skill_id": "skill_a",
                "status": "failed",
                "error": "Skill code raised RuntimeError",
            }
        ]
    }

    feedback = reflector.reflect("task-1", "finish task", trace, verification)

    assert feedback.success is False
    assert feedback.failed_skill_ids == ["skill_a"]
    assert feedback.skill_update_proposals[0]["skill_id"] == "skill_a"
    assert feedback.skill_update_proposals[0]["recommended_action"] == "repair"
    assert feedback.skill_update_proposals[0]["evidence"]

    proposals = feedback.to_maintenance_proposals()
    assert len(proposals) == 1
    assert proposals[0].skill_id == "skill_a"
    assert proposals[0].trigger == MaintenanceTrigger.RUNTIME_FAILURE
    assert proposals[0].recommended_action == MaintenanceRecommendedAction.REPAIR
    assert proposals[0].root_cause == "Execution trace contains skipped steps."
    assert proposals[0].feedback_sources == ["runtime_reflection"]
    assert proposals[0].targets_to_fix == ["Execution trace contains skipped steps."]
    assert proposals[0].invariants_to_preserve
    assert proposals[0].validation_plan
    assert proposals[0].requires_human_review is True


def test_reflection_fallback_uses_trace_skill_ids_for_verifier_failure():
    reflector = ReflectionAgent(FakeLLM("not json"))
    verification = VerificationResult(
        passed=False,
        score=0.0,
        goal="submit form",
        issues=["Path not found: output.final_state.submitted"],
        suggestions=["Repair the postcondition mapping."],
    )
    trace = {
        "steps": [
            {
                "skill_id": "skill_submit_form",
                "status": "success",
                "outputs": {"success": True},
            }
        ]
    }

    feedback = reflector.reflect("task-verify", "submit form", trace, verification)

    assert feedback.success is False
    assert feedback.failed_skill_ids == ["skill_submit_form"]
    assert feedback.skill_update_proposals == [
        {
            "skill_id": "skill_submit_form",
            "issue": "Path not found: output.final_state.submitted",
            "proposed_fix": (
                "Review runtime failure and repair the skill implementation or prompt."
            ),
            "recommended_action": "repair",
            "evidence": ["Path not found: output.final_state.submitted"],
        }
    ]


def test_reflection_fallback_success_does_not_generate_repair_proposal():
    reflector = ReflectionAgent(FakeLLM("not json"))
    verification = VerificationResult(
        passed=True,
        score=0.8,
        goal="finish task",
    )

    feedback = reflector.reflect("task-1", "finish task", {"result": "done"}, verification)

    assert feedback.success is True
    assert feedback.skill_update_proposals == []
    assert feedback.experience_summary == "Task completed successfully."


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages: object) -> SimpleNamespace:
        return SimpleNamespace(content=self.content)
