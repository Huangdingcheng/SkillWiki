from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="opt-in stable directory ownership is unsupported on Windows")

from skillwiki.layers.skill_governance.dual_bundle import EvidencePersistenceError, materialize_dual_bundle, _secure_core
from skillwiki.layers.skill_governance.upstream_adapters import materialize_source_bound_bundle


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    files = {"SKILL.md": b"skill\n", "MANIFEST.yaml": b"manifest_version: '1'\npackage_version: '7.0.0'\n"}
    identity = {"repository": "https://example.invalid/source", "commit": "abc", "version": "7.0.0", "license": "MIT"}
    files["source_identity.json"] = json.dumps(identity).encode()
    for name, data in files.items(): (source / name).write_bytes(data)
    declaration = {"skill_id": "s", "artifact_id": "s@1.0.0", "skill_version": "1.0.0", "source_repository": identity["repository"], "source_commit": "abc", "source_version": "7.0.0", "historical_package_digest": "sha256:historical", "declared_files": [{"path": name, "bytes": len(data), "sha256": sha(data)} for name, data in files.items()], "license": {"value": "MIT", "scope": "task-local source package", "evidence_path": "source_payload/source_identity.json", "evidence_sha256": sha(files["source_identity.json"]), "state": "resolved_fixture_metadata"}}
    plan = {"id": "validator-7", "target_namespace": "source_payload", "target_path": "source_payload/", "schema": "skill-provenance package manifest v1"}
    return source, files, declaration, plan


def invoke(source, output, declaration, plan):
    return materialize_dual_bundle(source_dir=source, output_dir=output, expected_declaration=declaration, task_id="test", task_digest="sha256:test", validator_plan=plan)


def test_success_preserves_all_bytes_and_separates_metadata(tmp_path):
    source, files, declaration, plan = fixture(tmp_path)
    output = tmp_path / "bundle"
    result = invoke(source, output, declaration, plan)
    assert result.state == "materialized"
    assert all((output / "source_payload" / name).read_bytes() == data for name, data in files.items())
    assert (output / "management_metadata/transfer-manifest.json").is_file()
    assert (output / "management_metadata/provenance-receipt.json").is_file()
    assert not (output / "management_metadata/rollback-ref.json").exists()
    assert all(phase["status"] == "not_run" for phase in result.phases.values())
    assert result.validator_plan["status"] == "not_run/defer"
    assert json.loads((tmp_path / "bundle.evidence/final.json").read_bytes())["exit_code"] == 0
    assert json.loads((tmp_path / "bundle.evidence/raw/receipt.json").read_bytes())["state"] == "pending"


def test_failure_keeps_raw_source_partial_output_and_logs(tmp_path):
    source, files, declaration, plan = fixture(tmp_path)
    (source / "MANIFEST.yaml").write_bytes(b"changed\n")
    output = tmp_path / "bundle"
    result = invoke(source, output, declaration, plan)
    assert result.state == "stopped"
    raw = tmp_path / "bundle.evidence/raw"
    assert (raw / "source_payload/MANIFEST.yaml").read_bytes() == b"changed\n"
    assert (raw / "source_payload/source_identity.json").read_bytes() == files["source_identity.json"]
    assert (raw / "managed_partial/source_payload/MANIFEST.yaml").read_bytes() == b"changed\n"
    assert (raw / "stdout.bin").read_bytes() == b""
    assert b"declared byte/hash mismatch" in (raw / "stderr.bin").read_bytes()
    assert any(row["path"] == "raw/source_payload/MANIFEST.yaml" for row in json.loads(Path(result.raw_evidence_index).read_bytes())["files"])
    assert (raw / "files.json").is_file()


