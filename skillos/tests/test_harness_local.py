from __future__ import annotations

import pytest

from skillos.layers.skill_runtime.harness import HarnessTestCase, HarnessWorkspace, LocalSkillOSHarness
from skillos.layers.skill_runtime.executor import SkillExecutor
from skillos.models.skill_model import Skill, SkillEvaluation, SkillImplementation, SkillInterface


class JsonLLM:
    def chat(self, messages: object):  # type: ignore[override]
        from types import SimpleNamespace

        return SimpleNamespace(content="""
{
  "result": {
    "entrypoint": "scripts/analyze.py",
    "arguments": [],
    "dependencies": ["python"],
    "side_effects": [],
    "mutation_avoided": true
  },
  "evidence": ["dry-run contract produced"],
  "verifier": {"passed": true, "checked": ["dry_run", "allowed_paths"]}
}
""")


def _skill(code: str) -> Skill:
    return Skill(
        name="harness_local_skill",
        description="Return an ok flag for local harness verification.",
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        implementation=SkillImplementation(code=code),
        evaluation=SkillEvaluation(
            verifier_specs=[{"type": "json_equals", "path": "output.ok", "value": True}]
        ),
    )


@pytest.mark.asyncio
async def test_local_skillos_harness_records_passing_evidence(tmp_path):
    workspace = HarnessWorkspace("loop-local-pass", root=tmp_path)
    skill = _skill("output['ok'] = True")
    test_case = HarnessTestCase(
        test_id="local-pass",
        name="local pass",
        goal="return ok",
        verifier_specs=skill.evaluation.verifier_specs,
    )

    result = await LocalSkillOSHarness().run_skill(skill, test_case, workspace, attempt=1)

    assert result.status == "passed"
    assert result.verifier_passed is True
    assert result.verifier_summary["score"] == 1.0
    assert (tmp_path / "loop-local-pass" / "attempt-001" / "result.json").exists()


@pytest.mark.asyncio
async def test_local_skillos_harness_keeps_failed_postcondition_evidence(tmp_path):
    workspace = HarnessWorkspace("loop-local-fail", root=tmp_path)
    skill = _skill("output['summary'] = 'missing ok'")
    test_case = HarnessTestCase(
        test_id="local-fail",
        name="local fail",
        goal="return ok",
        verifier_specs=skill.evaluation.verifier_specs,
    )

    result = await LocalSkillOSHarness().run_skill(skill, test_case, workspace, attempt=1)

    assert result.status == "failed"
    assert result.verifier_passed is False
    assert "Path not found: output.ok" in result.failure_reason
    assert result.output["output"]["summary"] == "missing ok"


@pytest.mark.asyncio
async def test_local_skillos_harness_enforces_input_contract_specs(tmp_path):
    workspace = HarnessWorkspace("loop-local-input-contract", root=tmp_path)
    skill = _skill("output['ok'] = True")
    specs = [
        {"type": "json_equals", "path": "input.dry_run", "value": True},
        {"type": "json_array_nonempty", "path": "input.allowed_paths"},
        {"type": "json_equals", "path": "output.ok", "value": True},
    ]
    test_case = HarnessTestCase(
        test_id="local-input-contract",
        name="local input contract",
        goal="enforce dry-run safety inputs",
        input_data={"dry_run": False, "allowed_paths": []},
        verifier_specs=specs,
    )

    result = await LocalSkillOSHarness().run_skill(skill, test_case, workspace, attempt=1)

    assert result.status == "failed"
    assert result.verifier_passed is False
    assert "Expected input.dry_run == True, got False." in result.failure_reason
    assert "Expected input.allowed_paths to be a non-empty array." in result.failure_reason
    assert result.output["input"] == {"dry_run": False, "allowed_paths": []}


@pytest.mark.asyncio
async def test_local_skillos_harness_verifies_prompt_skill_structured_json(tmp_path):
    workspace = HarnessWorkspace("loop-local-prompt-json", root=tmp_path)
    skill = Skill(
        name="script_dry_run_prompt_skill",
        description="Return a script dry-run analysis as JSON.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "script_context": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "allowed_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["task", "script_context", "dry_run", "allowed_paths"],
            },
            output_schema={"type": "object", "properties": {"result": {"type": "object"}}},
        ),
        implementation=SkillImplementation(prompt_template="Analyze {script_context} for {task}."),
        evaluation=SkillEvaluation(
            verifier_specs=[
                {"type": "json_equals", "path": "input.dry_run", "value": True},
                {"type": "json_array_nonempty", "path": "input.allowed_paths"},
                {"type": "json_nonempty", "path": "output.result.entrypoint"},
                {"type": "json_equals", "path": "output.result.mutation_avoided", "value": True},
                {"type": "json_equals", "path": "output.verifier.passed", "value": True},
            ]
        ),
    )
    test_case = HarnessTestCase(
        test_id="prompt-json",
        name="prompt JSON",
        goal="return script dry-run contract",
        input_data={
            "task": "analyze script",
            "script_context": "python scripts/analyze.py",
            "dry_run": True,
            "allowed_paths": ["scripts/analyze.py"],
        },
        verifier_specs=skill.evaluation.verifier_specs,
    )

    result = await LocalSkillOSHarness(
        executor=SkillExecutor(llm_client=JsonLLM())  # type: ignore[arg-type]
    ).run_skill(skill, test_case, workspace, attempt=1)

    assert result.status == "passed"
    assert result.verifier_passed is True
    assert result.output["output"]["result"]["entrypoint"] == "scripts/analyze.py"
