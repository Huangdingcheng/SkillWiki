import pytest

from skillwiki.layers.skill_management.skillnet_adapter import (
    SKILLNET_COMMIT,
    build_skillnet_receipt,
)


def test_prepared_receipt_is_source_bound_and_keeps_raw_boundary():
    receipt = build_skillnet_receipt(
        catalog_digest="sha256:" + "a" * 64,
        task_digest="sha256:" + "b" * 64,
        command=("skillfabric", "route", "dialogue-parser"),
        raw_stdout="",
        raw_stderr="entrypoint unavailable",
        status="blocked",
        provenance={"checkout": "artifacts/20260927-content-vertical-slice/SkillNet-REF-02/source"},
        recorded_at="2026-10-03T00:00:00+00:00",
    )
    assert receipt["source"]["commit"] == SKILLNET_COMMIT
    assert receipt["candidate"]["id"] is None
    assert receipt["raw"]["stderr"] == "entrypoint unavailable"
    assert receipt["status"] == "blocked"


def test_completed_receipt_requires_zero_exit():
    with pytest.raises(ValueError, match="completed receipt"):
        build_skillnet_receipt(
            catalog_digest="sha256:" + "a" * 64,
            task_digest="sha256:" + "b" * 64,
            command=("skillfabric", "route"),
            status="completed",
            exit_code=1,
        )


def test_frozen_identity_cannot_be_overridden():
    with pytest.raises(TypeError):
        build_skillnet_receipt(
            catalog_digest="sha256:" + "a" * 64,
            task_digest="sha256:" + "b" * 64,
            command=("skillfabric",),
            source_commit="other",  # type: ignore[call-arg]
        )


def test_expected_task_digest_rejects_exact_task_mismatch():
    with pytest.raises(ValueError, match="task_digest does not match expected_task_digest"):
        build_skillnet_receipt(
            catalog_digest="sha256:" + "a" * 64,
            task_digest="sha256:" + "b" * 64,
            expected_task_digest="sha256:" + "c" * 64,
            command=("skillfabric",),
        )


def test_expected_task_digest_preserves_actual_digest_when_matching():
    digest = "sha256:" + "b" * 64
    receipt = build_skillnet_receipt(
        catalog_digest="sha256:" + "a" * 64,
        task_digest=digest,
        expected_task_digest=digest,
        command=("skillfabric",),
    )
    assert receipt["task_digest"] == digest