def test_digest_excludes_receipt_and_evidence_index(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    output = tmp_path / "bundle"
    result = invoke(source, output, declaration, plan)
    manifest = output / "management_metadata/transfer-manifest.json"
    assert result.management_core_sha256 == sha(manifest.read_bytes())
    rows = []
    for path in sorted([*(f"source_payload/{name}" for name in ("SKILL.md", "MANIFEST.yaml", "source_identity.json")), "management_metadata/transfer-manifest.json"], key=lambda p: p.encode()):
        data = (output / path).read_bytes()
        rows.append({"path": path, "bytes": len(data), "sha256": sha(data)})
    assert result.materialized_bundle_root_sha256 == sha(canonical(rows))
    receipt = output / "management_metadata/provenance-receipt.json"
    receipt.write_bytes(receipt.read_bytes() + b" ")
    assert result.materialized_bundle_root_sha256 == sha(canonical(rows))
    assert not any(row["path"] == "evidence-index.json" for row in json.loads(Path(result.raw_evidence_index).read_bytes())["files"])


def test_license_and_source_version_conflicts_stop(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    wrong_license = copy.deepcopy(declaration)
    wrong_license["license"]["value"] = "Apache-2.0"
    license_result = invoke(source, tmp_path / "license-bundle", wrong_license, plan)
    assert license_result.state == "stopped" and "license conflict" in license_result.reason
    wrong_version = copy.deepcopy(declaration)
    wrong_version["source_version"] = "1.0.0"
    version_result = invoke(source, tmp_path / "version-bundle", wrong_version, plan)
    assert version_result.state == "stopped" and "source identity conflict" in version_result.reason


def test_legacy_adapter_retains_flat_subset_semantics(tmp_path):
    source, _, _, _ = fixture(tmp_path)
    output = tmp_path / "legacy"
    result = materialize_source_bound_bundle(source_dir=source, output_dir=output, source_files=("SKILL.md",), source_repository="https://example.invalid/source", source_commit="abc", task_id="test", task_digest="sha256:test", upstream_validator="validator-7")
    assert result["source_files"] == ["SKILL.md"] and result["source_bytes_preserved"] is True
    assert (output / "MANIFEST.yaml").is_file() and not (output / "source_payload").exists()


def test_malformed_license_is_recorded_inside_attempt(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    declaration["license"] = 7
    result = invoke(source, tmp_path / "bundle", declaration, plan)
    assert result.state == "stopped" and "license record" in result.reason
    assert json.loads((tmp_path / "bundle.evidence/raw/receipt.json").read_bytes())["exit_code"] == 1
    assert Path(result.raw_evidence_index).is_file()


def test_missing_first_source_archives_remaining_files(tmp_path):
    source, files, declaration, plan = fixture(tmp_path)
    (source / "SKILL.md").unlink()
    result = invoke(source, tmp_path / "bundle", declaration, plan)
    assert result.state == "stopped"
    raw = tmp_path / "bundle.evidence/raw"
    assert (raw / "source_payload/MANIFEST.yaml").read_bytes() == files["MANIFEST.yaml"]
    assert (raw / "source_payload/source_identity.json").read_bytes() == files["source_identity.json"]
    rows = json.loads((raw / "files.json").read_bytes())["files"]
    assert any(row["path"] == "raw/source_payload/SKILL.md" and row["present"] is False for row in rows)


@pytest.mark.parametrize("fault", ["provenance-receipt.json", "files.json", "evidence-index.json"])
def test_evidence_write_failure_never_leaves_success_receipt(tmp_path, fault):
    source, _, declaration, plan = fixture(tmp_path)
    secure = _secure_core()
    original = secure.put

    def write(fd, name, data):
        if name == fault:
            raise OSError(f"injected {fault} failure")
        return original(fd, name, data)

    with patch.object(secure, "put", write):
        with pytest.raises(EvidencePersistenceError, match="evidence-persistence failure"):
            invoke(source, tmp_path / "bundle", declaration, plan)
    assert not (tmp_path / "bundle.evidence/final.json").exists()
    assert json.loads((tmp_path / "bundle.evidence/failed.json").read_bytes())["exit_code"] == 1
    for receipt in (tmp_path / "bundle/management_metadata/provenance-receipt.json",
                    tmp_path / "bundle.evidence/raw/receipt.json"):
        if receipt.exists():
            pending = json.loads(receipt.read_bytes())
            assert pending["state"] == "pending" and pending["exit_code"] is None


def test_dangling_output_symlink_is_rejected(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    output = tmp_path / "bundle"
    try:
        output.symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(ValueError, match="symlink/reparse"):
        invoke(source, output, declaration, plan)
    assert output.is_symlink() and not (tmp_path / "outside").exists()


def test_output_parent_symlink_is_rejected(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    link = tmp_path / "linked"
    try:
        link.symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(ValueError, match="symlink/reparse"):
        invoke(source, link / "bundle", declaration, plan)
    assert not (tmp_path / "outside").exists()


def test_unwritable_initial_evidence_raises_identifiable_failure(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    secure = _secure_core()
    original = secure.put

    def write(fd, name, data):
        if name == "stdout.bin":
            raise OSError("storage unavailable")
        return original(fd, name, data)

    with patch.object(secure, "put", write):
        with pytest.raises(EvidencePersistenceError, match="evidence-persistence failure"):
            invoke(source, tmp_path / "bundle", declaration, plan)


def test_attempt_collision_preserves_historical_bytes(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    attempt = tmp_path / "bundle.evidence"
    attempt.mkdir()
    prior = b'{"prior":"unchanged"}\n'
    (attempt / "failed.json").write_bytes(prior)
    from skillwiki.layers.skill_governance.dual_bundle import AttemptCollisionError
    with pytest.raises(AttemptCollisionError, match="collision"):
        invoke(source, tmp_path / "bundle", declaration, plan)
    assert (attempt / "failed.json").read_bytes() == prior
    assert not (tmp_path / "bundle").exists()


def test_attempt_symlink_does_not_write_outside(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    attempt = tmp_path / "bundle.evidence"
    try:
        attempt.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    from skillwiki.layers.skill_governance.dual_bundle import UnsafeEvidencePathError
    with pytest.raises(UnsafeEvidencePathError, match="symlink/reparse"):
        invoke(source, tmp_path / "bundle", declaration, plan)
    assert list(outside.iterdir()) == []


def test_cleanup_unlink_denied_keeps_only_pending_receipts(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    secure = _secure_core()
    original_write, original_unlink = secure.put, os.unlink

    def write(fd, name, data):
        if name == "files.json":
            raise OSError("injected inventory failure")
        return original_write(fd, name, data)

    def unlink(path, *args, **kwargs):
        if path == "provenance-receipt.json":
            raise PermissionError("injected unlink denial")
        return original_unlink(path, *args, **kwargs)

    with patch.object(secure, "put", write), patch.object(os, "unlink", unlink):
        with pytest.raises(EvidencePersistenceError, match="evidence-persistence failure"):
            invoke(source, tmp_path / "bundle", declaration, plan)
    assert not (tmp_path / "bundle.evidence/final.json").exists()
    assert json.loads((tmp_path / "bundle.evidence/failed.json").read_bytes())["exit_code"] == 1
    for receipt in (tmp_path / "bundle/management_metadata/provenance-receipt.json",
                    tmp_path / "bundle.evidence/raw/receipt.json"):
        assert json.loads(receipt.read_bytes())["state"] == "pending"


def test_final_status_replace_denied_is_not_success(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    secure = _secure_core()
    original_replace = secure.replace

    def replace(fd, name, data):
        if name == "final.json":
            raise PermissionError("injected final replace denial")
        return original_replace(fd, name, data)

    with patch.object(secure, "replace", replace):
        with pytest.raises(EvidencePersistenceError, match="evidence-persistence failure"):
            invoke(source, tmp_path / "bundle", declaration, plan)
    assert not (tmp_path / "bundle.evidence/final.json").exists()
    assert json.loads((tmp_path / "bundle.evidence/failed.json").read_bytes())["exit_code"] == 1


def test_final_and_failed_status_replace_denied_raises(tmp_path):
    source, _, declaration, plan = fixture(tmp_path)
    secure = _secure_core()
    original_replace = secure.replace

    def replace(fd, name, data):
        if name in {"final.json", "failed.json"}:
            raise PermissionError("injected status replacement denial")
        return original_replace(fd, name, data)

    with patch.object(secure, "replace", replace):
        with pytest.raises(EvidencePersistenceError, match="evidence-persistence failure"):
            invoke(source, tmp_path / "bundle", declaration, plan)
    assert not (tmp_path / "bundle.evidence/final.json").exists()
    for receipt in (tmp_path / "bundle/management_metadata/provenance-receipt.json",
                    tmp_path / "bundle.evidence/raw/receipt.json"):
        pending = json.loads(receipt.read_bytes())
        assert pending["state"] == "pending" and pending["exit_code"] is None
