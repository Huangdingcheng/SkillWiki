"""Reusable M1 evidence adapter for the local SkillWiki harness."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Mapping, Sequence

from ....models.skill_model import Skill, SkillEvaluation, SkillImplementation, SkillInterface
from ...skill_governance.artifact_identity import (
    canonical_skill_artifact_hash,
    canonical_skill_artifact_json,
)
from .base import HarnessRunResult, HarnessTestCase
from .codex_cli import CodexCliHarness
from .local_skillwiki import LocalSkillWikiHarness
from .workspace import HarnessWorkspace


M1_CONTRACT_VERSION = "m1-contract-20260922"
M1_VERIFIER_VERSION = "deterministic-verifier-v1"
C02_TASK_ID = "c02_extract_email"
C02_FIXTURE_ID = "c02-email-fixture-v1"
C02_CASES = ("normal", "skill_output_failure", "environment_unknown")


def build_artifact_manifest(
    skill: Skill,
    *,
    source_snapshot: str,
    raw_content: bytes | str | None = None,
    contract_version: str = M1_CONTRACT_VERSION,
) -> Dict[str, Any]:
    """Hash the complete Skill model and optionally preserved raw artifact bytes."""

    canonical = canonical_skill_artifact_json(skill)
    manifest: Dict[str, Any] = {
        "artifact_id": f"{skill.skill_id}@{skill.version}",
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_hash": {
            "algorithm": "sha256",
            "encoding": "utf-8-lf-canonical",
            "value": canonical_skill_artifact_hash(skill),
        },
        "source_snapshot": source_snapshot,
        "contract_version": contract_version,
        "dependency_ids": [],
        "license_refs": [],
        "canonical_content": canonical,
    }
    if raw_content is not None:
        raw_bytes = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
        manifest["raw_content_hash"] = hashlib.sha256(raw_bytes).hexdigest()
    return manifest


def artifact_manifest(skill: Skill) -> Dict[str, Any]:
    """Backward-compatible C02 manifest helper."""

    return build_artifact_manifest(skill, source_snapshot=C02_FIXTURE_ID)


def extract_evidence_spans(result: HarnessRunResult) -> List[Dict[str, Any]]:
    """Normalize deterministic verifier details into auditable spans."""

    details = result.verifier_summary.get("details", {})
    verifier_results = details.get("results", []) if isinstance(details, Mapping) else []
    spans: List[Dict[str, Any]] = []
    for item in verifier_results if isinstance(verifier_results, list) else []:
        if not isinstance(item, Mapping):
            continue
        spans.append(
            {
                "verifier_path": item.get("path", ""),
                "checked_value": item.get("actual"),
                "expected_value": item.get("expected"),
                "failure_issue": item.get("issue", ""),
                "passed": bool(item.get("passed", False)),
            }
        )
    if not spans:
        spans.append(
            {
                "verifier_path": "",
                "checked_value": None,
                "expected_value": None,
                "failure_issue": result.failure_reason,
                "passed": bool(result.verifier_passed),
            }
        )
    return spans


def _environment_fingerprint(fixture_id: str) -> str:
    return "|".join(
        (
            f"python={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            f"os={platform.system()}-{platform.release()}",
            "provider=local",
            f"fixture={fixture_id}",
        )
    )


def _cost() -> Dict[str, Any]:
    return {
        "tool_calls": 0,
        "model_calls": 0,
        "input_tokens": None,
        "output_tokens": None,
        "usd": None,
    }


def _classify_result(case: str, result: HarnessRunResult) -> tuple[str, str, str, int | None]:
    if case == "environment_unknown":
        return (
            "unknown",
            "environment",
            "Configured local runner command is unavailable; no child process started.",
            None,
        )
    if result.verifier_passed:
        return "passed", "none", "", 0
    return "failed", "skill_output", "", 1


async def run_runtime_cases(
    *,
    skill: Skill,
    task_id: str,
    fixture_id: str,
    cases: Sequence[str],
    test_case_factory: Any,
    evidence_root: Path,
    source_snapshot: str | None = None,
    raw_content: bytes | str | None = None,
) -> Dict[str, Any]:
    """Run arbitrary Skill cases through existing harnesses and emit M1 traces."""

    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    manifest = build_artifact_manifest(
        skill,
        source_snapshot=source_snapshot or fixture_id,
        raw_content=raw_content,
    )
    manifest_path = evidence_root / "artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    traces: List[Dict[str, Any]] = []
    environment_fingerprint = _environment_fingerprint(fixture_id)
    for index, case in enumerate(cases, start=1):
        workspace = HarnessWorkspace(f"{fixture_id}-{case}", root=evidence_root / "harness-runs")
        test_case: HarnessTestCase = test_case_factory(case, skill)
        if case == "environment_unknown":
            harness = CodexCliHarness(command=f"{fixture_id}-command-does-not-exist")
            harness_config = f"codex_cli|command={fixture_id}-command-does-not-exist"
        else:
            harness = LocalSkillWikiHarness()
            harness_config = "local_skillwiki|deterministic-fixture"

        started = perf_counter()
        result = await harness.run_skill(skill, test_case, workspace, attempt=1)
        wall_time_ms = (perf_counter() - started) * 1000
        status, failure_class, unknown_reason, exit_code = _classify_result(case, result)
        result_path = Path(result.evidence_path)
        trace_path = result_path / "trace.json"
        trace = {
            "run_id": result.run_id,
            "artifact_hash": manifest["content_hash"]["value"],
            "task_id": task_id,
            "input_ref": f"{fixture_id}:{case}",
            "environment_fingerprint": environment_fingerprint,
            "model_harness_config": harness_config,
            "verifier_version": M1_VERIFIER_VERSION,
            "status": status,
            "failure_class": failure_class,
            "unknown_reason": unknown_reason,
            "cost": _cost(),
            "evidence_path": str((result_path / "result.json").resolve()),
            "evidence_spans": extract_evidence_spans(result),
            "case": case,
            "contract_version": M1_CONTRACT_VERSION,
            "adapter_metadata": {
                "case_index": index,
                "exit_code": exit_code,
                "wall_time_ms": round(wall_time_ms, 3),
                "harness_status": result.status,
                "failure_reason": result.failure_reason,
                "trace_path": str(trace_path.resolve()),
            },
        }
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        traces.append(trace)

    counts = {
        status: sum(trace["status"] == status for trace in traces)
        for status in ("passed", "failed", "unknown")
    }
    return {
        "contract_version": M1_CONTRACT_VERSION,
        "task_id": task_id,
        "artifact_hash": manifest["content_hash"]["value"],
        "artifact_manifest_path": str(manifest_path.resolve()),
        "denominator": len(traces),
        "counts": counts,
        "trace_ids": [trace["run_id"] for trace in traces],
        "traces": traces,
    }


def build_c02_skill() -> Skill:
    """Build the original C02 deterministic artifact."""

    return Skill(
        skill_id=C02_TASK_ID,
        name=C02_TASK_ID,
        description="Extract the declared email from a contact task.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "case": {"type": "string"},
                    "text": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["case", "text", "email"],
            },
            output_schema={"type": "object", "properties": {"email": {"type": "string"}}},
        ),
        implementation=SkillImplementation(
            code=(
                "if input_data.get('case') == 'skill_output_failure':\n"
                "    output['summary'] = input_data.get('text', '')\n"
                "else:\n"
                "    output['email'] = input_data.get('email', '')\n"
            )
        ),
        evaluation=SkillEvaluation(
            verifier_specs=[
                {"type": "json_equals", "path": "output.email", "value": "ada@example.com"}
            ]
        ),
    )


def _case_test_case(case: str, skill: Skill) -> HarnessTestCase:
    return HarnessTestCase(
        test_id=f"{C02_FIXTURE_ID}:{case}",
        name=f"C02 {case}",
        goal="Extract Ada's email address from the contact task.",
        input_data={
            "case": case,
            "text": "Contact Ada at ada@example.com",
            "email": "ada@example.com",
        },
        verifier_specs=skill.evaluation.verifier_specs,
        timeout_s=10,
    )


async def run_c02_cases(evidence_root: Path) -> Dict[str, Any]:
    """Backward-compatible C02 wrapper around the generic adapter."""

    return await run_runtime_cases(
        skill=build_c02_skill(),
        task_id=C02_TASK_ID,
        fixture_id=C02_FIXTURE_ID,
        cases=C02_CASES,
        test_case_factory=_case_test_case,
        evidence_root=evidence_root,
    )
