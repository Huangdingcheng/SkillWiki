"""Execution harness models for Skill verification loops."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Protocol

from pydantic import BaseModel, Field

from ....models.skill_model import Skill


class HarnessKind(str, Enum):
    """Supported execution harness adapters."""

    LOCAL_SKILLOS = "local_skillos"
    CODEX_CLI = "codex_cli"
    CLAUDE_CODE = "claude_code"


class HarnessTestCase(BaseModel):
    """One executable postcondition check for a Skill."""

    test_id: str
    name: str
    goal: str
    input_data: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    verifier_specs: List[Dict[str, Any]] = Field(default_factory=list)
    timeout_s: int = Field(default=120, ge=1)
    expected_artifacts: List[str] = Field(default_factory=list)


class HarnessRunResult(BaseModel):
    """Evidence for one harness attempt."""

    run_id: str
    skill_id: str
    attempt: int
    harness: HarnessKind
    status: str
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output: Dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    verifier_passed: bool = False
    verifier_summary: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    cost_estimate: Dict[str, Any] = Field(default_factory=dict)
    failure_reason: str = ""
    evidence_path: str = ""


class VerificationLoopResult(BaseModel):
    """Final result of a repair/retry verification loop."""

    loop_id: str
    skill_id: str
    initial_version: str
    final_version: str
    status: str
    attempts: List[HarnessRunResult] = Field(default_factory=list)
    repairs: List[Dict[str, Any]] = Field(default_factory=list)
    score: Dict[str, Any] = Field(default_factory=dict)
    promotion_allowed: bool = False
    final_state: str = ""
    evidence_path: str = ""


class SkillHarness(Protocol):
    """Common harness adapter interface."""

    async def run_skill(
        self,
        skill: Skill,
        test_case: HarnessTestCase,
        workspace: "HarnessWorkspace",
        *,
        attempt: int = 1,
    ) -> HarnessRunResult:
        ...


class HarnessWorkspace(Protocol):
    """Minimal workspace protocol used by harness adapters."""

    loop_id: str

    def attempt_dir(self, attempt: int) -> Any:
        ...

    def write_json(self, path: Any, payload: Dict[str, Any]) -> None:
        ...
