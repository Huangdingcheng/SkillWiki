from skillwiki.layers.skill_governance.upstream_adapters import (
    FIRST_BATCH_COMPONENTS,
    build_reference_run_receipt,
    build_content_transfer_artifact,
    materialize_source_bound_bundle,
    probe_component,
    run_skill_provenance_validator,
    source_refresh_event,
)
import json
from pathlib import Path
import sys
import pytest


def test_probe_preserves_unavailable_component_state():
    result = probe_component("skillguard")
    assert result.status == "interface_only"
    assert result.imported is False
    assert "no verified" in result.reason


def test_refresh_workflow_without_cli_is_interface_only():
    result = probe_component("arex-skill-refresh")
    assert result.status == "interface_only"
    assert result.imported is False


def test_refresh_unknown_is_deferred_with_evidence():
    event = source_refresh_event(
        "arex-skill-refresh", source_commit="ac3fe1a", observed_state="unknown",
        evidence=["api-metadata.json"],
    )
    assert event["action"] == "defer_until_review"
    assert event["evidence"] == ["api-metadata.json"]


def test_frozen_component_inventory_has_no_paper_eligibility_claim():
    assert set(FIRST_BATCH_COMPONENTS) == {
        "skillnet-fabric", "gepa", "skillopt", "arex-skill-refresh", "skillguard"
    }
    assert all(c.disposition in {"experimental_control", "conditional_ingestion"}
               for c in FIRST_BATCH_COMPONENTS.values())


def test_content_transfer_artifact_preserves_hashes_and_unexecuted_state(tmp_path):
    pack = tmp_path / "pack.md"
    ledger = tmp_path / "ledger.json"
    pack.write_text("reference-card", encoding="utf-8")
    ledger.write_text('{"status":"checked"}', encoding="utf-8")

    artifact = build_content_transfer_artifact(
        reference_pack_path=str(pack),
        ledger_path=str(ledger),
        source_commit="source-commit",
        task_digest="sha256:task",
        upstream_component="gepa",
        license_state="MIT",
    )

    assert artifact.transfer_status == "prepared_unexecuted"
    assert artifact.reference_pack_sha256.startswith("sha256:")
    assert artifact.ledger_sha256.startswith("sha256:")
    assert artifact.successfully_run is False
    assert artifact.fairly_compared is False
    assert artifact.paper_eligible is False


