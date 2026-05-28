from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


def _load_runner_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_input_skill_eval.py"
    spec = importlib.util.spec_from_file_location("run_input_skill_eval", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_manifest_normalizes_fixture_paths_and_hash(tmp_path: Path) -> None:
    runner = _load_runner_module()
    fixture_root = tmp_path / "fixtures"
    fixture_file = fixture_root / "document" / "runbook.md"
    fixture_file.parent.mkdir(parents=True)
    fixture_file.write_text("# Restart service\n\n1. Check status\n2. Restart safely\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "fixtures": [
                    {
                        "source_id": "doc-restart-service",
                        "input_type": "document",
                        "domain": "software",
                        "content_file": "document/runbook.md",
                        "source_url": "https://example.test/runbook",
                        "paper_or_project": "local fixture",
                        "expected_skill_shape": "functional",
                        "license_note": "test-only",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = runner.load_manifest(manifest, fixture_root=fixture_root, max_chars=0)

    assert len(cases) == 1
    case = cases[0]
    assert case.source_id == "doc-restart-service"
    assert case.input_type == "document"
    assert case.local_path == fixture_file
    assert case.content.startswith("# Restart service")
    assert case.content_sha256 == hashlib.sha256(case.content.encode("utf-8")).hexdigest()
    assert case.manifest["domain"] == "software"


def test_candidate_payload_preserves_metadata_and_run_provenance() -> None:
    runner = _load_runner_module()
    case = runner.FixtureCase(
        source_id="api-petstore-add-pet",
        input_type="api_doc",
        domain="api",
        source_url="https://example.test/openapi.yaml",
        paper_or_project="OpenAPI sample",
        expected_skill_shape="atomic",
        license_note="public docs",
        local_path=Path("openapi.yaml"),
        content="openapi: 3.0.0",
        content_sha256="abc123",
        manifest={},
        truncated=False,
    )
    unit = {
        "unit_id": "unit-1",
        "source_type": "api_doc",
        "proposed_skill_name": "Add Pet",
        "proposed_description": "Create a pet through the API.",
        "proposed_type": "atomic",
        "index_keywords": ["petstore", "api"],
        "metadata": {
            "candidate_interface": {
                "input_schema": {"type": "object", "required": ["name"]},
                "output_schema": {"type": "object", "required": ["pet_id"]},
                "preconditions": ["API server is reachable."],
                "postconditions": ["Pet exists."],
            },
            "candidate_implementation": {
                "prompt_template": "Call POST /pet with a valid body.",
                "tool_calls": ["http.post"],
            },
            "candidate_relations": {"dependency_ids": ["auth_token_skill"]},
        },
    }

    payload = runner.candidate_review_payload(unit, case, run_id="run-20260526")

    assert payload["name"] == "add_pet_api_petstore_add_pet"
    assert payload["source_type"] == "api_doc"
    assert "eval-run:run-20260526" in payload["tags"]
    assert "source-id:api-petstore-add-pet" in payload["tags"]
    assert payload["dependency_ids"] == ["auth_token_skill"]
    assert payload["tool_calls"] == ["http.post"]
    provenance = payload["provenance"]
    assert provenance["source_type"] == "api_doc"
    assert provenance["source_ids"] == ["api-petstore-add-pet", "unit-1"]
    context = provenance["creation_context"]
    assert context["content_sha256"] == "abc123"
    assert context["eval_run_id"] == "run-20260526"
    assert context["fixture_import_mode"] == "isolated_eval_candidate"


def test_candidate_payload_uses_source_suffix_to_avoid_eval_name_collisions() -> None:
    runner = _load_runner_module()
    unit = {
        "unit_id": "unit-1",
        "source_type": "document",
        "proposed_skill_name": "document_grounded_extractor",
        "proposed_description": "Extract a document-grounded procedure.",
        "proposed_type": "functional",
        "metadata": {},
    }
    first = runner.FixtureCase(
        source_id="document-kubernetes-pending-pod-runbook",
        input_type="document",
        domain="software",
        source_url="https://example.test/a",
        paper_or_project="test",
        expected_skill_shape="functional",
        license_note="test-only",
        local_path=Path("a.md"),
        content="a",
        content_sha256="sha-a",
        manifest={},
    )
    second = runner.FixtureCase(
        source_id="document-kubernetes-termination-message",
        input_type="document",
        domain="software",
        source_url="https://example.test/b",
        paper_or_project="test",
        expected_skill_shape="functional",
        license_note="test-only",
        local_path=Path("b.md"),
        content="b",
        content_sha256="sha-b",
        manifest={},
    )

    first_payload = runner.candidate_review_payload(unit, first, run_id="run")
    second_payload = runner.candidate_review_payload(unit, second, run_id="run")

    assert first_payload["name"] != second_payload["name"]
    assert first_payload["name"].startswith("document_grounded_extractor_")
    assert second_payload["name"].startswith("document_grounded_extractor_")


def test_safe_filename_is_short_enough_for_windows_run_artifacts() -> None:
    runner = _load_runner_module()
    long_label = (
        "version_document_document_skillsbench_03_instruction_"
        "software_dependency_audit_1_snapshot"
    )

    filename = runner.safe_filename(long_label)

    assert len(filename) <= 96
    assert filename.startswith("version_document")


def test_score_fixture_record_rewards_full_workflow_more_than_parse_only() -> None:
    runner = _load_runner_module()
    parse_only = {
        "parse": {
            "http_status": 200,
            "success": True,
            "unit_count": 1,
            "schema_completeness": 0.8,
            "ctx2skill_evidence_completeness": 0.6,
            "layer_correctness": 0.5,
        },
        "audit": {"status": "not_run"},
        "create": {"status": "not_run"},
        "graph": {"status": "not_run"},
        "version": {"status": "not_run"},
        "harness": {"status": "not_run"},
        "skillsbench": {"status": "not_run"},
    }
    full_workflow = {
        **parse_only,
        "audit": {"http_status": 200, "passed": True, "audit_score": 0.9},
        "create": {"http_status": 201, "success": True, "created_skill_id": "skill-1"},
        "graph": {"http_status": 200, "skill_present": True, "relation_count": 2},
        "version": {"http_status": 200, "business_diff_available": True, "snapshot_created": True},
        "harness": {"status": "verified", "positive_pass": True, "negative_rejected": True},
        "skillsbench": {"status": "mapped", "verifier_ran": True, "generated_pass": True},
    }

    parse_score = runner.score_fixture_record(parse_only)
    full_score = runner.score_fixture_record(full_workflow)

    assert 0 < parse_score < full_score
    assert full_score >= 0.9
