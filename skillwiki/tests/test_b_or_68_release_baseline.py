import json
import os
import subprocess
from pathlib import Path

import pytest

from skillwiki.layers.skill_governance.git_ci_release_baseline import (
    compare_release_evidence_paths,
    git_ci_release_decision,
)


ROOT = Path(__file__).parents[2]
DREF = ROOT / "research/20260929/D-REF-23-governance-comparison.json"


def _cases():
    return json.loads(DREF.read_text(encoding="utf-8"))["cases"]


def _evidence(case):
    fixture = case["case_id"]
    return {
        "source_identity": {"repository": "https://github.com/snapsynapse/skill-provenance", "commit": "bcddbd2cd8902ae14e2dcaa328d59fd453be5d16", "version": "7.0.0", "license": "MIT", "matches": fixture != "source_identity_version_mismatch"},
        "version": "7.0.0",
        "license": "uncertain" if fixture == "license_uncertain" else "MIT",
        "manifest": {"present": fixture != "missing_manifest", "package_digest": "sha256:" + "a" * 64, "file_hashes": {"SKILL.md": "sha256:" + "b" * 64}},
        "environment": {"status": "unknown" if fixture == "environment_dependency_unknown" else "known", "dependency_manifest": "d-ref-23-env-wsl-lf"},
        "validator": {
            "status": "failed" if fixture == "tampered_skill_md" else "success",
            "command": ["validator", "fixture"], "stdout": "raw stdout", "stderr": "", "exit_code": 0, "termination": "exited", "started_at": "2026-09-29T00:00:00Z", "ended_at": "2026-09-29T00:00:01Z", "duration_seconds": 0.1,
        },
        "release_action": {"action": "rollback" if fixture == "rollback_published_version" else "release"},
    }


def test_same_seven_fixture_denominator_and_decisions():
    expected = {
        "complete_package": "accept", "tampered_skill_md": "reject", "missing_manifest": "reject",
        "source_identity_version_mismatch": "reject", "license_uncertain": "defer",
        "environment_dependency_unknown": "defer", "rollback_published_version": "rollback",
    }
    cases = _cases()
    assert len(cases) == 7
    for case in cases:
        assert git_ci_release_decision(_evidence(case))["decision"] == expected[case["case_id"]]


@pytest.mark.parametrize("field", ["license", "manifest", "environment", "validator", "release_action"])
def test_missing_field_defers_without_silent_completion(field):
    evidence = _evidence(_cases()[0])
    del evidence[field]
    result = git_ci_release_decision(evidence)
    assert result["decision"] == "defer"
    assert field in result["missing_fields"]
    assert result["release_executed"] is False


def test_validator_failed_rejects_and_raw_evidence_is_retained():
    evidence = _evidence(next(case for case in _cases() if case["case_id"] == "tampered_skill_md"))
    result = git_ci_release_decision(evidence)
    assert result["decision"] == "reject"
    assert evidence["validator"]["command"] == ["validator", "fixture"]


def test_cross_path_comparison_is_structured_without_claiming_fairness():
    comparison = compare_release_evidence_paths(
        baseline_evidence=_evidence(_cases()[0]), skillwiki_decision="accept"
    )
    assert comparison["same_decision"] is True
    assert comparison["shared_input_fields"] == sorted(_evidence(_cases()[0]))


def test_current_b_or_71_generator_consumes_fixture_and_writes_seven_cases():
    env = dict(os.environ, PYTHONPATH="skillwiki")
    process = subprocess.run(
        ["python", "research/20260930/B-OR-71-baseline.py"],
        cwd=ROOT, text=True, capture_output=True, check=False, env=env,
    )
    assert process.returncode == 0
    assert '"task": "B-OR-71"' in process.stdout
    output = json.loads((ROOT / "artifacts/20260930-b-or-71/baseline-output.json").read_text(encoding="utf-8"))
    inputs = json.loads((ROOT / "artifacts/20260930-b-or-71/shared-input-fixture.json").read_text(encoding="utf-8"))
    assert len(inputs) == len(output) == 7
    assert all(item["source_paths"]["shared_inputs"] for item in inputs.values())


def test_current_generator_records_exact_dref_paths_digests_and_identity():
    inputs = json.loads((ROOT / "artifacts/20260930-b-or-71/shared-input-fixture.json").read_text(encoding="utf-8"))
    for case in _cases():
        cid = case["case_id"]
        row = inputs[cid]
        assert row["source_paths"]["package"].endswith(case["fixture"]["package_path"])
        assert row["source_paths"]["evidence"].endswith(case["fixture"]["evidence_path"])
        assert row["shared_inputs"] == json.loads((ROOT / "artifacts/20260929-governance-d-ref-23/raw" / cid / "shared-inputs.json").read_text(encoding="utf-8"))
        manifest = row["package_evidence"]["manifest"]
        assert row["manifest"]["package_digest"] == case["fixture"]["shared_inputs"]["package_digest"]
        assert row["source_identity"]["repository"] == manifest["source_repository"]
        assert row["source_identity"]["commit"] == manifest["source_commit"]
        assert row["source_identity"]["version"] == manifest["source_version"]


@pytest.mark.parametrize("mutation", [
    lambda e: e["source_identity"].pop("matches"),
    lambda e: e["manifest"].pop("package_digest"),
    lambda e: e["environment"].pop("dependency_manifest"),
    lambda e: e["validator"].update(duration_seconds=float("nan")),
    lambda e: e["validator"].pop("termination"),
])
def test_five_d_or_73_malformed_boundaries_defer(mutation):
    evidence = _evidence(_cases()[0])
    mutation(evidence)
    result = git_ci_release_decision(evidence)
    assert result["decision"] == "defer"
    assert result["release_executed"] is False


@pytest.mark.parametrize("bad", [None, {"action": "publish"}, {"action": None}])
def test_unknown_release_action_is_stable_defer(bad):
    evidence = _evidence(_cases()[0])
    evidence["release_action"] = bad
    result = git_ci_release_decision(evidence)
    assert result["decision"] == "defer"
    assert result["release_executed"] is False
