"""Codex CLI harness adapter."""

from __future__ import annotations

import asyncio
import json
import shutil
from time import perf_counter
from typing import Any, Dict, Sequence
from uuid import uuid4

from ....models.skill_model import Skill
from ..verifier import evaluate_verifier_specs
from .base import HarnessKind, HarnessRunResult, HarnessTestCase
from .workspace import HarnessWorkspace


class CodexCliHarness:
    """Run a Skill package through `codex exec` in a temp evidence workspace."""

    kind = HarnessKind.CODEX_CLI

    def __init__(self, command: str = "codex") -> None:
        self.command = command

    async def run_skill(
        self,
        skill: Skill,
        test_case: HarnessTestCase,
        workspace: HarnessWorkspace,
        *,
        attempt: int = 1,
    ) -> HarnessRunResult:
        attempt_path = workspace.attempt_dir(attempt)
        run_id = f"harness_run_{uuid4().hex[:12]}"
        prompt = _build_prompt(skill, test_case)
        workspace.write_text(attempt_path / "prompt.md", prompt)
        workspace.write_text(attempt_path / "SKILL.md", _skill_markdown(skill))
        workspace.write_json(attempt_path / "skill.json", skill.model_dump(mode="json"))
        workspace.write_json(attempt_path / "input.json", test_case.input_data)
        workspace.write_json(
            attempt_path / "verifier_specs.json",
            {"verifier_specs": test_case.verifier_specs},
        )

        if shutil.which(self.command) is None:
            result = HarnessRunResult(
                run_id=run_id,
                skill_id=skill.skill_id,
                attempt=attempt,
                harness=self.kind,
                status="harness_unavailable",
                input_data=test_case.input_data,
                output={},
                verifier_passed=False,
                verifier_summary={
                    "passed": False,
                    "score": 0.0,
                    "issues": [f"{self.command} CLI is not available on PATH."],
                },
                failure_reason=f"{self.command} CLI is not available on PATH.",
                evidence_path=str(attempt_path),
            )
            workspace.write_json(attempt_path / "result.json", result.model_dump(mode="json"))
            return result

        args = [
            self.command,
            "exec",
            "--full-auto",
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            "result.txt",
            "-",
        ]
        start = perf_counter()
        try:
            completed = await _run_subprocess(
                args,
                prompt,
                cwd=str(attempt_path),
                timeout_s=test_case.timeout_s,
            )
        except asyncio.TimeoutError:
            latency_ms = (perf_counter() - start) * 1000
            result = HarnessRunResult(
                run_id=run_id,
                skill_id=skill.skill_id,
                attempt=attempt,
                harness=self.kind,
                status="timeout",
                input_data=test_case.input_data,
                verifier_passed=False,
                verifier_summary={"passed": False, "score": 0.0, "issues": ["Harness timed out."]},
                latency_ms=latency_ms,
                failure_reason="Harness timed out.",
                evidence_path=str(attempt_path),
            )
            workspace.write_json(attempt_path / "result.json", result.model_dump(mode="json"))
            return result

        latency_ms = (perf_counter() - start) * 1000
        stdout = completed["stdout"]
        stderr = completed["stderr"]
        workspace.write_text(attempt_path / "stdout.log", stdout)
        workspace.write_text(attempt_path / "stderr.log", stderr)

        output = _read_output_json(attempt_path / "output.json")
        if output is None:
            output = {"result_text": _read_text_if_present(attempt_path / "result.txt") or stdout}
        payload = {"input": test_case.input_data, "output": output, "final_state": output}
        verification = evaluate_verifier_specs(
            test_case.verifier_specs,
            payload,
            goal=test_case.goal,
        )
        process_ok = completed["returncode"] == 0
        passed = process_ok and verification.passed
        failure_reason = ""
        if not process_ok:
            failure_reason = f"Codex CLI exited with code {completed['returncode']}."
        elif not verification.passed:
            failure_reason = "; ".join(verification.issues)

        result = HarnessRunResult(
            run_id=run_id,
            skill_id=skill.skill_id,
            attempt=attempt,
            harness=self.kind,
            status="passed" if passed else "failed",
            input_data=test_case.input_data,
            output=payload,
            stdout=stdout,
            stderr=stderr,
            verifier_passed=passed,
            verifier_summary={
                "passed": verification.passed,
                "score": verification.score,
                "issues": list(verification.issues),
                "suggestions": list(verification.suggestions),
                "details": dict(verification.details),
                "process_returncode": completed["returncode"],
            },
            latency_ms=latency_ms,
            failure_reason=failure_reason,
            evidence_path=str(attempt_path),
        )
        workspace.write_json(attempt_path / "result.json", result.model_dump(mode="json"))
        return result


async def _run_subprocess(
    args: Sequence[str],
    stdin: str,
    *,
    cwd: str,
    timeout_s: int,
) -> Dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        process.communicate(stdin.encode("utf-8")),
        timeout=timeout_s,
    )
    return {
        "returncode": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


def _build_prompt(skill: Skill, test_case: HarnessTestCase) -> str:
    return (
        "You are a harness agent evaluating one Skill.\n\n"
        "Use the Skill definition in SKILL.md and skill.json.\n"
        f"Task goal:\n{test_case.goal}\n\n"
        "Inputs are stored in input.json.\n\n"
        "Rules:\n"
        "- Follow the Skill exactly when applicable.\n"
        "- Do not modify files outside this workspace.\n"
        "- Write final machine-readable result to output.json.\n"
        "- The final answer must be concise and must not hide errors.\n"
    )


def _skill_markdown(skill: Skill) -> str:
    return (
        f"# {skill.name}\n\n"
        f"{skill.description}\n\n"
        f"- Skill ID: `{skill.skill_id}`\n"
        f"- Version: `{skill.version}`\n"
        f"- Type: `{skill.skill_type.value}`\n"
        f"- State: `{skill.state.value}`\n"
    )


def _read_output_json(path: Any) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else {"value": data}


def _read_text_if_present(path: Any) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")
