from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from skillwiki.layers.skill_runtime.harness import benchflow_runtime_evidence as runtime
from skillwiki.layers.skill_runtime.harness import benchflow_current_registry as registry
from skillwiki.layers.skill_runtime.harness.benchflow_evidence import read_benchflow_evidence
from tests.test_benchflow_evidence import _write_upstream_shaped_fixture


def setup_source(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    package = checkout / "src" / "benchflow"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# controlled runtime fixture\n")
    (checkout / "pyproject.toml").write_text("# controlled distribution fixture\n")
    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: SimpleNamespace(__file__=str(package / "__init__.py")))
    monkeypatch.setattr(runtime.importlib_metadata, "version", lambda _: "0.7.9.dev0")
    def git(argv, **kwargs):
        if "--show-toplevel" in argv:
            return str(checkout)
        if "rev-parse" in argv:
            return "b" * 40
        return ""
    monkeypatch.setattr(runtime.subprocess, "check_output", git)
    return checkout


def test_source_witness_measures_import_and_binds_run_without_wheel(tmp_path, monkeypatch):
    checkout = setup_source(tmp_path, monkeypatch)
    run = _write_upstream_shaped_fixture(tmp_path)
    measured = runtime.measure_source_runtime(checkout)
    payload = runtime.write_source_runtime_attestation(run, checkout_root=checkout, expected_measurement=measured)
    evidence = read_benchflow_evidence(run, expected_runtime_version="0.7.9.dev0")
    assert evidence.runtime_attestation_valid
    assert evidence.status == "passed"
    assert payload["checkout_commit"] == "b" * 40
    assert "wheel_sha256" not in payload
    assert payload["source_tree_sha256"] == runtime.source_manifest_digest(payload["source_files"])
    before = (run / runtime.SOURCE_RUNTIME_FILENAME).read_bytes()
    with pytest.raises(FileExistsError):
        runtime.write_source_runtime_attestation(run, checkout_root=checkout)
    assert (run / runtime.SOURCE_RUNTIME_FILENAME).read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("checkout_commit", "short"), ("source_dirty", True),
    ("source_tree_sha256", "0" * 64), ("source_files", []),
    ("import_path", "not/the/import"), ("result_sha256", "0" * 64),
])
def test_source_witness_tampering_blocks(tmp_path, monkeypatch, field, value):
    checkout = setup_source(tmp_path, monkeypatch)
    run = _write_upstream_shaped_fixture(tmp_path)
    payload = runtime.write_source_runtime_attestation(run, checkout_root=checkout)
    payload[field] = value
    (run / runtime.SOURCE_RUNTIME_FILENAME).write_text(json.dumps(payload))
    evidence = read_benchflow_evidence(run)
    assert not evidence.runtime_attestation_valid
    assert evidence.status == "blocked"
    assert not evidence.model_result_eligible


def test_source_changes_after_admission_block_attestation(tmp_path, monkeypatch):
    checkout = setup_source(tmp_path, monkeypatch)
    run = _write_upstream_shaped_fixture(tmp_path)
    measured = runtime.measure_source_runtime(checkout)
    (checkout / "src" / "benchflow" / "__init__.py").write_text("# changed after admission\n")
    with pytest.raises(ValueError, match="runtime changed"):
        runtime.write_source_runtime_attestation(run, checkout_root=checkout, expected_measurement=measured)
    assert not (run / runtime.SOURCE_RUNTIME_FILENAME).exists()


def test_wrong_import_and_dirty_checkout_cannot_be_measured(tmp_path, monkeypatch):
    checkout = setup_source(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: SimpleNamespace(__file__=str(tmp_path / "other.py")))
    with pytest.raises(ValueError, match="not the requested source"):
        runtime.measure_source_runtime(checkout)
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: SimpleNamespace(__file__=str(checkout / "src" / "benchflow" / "__init__.py")))
    def git(argv, **kwargs):
        return str(checkout) if "--show-toplevel" in argv else "b" * 40 if "rev-parse" in argv else " M src/benchflow/__init__.py"
    monkeypatch.setattr(runtime.subprocess, "check_output", git)
    with pytest.raises(ValueError, match="committed"):
        runtime.measure_source_runtime(checkout)


def test_two_runtime_witnesses_are_ambiguous(tmp_path, monkeypatch):
    checkout = setup_source(tmp_path, monkeypatch)
    run = _write_upstream_shaped_fixture(tmp_path)
    runtime.write_source_runtime_attestation(run, checkout_root=checkout)
    (run / runtime.RUNTIME_ATTESTATION_FILENAME).write_text("{}")
    evidence = read_benchflow_evidence(run)
    assert evidence.status == "blocked"
    assert any("ambiguous" in e for e in evidence.errors)


def test_current_receipt_binds_source_witness_and_skill_mode(tmp_path, monkeypatch):
    checkout = setup_source(tmp_path, monkeypatch)
    run = _write_upstream_shaped_fixture(tmp_path)
    payload = runtime.write_source_runtime_attestation(run, checkout_root=checkout)
    monkeypatch.setattr(registry, "_ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(registry, "_REGISTRY_ROOT", tmp_path / "registrations")
    observed = read_benchflow_evidence(run).observed
    receipt = {"run_id": "source-fixture", "run_path": str(run),
               "status": "completed", "runtime_version": "0.7.9.dev0",
               "skill_mode": observed["skill_mode"]}
    registered = registry.register_current_benchflow_run(receipt)
    assert registered["evidence_integrity"] == "verified"
    assert registered["promotion_allowed"] is False
    assert runtime.SOURCE_RUNTIME_FILENAME in registered["raw_artifacts"]
    with pytest.raises(FileExistsError):
        registry.register_current_benchflow_run(receipt)
    wrong = registry.register_current_benchflow_run({**receipt, "run_id": "wrong-mode", "skill_mode": "wrong"})
    assert wrong["evidence_integrity"] == "mismatch"
    payload["checkout_commit"] = "c" * 40
    (run / runtime.SOURCE_RUNTIME_FILENAME).write_text(json.dumps(payload))
    reread = registry.get_current_benchflow_run("source-fixture")
    assert reread["evidence_integrity"] == "mismatch"
    assert any("hash mismatch" in e for e in reread["integrity_errors"])
