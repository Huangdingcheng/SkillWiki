from __future__ import annotations

import importlib.util
import sys


def _load_module():
    script_path = __file__
    from pathlib import Path

    path = Path(script_path).resolve().parents[2] / "scripts" / "report_skillsbench_mapping.py"
    spec = importlib.util.spec_from_file_location("report_skillsbench_mapping", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_report_payload_maps_created_skills_to_benchmark_tasks() -> None:
    module = _load_module()
    manifest = {
        "fixtures": [
            {
                "source_id": "doc-citation",
                "input_type": "document",
                "domain": "science",
                "paper_or_project": "SkillsBench",
                "source_url": "https://example.test",
                "expected_skill_shape": "functional",
                "target_benchmark_tasks": ["citation-check"],
            }
        ]
    }
    input_summary = {
        "manifest": "manifest.json",
        "run_dir": "run",
        "scores": {"overall": 0.91},
        "skill_counts": {"delta": 1},
        "records": [
            {
                "fixture": {"source_id": "doc-citation"},
                "parse": {"success": True},
                "audit": {"passed_count": 1},
                "create": {
                    "created_skill_ids": ["skill-1"],
                    "items": [{"created_skill_name": "document_grounded_doc_citation"}],
                },
                "graph": {"present_count": 1},
                "version": {"business_diff_available_count": 1, "snapshot_created_count": 1},
                "score": 0.91,
            }
        ],
    }
    task_check = {
        "skillsbench_sparse_root": "skillsbench",
        "results": [{"task": "citation-check", "valid": True, "returncode": 0, "stdout": "valid"}],
    }

    report = module.build_report_payload(
        manifest=manifest,
        input_eval_summary=input_summary,
        task_check=task_check,
        oracle_result={"task_name": "citation-check", "agent": "oracle", "error": "[WinError 2]"},
        run_label="test",
        docker_available=False,
    )

    assert report["summary"]["mapped_fixture_task_pairs"] == 1
    assert report["summary"]["candidate_created_pairs"] == 1
    assert report["by_task"]["citation-check"]["fixture_count"] == 1
    assert report["by_task"]["citation-check"]["official_task_check_valid"] is True
    assert report["official_oracle"]["status"] == "blocked"
    assert report["mappings"][0]["created_skill_ids"] == ["skill-1"]


def test_render_markdown_states_official_score_boundary() -> None:
    module = _load_module()
    report = {
        "generated_at": "2026-05-27T00:00:00",
        "input_eval_run": "run",
        "skillsbench_subset": "subset",
        "summary": {
            "mapped_fixture_task_pairs": 1,
            "mapped_benchmark_tasks": 1,
            "candidate_created_pairs": 1,
            "task_check_valid_count": 1,
            "task_check_total": 1,
        },
        "official_oracle": {
            "attempted": True,
            "docker_available": False,
            "status": "blocked",
            "error": "[WinError 2]",
        },
        "by_task": {
            "citation-check": {
                "fixture_count": 1,
                "candidate_created_count": 1,
                "graph_present_count": 1,
                "business_diff_count": 1,
                "snapshot_count": 1,
                "mean_local_workflow_score": 0.91,
                "input_types": {"document": 1},
                "domains": {"science": 1},
                "official_task_check_valid": True,
            }
        },
        "claim_boundary": "Official sandboxed scores are not claimed.",
    }

    markdown = module.render_markdown(report)

    assert "Official SkillsBench Status" in markdown
    assert "Oracle blocker" in markdown
    assert "official sandboxed oracle/no-skill/generated-skill scores are not claimed" in markdown
