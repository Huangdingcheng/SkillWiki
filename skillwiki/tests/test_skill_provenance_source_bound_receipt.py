import json
from pathlib import Path

import pytest

from skillwiki.layers.skill_governance.upstream_adapters import (
    OfficialValidatorResult,
    build_reference_run_receipt,
    persist_source_bound_validator_receipt,
)


IDENTITY = {
    "repository": "https://github.com/snapsynapse/skill-provenance",
    "commit": "bcddbd2cd8902ae14e2dcaa328d59fd453be5d16",
    "version": "7.0.0",
    "license": "MIT",
}


def _result(status="success", stdout="Checked 3 files, skipped 0, unlisted 0, errors 0\nAll hashes verified.\n", stderr="", exit_code=0):
    return OfficialValidatorResult(
        component="skill-provenance",
        status=status,
        command=("bash", "validate.sh", "/tmp/bundle"),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        checked_files=3 if status == "success" else None,
        all_hashes_verified=status == "success",
        cwd="/tmp/skill-provenance",
        environment={"LANG": "C.UTF-8"},
    )


def test_persists_success_with_source_identity_and_non_claims(tmp_path: Path):
    path = tmp_path / "success.json"
    receipt = persist_source_bound_validator_receipt(
        result=_result(), source_identity=IDENTITY, evidence_path=path
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert receipt.integrated is True
    assert receipt.promotion_allowed is True
    assert stored["validator"]["source_identity"] == IDENTITY
    assert stored["validator"]["cwd"] == "/tmp/skill-provenance"
    assert stored["fairly_compared"] is False
    assert stored["paper_eligible"] is False


@pytest.mark.parametrize(
    ("status", "stdout", "stderr", "exit_code"),
    [
        ("tampered", "hash mismatch: SKILL.md\n", "", 1),
        ("tampered", "MISMATCH SKILL.md\n", "", 1),
        ("missing_manifest", "", "MANIFEST.yaml missing\n", 2),
        ("blocked_environment", "", "docker unavailable\n", 127),
        ("timeout", "partial output", "", None),
    ],
)
def test_negative_states_are_distinct_and_never_promoted(tmp_path, status, stdout, stderr, exit_code):
    receipt = persist_source_bound_validator_receipt(
        result=_result(status, stdout, stderr, exit_code),
        source_identity=IDENTITY,
        evidence_path=tmp_path / f"{status}.json",
    )
    assert receipt.validator["status"] == status
    assert receipt.integrated is True
    assert receipt.promotion_allowed is False
    assert receipt.successfully_run is False


def test_release_receipt_rejects_failed_validator_promotion():
    with pytest.raises(ValueError, match="cannot be promoted"):
        build_reference_run_receipt(
            source_identity=IDENTITY,
            upstream_validator_result=_result("tampered", "hash mismatch", "", 1),
            adapter_materialization={"upstream_package_integrated": True},
            skillwiki_integration={"integrated": True},
            task_execution={"status": "not_run"},
            fair_comparison={"compared": False},
            paper_eligibility={"eligible": False},
        )


def test_official_c674_mismatch_line_maps_to_tampered(tmp_path: Path):
    receipt = persist_source_bound_validator_receipt(
        result=_result("failed", "MISMATCH SKILL.md\n", "", 1),
        source_identity=IDENTITY,
        evidence_path=tmp_path / "c674-style-mismatch.json",
    )
    assert receipt.validator["status"] == "tampered"
    assert receipt.promotion_allowed is False
