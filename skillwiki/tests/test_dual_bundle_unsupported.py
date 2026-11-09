from __future__ import annotations

import os

import pytest

from skillwiki.layers.skill_governance.dual_bundle import (
    EvidencePersistenceError, materialize_dual_bundle, read_dual_bundle_state,
)
from skillwiki.layers.skill_governance.upstream_adapters import materialize_source_bound_bundle


@pytest.mark.skipif(os.name != "nt", reason="Windows fail-closed boundary")
def test_windows_opt_in_fails_before_writing(tmp_path):
    output = tmp_path / "bundle"
    with pytest.raises(EvidencePersistenceError, match="unsupported"):
        materialize_dual_bundle(source_dir=tmp_path, output_dir=output,
                                expected_declaration={}, task_id="x", task_digest="x", validator_plan={})
    assert not output.exists()
    assert not (tmp_path / "bundle.evidence").exists()
    assert read_dual_bundle_state(tmp_path / "bundle.evidence")["success"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows fail-closed and legacy boundary")
def test_windows_opt_in_rejection_does_not_disable_legacy_adapter(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_bytes(b"legacy bytes\n")
    with pytest.raises(EvidencePersistenceError, match="unsupported"):
        materialize_dual_bundle(source_dir=source, output_dir=tmp_path / "opt-in",
                                expected_declaration={}, task_id="x", task_digest="x", validator_plan={})
    assert not (tmp_path / "opt-in").exists()
    assert not (tmp_path / "opt-in.evidence").exists()
    legacy = materialize_source_bound_bundle(
        source_dir=source, output_dir=tmp_path / "legacy", source_files=("SKILL.md",),
        source_repository="https://example.invalid/source", source_commit="abc",
        task_id="test", task_digest="sha256:test", upstream_validator="validator-7")
    assert legacy["source_bytes_preserved"] is True
    assert (tmp_path / "legacy/SKILL.md").read_bytes() == b"legacy bytes\n"
