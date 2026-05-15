from __future__ import annotations

from pathlib import Path

import pytest

from skillos.layers.skill_runtime.harness import CodexCliHarness, HarnessTestCase, HarnessWorkspace
from skillos.layers.skill_runtime.harness import codex_cli
from skillos.models.skill_model import Skill, SkillEvaluation, SkillImplementation, SkillInterface


def _skill() -> Skill:
    return Skill(
        name="codex_harness_skill",
        description="Return an ok flag through the Codex CLI harness.",
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        implementation=SkillImplementation(code="output['ok'] = True"),
        evaluation=SkillEvaluation(
            verifier_specs=[{"type": "json_equals", "path": "output.ok", "value": True}]
        ),
    )


@pytest.mark.asyncio
async def test_codex_cli_harness_returns_unavailable_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_cli.shutil, "which", lambda command: None)
    workspace = HarnessWorkspace("loop-codex-missing", root=tmp_path)
    skill = _skill()
    test_case = HarnessTestCase(
        test_id="codex-missing",
        name="codex missing",
        goal="return ok",
        verifier_specs=skill.evaluation.verifier_specs,
    )

    result = await CodexCliHarness().run_skill(skill, test_case, workspace, attempt=1)

    assert result.status == "harness_unavailable"
    assert result.verifier_passed is False
    assert "not available" in result.failure_reason


@pytest.mark.asyncio
async def test_codex_cli_harness_parses_output_json_from_mocked_process(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_cli.shutil, "which", lambda command: "C:/fake/codex.exe")

    async def fake_run(args, stdin, *, cwd, timeout_s):  # noqa: ANN001
        Path(cwd, "output.json").write_text('{"ok": true}', encoding="utf-8")
        return {"returncode": 0, "stdout": "done", "stderr": ""}

    monkeypatch.setattr(codex_cli, "_run_subprocess", fake_run)
    workspace = HarnessWorkspace("loop-codex-pass", root=tmp_path)
    skill = _skill()
    test_case = HarnessTestCase(
        test_id="codex-pass",
        name="codex pass",
        goal="return ok",
        verifier_specs=skill.evaluation.verifier_specs,
    )

    result = await CodexCliHarness().run_skill(skill, test_case, workspace, attempt=1)

    assert result.status == "passed"
    assert result.verifier_passed is True
    assert result.stdout == "done"
    assert (tmp_path / "loop-codex-pass" / "attempt-001" / "SKILL.md").exists()
    assert (tmp_path / "loop-codex-pass" / "attempt-001" / "prompt.md").exists()
