from __future__ import annotations

import json

import pytest

from skillwiki.layers.skill_runtime.harness.m1_trace import (
    C02_CASES,
    M1_CONTRACT_VERSION,
    artifact_manifest,
    build_artifact_manifest,
    build_c02_skill,
    extract_evidence_spans,
    run_c02_cases,
)
from skillwiki.layers.skill_runtime.harness.base import HarnessTestCase
from skillwiki.layers.skill_runtime.harness.local_skillwiki import LocalSkillWikiHarness
from skillwiki.layers.skill_runtime.harness.workspace import HarnessWorkspace
from skillwiki.models.skill_model import Skill, SkillEvaluation, SkillImplementation, SkillInterface, SkillState
from skillwiki.layers.skill_governance.artifact_identity import canonical_skill_artifact_hash


@pytest.mark.asyncio
async def test_c02_traces_cover_full_denominator_and_contract_shape(tmp_path):
    summary = await run_c02_cases(tmp_path)

    assert summary["denominator"] == 3
    assert summary["counts"] == {"passed": 1, "failed": 1, "unknown": 1}
    assert len(summary["traces"]) == len(C02_CASES)
    assert len({trace["artifact_hash"] for trace in summary["traces"]}) == 1
    for trace in summary["traces"]:
        assert trace["contract_version"] == M1_CONTRACT_VERSION
        assert trace["artifact_hash"] == summary["artifact_hash"]
        assert trace["environment_fingerprint"]
        assert trace["verifier_version"]
        assert trace["cost"]["model_calls"] == 0
        assert trace["adapter_metadata"]["wall_time_ms"] >= 0
        assert (tmp_path / trace["evidence_path"].split(str(tmp_path), 1)[-1].lstrip("\\/")).exists()


@pytest.mark.asyncio
async def test_c02_failure_classes_are_explicit_and_non_overlapping(tmp_path):
    summary = await run_c02_cases(tmp_path)
    by_case = {trace["case"]: trace for trace in summary["traces"]}

    assert by_case["normal"]["status"] == "passed"
    assert by_case["normal"]["failure_class"] == "none"
    assert by_case["skill_output_failure"]["status"] == "failed"
    assert by_case["skill_output_failure"]["failure_class"] == "skill_output"
    assert by_case["environment_unknown"]["status"] == "unknown"
    assert by_case["environment_unknown"]["failure_class"] == "environment"
    assert by_case["environment_unknown"]["unknown_reason"]
    assert by_case["environment_unknown"]["adapter_metadata"]["exit_code"] is None


@pytest.mark.asyncio
async def test_c02_trace_and_raw_result_are_json_readable(tmp_path):
    summary = await run_c02_cases(tmp_path)
    for trace in summary["traces"]:
        trace_path = trace["adapter_metadata"]["trace_path"]
        raw_path = trace["evidence_path"]
        assert json.loads(open(trace_path, encoding="utf-8").read())["run_id"] == trace["run_id"]
        assert json.loads(open(raw_path, encoding="utf-8").read())["status"] in {
            "passed",
            "failed",
            "harness_unavailable",
        }


def test_generic_manifest_hashes_complete_artifact_and_raw_bytes():
    skill = build_c02_skill()
    first = build_artifact_manifest(skill, source_snapshot="fixture", raw_content=b"raw-a")
    changed = skill.model_copy(update={"description": "changed description"})
    second = build_artifact_manifest(changed, source_snapshot="fixture", raw_content=b"raw-b")

    assert first["content_hash"]["value"] == artifact_manifest(skill)["content_hash"]["value"]
    assert first["content_hash"]["value"] != second["content_hash"]["value"]
    assert first["raw_content_hash"] != second["raw_content_hash"]
    assert first["canonical_content"].endswith("\n")


def test_artifact_identity_excludes_lifecycle_and_evidence_metadata():
    skill = build_c02_skill()
    baseline = canonical_skill_artifact_hash(skill)
    changed = skill.model_copy(deep=True)
    changed.transition_to(SkillState.VERIFIED)
    changed.evaluation.harness_validation = {"decision": "accept", "created_at": "volatile"}
    changed.metrics.usage_count = 7

    assert canonical_skill_artifact_hash(changed) == baseline


@pytest.mark.asyncio
async def test_generic_evidence_spans_capture_checked_value_and_failure(tmp_path):
    skill = Skill(
        skill_id="c03_span_fixture",
        name="c03_span_fixture",
        description="Return a title.",
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        ),
        implementation=SkillImplementation(code="output['title'] = input_data.get('title', '')"),
        evaluation=SkillEvaluation(
            verifier_specs=[{"type": "json_equals", "path": "output.title", "value": "SkillWiki"}]
        ),
    )
    case = HarnessTestCase(
        test_id="c03-span",
        name="span",
        goal="Return SkillWiki.",
        input_data={"title": "Wrong"},
        verifier_specs=skill.evaluation.verifier_specs,
    )
    result = await LocalSkillWikiHarness().run_skill(
        skill, case, HarnessWorkspace("c03-span", root=tmp_path), attempt=1
    )
    spans = extract_evidence_spans(result)

    assert spans[0]["verifier_path"] == "output.title"
    assert spans[0]["checked_value"] == "Wrong"
    assert spans[0]["expected_value"] == "SkillWiki"
    assert spans[0]["passed"] is False
    assert "Expected output.title" in spans[0]["failure_issue"]