def test_manifest_bridge_copies_source_bytes_and_binds_identity(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("skill\n", encoding="utf-8")
    result = materialize_source_bound_bundle(
        source_dir=source,
        output_dir=tmp_path / "derived",
        source_files=("SKILL.md",),
        source_repository="https://example.invalid/repo.git",
        source_commit="abc123",
        task_id="task-1",
        task_digest="sha256:task",
        upstream_validator="validator@commit",
    )
    assert result["source_bytes_preserved"] is True
    assert (tmp_path / "derived" / "MANIFEST.yaml").is_file()
    assert '"source_commit": "abc123"' in (tmp_path / "derived" / "source_identity.json").read_text()


def test_manifest_bridge_rejects_reserved_and_rooted_source_paths(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("skill\n", encoding="utf-8")
    for bad_path in ("MANIFEST.yaml", "source_identity.json", r"\\outside.txt", "C:outside.txt"):
        with pytest.raises(ValueError):
            materialize_source_bound_bundle(
                source_dir=source,
                output_dir=tmp_path / ("derived-" + bad_path.replace("\\", "x").replace(":", "x")),
                source_files=(bad_path,),
                source_repository="https://example.invalid/repo.git",
                source_commit="abc123",
                task_id="task-1",
                task_digest="sha256:task",
                upstream_validator="validator@commit",
            )


def test_manifest_bridge_rejects_output_inside_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("skill\n", encoding="utf-8")
    with pytest.raises(ValueError):
        materialize_source_bound_bundle(
            source_dir=source,
            output_dir=source / "derived",
            source_files=("SKILL.md",),
            source_repository="https://example.invalid/repo.git",
            source_commit="abc123",
            task_id="task-1",
            task_digest="sha256:task",
            upstream_validator="validator@commit",
        )


def test_official_validator_does_not_promote_zero_exit_without_marker(tmp_path):
    script = tmp_path / "validator.py"
    script.write_text("print('done')\n", encoding="utf-8")
    result = run_skill_provenance_validator(
        validator_script=script,
        bundle_dir=tmp_path,
        runner=(sys.executable,),
    )
    assert result.exit_code == 0
    assert result.status == "failed"
    assert result.all_hashes_verified is False


def test_official_validator_records_missing_runner_as_blocked(tmp_path):
    result = run_skill_provenance_validator(
        validator_script=tmp_path / "validator.sh",
        bundle_dir=tmp_path,
        runner=("missing-validator-runner",),
    )
    assert result.status == "blocked_environment"
    assert result.exit_code is None


def test_official_validator_timeout_is_explicit(tmp_path):
    script = tmp_path / "validator.py"
    script.write_text("import time; time.sleep(1)\n", encoding="utf-8")
    result = run_skill_provenance_validator(
        validator_script=script,
        bundle_dir=tmp_path,
        runner=(sys.executable,),
        timeout_seconds=0.01,
    )
    assert result.status == "timeout"
    assert result.all_hashes_verified is False
    assert result.termination_confirmed is False


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_validator_rejects_nonfinite_or_nonpositive_timeout(tmp_path, timeout_seconds):
    with pytest.raises(ValueError, match="positive timeout"):
        run_skill_provenance_validator(
            validator_script=tmp_path / "validator.py",
            bundle_dir=tmp_path,
            runner=(sys.executable,),
            timeout_seconds=timeout_seconds,
        )


@pytest.mark.parametrize("summary", [
    "", "Checked 0 files, skipped 0, unlisted 0, errors 0",
    "Checked 1 files, skipped 1, unlisted 0, errors 0",
    "Checked 1 files, skipped 0, unlisted 1, errors 0",
    "Checked 1 files, skipped 0, unlisted 0, errors 1",
])
def test_zero_exit_marker_cannot_hide_incomplete_validation(tmp_path, summary):
    # A local protocol fixture tests interpretation only, not upstream behavior.
    script = tmp_path / "protocol_fixture.py"
    script.write_text("print(" + repr(summary + "\nAll hashes verified.") + ")\n", encoding="utf-8")
    result = run_skill_provenance_validator(
        validator_script=script, bundle_dir=tmp_path, runner=(sys.executable,),
    )
    assert result.exit_code == 0
    assert result.status == "failed"


@pytest.mark.parametrize("path", ["", "--update", "--allow-unlisted", "relative-bundle"])
def test_validator_rejects_ambiguous_bundle_before_launch(tmp_path, path):
    with pytest.raises(ValueError, match="absolute"):
        run_skill_provenance_validator(validator_script=tmp_path / "validate.sh", bundle_dir=path)


def _provenance_result(case):
    path = (Path(__file__).parents[2] / "artifacts" / "20260929-skill-provenance-b-ref-21" / f"{case}.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _receipt(case):
    evidence = _provenance_result(case)
    return build_reference_run_receipt(
        source_identity={"repository": "snapsynapse/skill-provenance", "commit": "bcddbd2", "license": "MIT"},
        upstream_validator_result=evidence["validator"],
        adapter_materialization={"status": "thin_adapter", "source_bytes_preserved": True},
        skillwiki_integration={"promoted": case == "complete", "upstream_package_integrated": case == "complete"},
        task_execution={"status": "not_run", "model_called": False, "verifier_called": False},
        fair_comparison={"status": "not_run", "compared": False},
        paper_eligibility={"eligible": False, "reason": "no task comparison"},
        explicit_non_claims=("validator result does not claim task quality",),
    )


def test_complete_provenance_receipt_separates_success_from_task_not_run():
    receipt = _receipt("complete").to_dict()
    assert receipt["upstream_validator_result"]["status"] == "success"
    assert receipt["adapter_materialization"]["status"] == "thin_adapter"
    assert receipt["skillwiki_integration"]["promoted"] is True
    assert receipt["task_execution"]["status"] == "not_run"
    assert receipt["fair_comparison"]["compared"] is False
    assert receipt["paper_eligibility"]["eligible"] is False
    assert receipt["explicit_non_claims"]


@pytest.mark.parametrize("case", ["tampered", "missing-manifest"])
def test_failed_provenance_receipt_cannot_promote(case):
    receipt = _receipt(case).to_dict()
    assert receipt["upstream_validator_result"]["status"] == "failed"
    assert receipt["skillwiki_integration"]["promoted"] is False
    assert receipt["task_execution"]["status"] == "not_run"


def test_failed_provenance_promotion_is_rejected():
    evidence = _provenance_result("tampered")
    with pytest.raises(ValueError, match="cannot be promoted"):
        build_reference_run_receipt(
            source_identity={"repository": "snapsynapse/skill-provenance", "commit": "bcddbd2", "license": "MIT"},
            upstream_validator_result=evidence["validator"],
            adapter_materialization={"status": "thin_adapter"},
            skillwiki_integration={"promoted": True},
            task_execution={"status": "not_run"},
            fair_comparison={"status": "not_run"},
            paper_eligibility={"eligible": False},
        )


def test_gepa_derived_text_receipt_stays_adapter_only():
    manifest = json.loads((Path(__file__).parents[2] / "artifacts" / "20260928-gepa-text-adapter" / "B-REF-04" / "adapter-manifest.json").read_text(encoding="utf-8"))
    receipt = build_reference_run_receipt(
        source_identity=manifest["upstream"],
        upstream_validator_result={"component": "gepa", "status": "success", "command": ["gepa"], "stdout": "probe_success=true", "stderr": "", "exit_code": 0},
        adapter_materialization={"status": "derived_text", "upstream_package_integrated": False, "raw_command": manifest["adapter"]["script"]},
        skillwiki_integration={"promoted": False, "upstream_package_integrated": False},
        task_execution={"status": "not_run", "model_called": False, "verifier_called": False},
        fair_comparison={"status": "not_run", "compared": False},
        paper_eligibility={"eligible": False},
    ).to_dict()
    assert receipt["upstream_validator_result"]["status"] == "success"
    assert receipt["adapter_materialization"]["upstream_package_integrated"] is False
    assert receipt["skillwiki_integration"]["promoted"] is False
    assert receipt["task_execution"]["status"] == "not_run"


def test_derived_text_promotion_is_normalized_false_even_when_caller_requests_true():
    receipt = build_reference_run_receipt(
        source_identity={"repository": "gepa-ai/gepa", "version": "0.1.4", "license": "MIT"},
        upstream_validator_result={"status": "success", "command": ["probe"], "stdout": "ok", "stderr": "", "exit_code": 0},
        adapter_materialization={"status": "derived_text", "upstream_package_integrated": False},
        skillwiki_integration={"promoted": True}, task_execution={"status": "not_run"},
        fair_comparison={"compared": False}, paper_eligibility={"eligible": True},
    )
    assert receipt.skillwiki_integration["promoted"] is False


@pytest.mark.parametrize("task_status,compared", [
    ("not_run", True), ("blocked", True), ("failed", True), ("completed", False),
])
def test_paper_eligibility_is_normalized_false_without_task_and_comparison(task_status, compared):
    receipt = build_reference_run_receipt(
        source_identity={"source": "example/source", "commit": "abc", "license": "MIT"},
        upstream_validator_result={"status": "success", "command": ["validate"], "stdout": "ok", "stderr": "", "exit_code": 0},
        adapter_materialization={"status": "thin_adapter", "upstream_package_integrated": True},
        skillwiki_integration={"promoted": False}, task_execution={"status": task_status},
        fair_comparison={"compared": compared}, paper_eligibility={"eligible": True},
    )
    assert receipt.paper_eligibility["eligible"] is False


@pytest.mark.parametrize("identity", [
    {}, {"commit": "abc", "license": "MIT"}, {"repository": "repo", "license": "MIT"},
    {"repository": "repo", "commit": "abc"},
])
def test_receipt_requires_source_identity_fields(identity):
    with pytest.raises(ValueError, match="source_identity requires"):
        build_reference_run_receipt(
            source_identity=identity,
            upstream_validator_result={"status": "success", "command": ["validate"], "stdout": "", "stderr": "", "exit_code": 0},
            adapter_materialization={"status": "thin_adapter"}, skillwiki_integration={},
            task_execution={"status": "not_run"}, fair_comparison={"compared": False}, paper_eligibility={},
        )


@pytest.mark.parametrize("missing", ["command", "stdout", "stderr", "exit_code"])
def test_receipt_requires_raw_validator_evidence(missing):
    evidence = {"status": "success", "command": ["validate"], "stdout": "", "stderr": "", "exit_code": 0}
    del evidence[missing]
    with pytest.raises(ValueError, match="raw evidence"):
        build_reference_run_receipt(
            source_identity={"repository": "repo", "commit": "abc", "license": "MIT"},
            upstream_validator_result=evidence, adapter_materialization={"status": "thin_adapter"},
            skillwiki_integration={}, task_execution={"status": "not_run"},
            fair_comparison={"compared": False}, paper_eligibility={},
        )


def _receipt_inputs():
    return {
        "source_identity": {"repository": "example/repo", "commit": "abc", "license": "MIT"},
        "upstream_validator_result": {
            "status": "success", "command": ["validate"], "stdout": "ok", "stderr": "", "exit_code": 0,
        },
        "adapter_materialization": {"status": "thin_adapter", "upstream_package_integrated": True},
        "skillwiki_integration": {"promoted": False},
        "task_execution": {"status": "not_run"},
        "fair_comparison": {"compared": False},
        "paper_eligibility": {"eligible": False},
    }


@pytest.mark.parametrize("materialization", [
    {"status": "derived_text"}, {"status": "thin_adapter", "upstream_package_integrated": False},
])
def test_unintegrated_adapter_forces_both_promotion_aliases_false(materialization):
    inputs = _receipt_inputs()
    inputs["adapter_materialization"] = materialization
    inputs["skillwiki_integration"] = {"promoted": True, "integrated": True}
    integration = build_reference_run_receipt(**inputs).skillwiki_integration
    assert integration["promoted"] is False
    assert integration["integrated"] is False


@pytest.mark.parametrize("materialization", [
    {"status": "derived_text"}, {"status": "thin_adapter", "upstream_package_integrated": False},
])
def test_unintegrated_adapter_rejects_conflicting_aliases_before_forcing_false(materialization):
    inputs = _receipt_inputs()
    inputs["adapter_materialization"] = materialization
    inputs["skillwiki_integration"] = {"promoted": True, "integrated": False}
    with pytest.raises(ValueError, match="conflict"):
        build_reference_run_receipt(**inputs)


@pytest.mark.parametrize("alias", ["promoted", "integrated"])
@pytest.mark.parametrize("value", [True, False])
def test_single_promotion_alias_normalizes_both(alias, value):
    inputs = _receipt_inputs()
    inputs["skillwiki_integration"] = {alias: value}
    integration = build_reference_run_receipt(**inputs).skillwiki_integration
    assert integration == {"promoted": value, "integrated": value}


def test_conflicting_promotion_aliases_are_rejected():
    inputs = _receipt_inputs()
    inputs["skillwiki_integration"] = {"promoted": False, "integrated": True}
    with pytest.raises(ValueError, match="conflict"):
        build_reference_run_receipt(**inputs)


@pytest.mark.parametrize("status", [None, "", "unknown", "not_run", "blocked", "failed"])
def test_paper_eligibility_requires_explicit_completed_task(status):
    inputs = _receipt_inputs()
    inputs["task_execution"] = {} if status is None else {"status": status}
    inputs["fair_comparison"] = {"compared": True}
    inputs["paper_eligibility"] = {"eligible": True}
    assert build_reference_run_receipt(**inputs).paper_eligibility["eligible"] is False


def test_completed_task_and_comparison_can_keep_eligibility_claim():
    inputs = _receipt_inputs()
    inputs["task_execution"] = {"status": "completed"}
    inputs["fair_comparison"] = {"compared": True}
    inputs["paper_eligibility"] = {"eligible": True}
    assert build_reference_run_receipt(**inputs).paper_eligibility["eligible"] is True


@pytest.mark.parametrize("field,value", [
    ("command", None), ("command", []), ("stdout", None), ("stderr", None),
    ("exit_code", None), ("exit_code", True), ("exit_code", "0"),
])
def test_receipt_rejects_unusable_validator_evidence(field, value):
    inputs = _receipt_inputs()
    inputs["upstream_validator_result"][field] = value
    with pytest.raises(ValueError, match=field):
        build_reference_run_receipt(**inputs)
