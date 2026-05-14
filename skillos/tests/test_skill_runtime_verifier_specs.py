from __future__ import annotations

from skillos.layers.skill_runtime.verifier import VerifierAgent, evaluate_verifier_specs


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages: object):  # type: ignore[override]
        from types import SimpleNamespace

        return SimpleNamespace(content=self.content)


def test_deterministic_verifier_specs_preserve_final_state():
    result = evaluate_verifier_specs(
        [
            {"type": "json_equals", "path": "output.success", "value": True},
            {"type": "json_exists", "path": "final_state.submitted"},
            {"type": "json_exists", "path": "output.final_state.submitted"},
            {"type": "contains", "path": "output.text", "value": "dashboard"},
            {"type": "boolean_success", "path": "output.success"},
        ],
        {
            "success": True,
            "final_state": {"submitted": True},
            "text": "opened dashboard",
        },
        goal="fill a login form",
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.details["verifier"] == "deterministic"


def test_pathless_boolean_success_rejects_failed_status():
    result = evaluate_verifier_specs(
        [{"type": "boolean_success"}],
        {"status": "failed"},
        goal="detect failed status",
    )

    assert result.passed is False
    assert result.issues == ["Output contains failure evidence."]


def test_deterministic_verifier_specs_fail_for_missing_path_and_false_success():
    result = evaluate_verifier_specs(
        [
            {"type": "json_exists", "path": "output.final_state.submitted"},
            {"type": "boolean_success", "path": "output.success"},
        ],
        {"success": False, "error": "postcondition failed"},
        goal="submit form",
    )

    assert result.passed is False
    assert result.score == 0.0
    assert "Path not found: output.final_state.submitted" in result.issues
    assert any("Expected output.success to be true." in issue for issue in result.issues)


def test_verifier_agent_uses_deterministic_specs_before_llm():
    verifier = VerifierAgent(FakeLLM("not json"))

    result = verifier.verify(
        "validate response",
        {"status_code": 200},
        verifier_specs=[{"type": "json_equals", "path": "output.status_code", "value": 200}],
    )

    assert result.passed is True
    assert result.details["verifier"] == "deterministic"
