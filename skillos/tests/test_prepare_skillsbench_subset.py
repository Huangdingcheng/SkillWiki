from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_subset_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_skillsbench_subset.py"
    spec = importlib.util.spec_from_file_location("prepare_skillsbench_subset", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_tasks_are_cross_domain_and_deduped() -> None:
    module = _load_subset_module()

    tasks = module.normalize_task_ids(["citation-check", "citation-check", "sales-pivot-analysis"])

    assert tasks == ["citation-check", "sales-pivot-analysis"]
    assert set(module.DEFAULT_TASK_IDS) >= {
        "citation-check",
        "sales-pivot-analysis",
        "software-dependency-audit",
    }


def test_safe_repo_path_rejects_escape() -> None:
    module = _load_subset_module()

    assert module.safe_repo_path("tasks/citation-check/task.yaml") == Path("tasks/citation-check/task.yaml")

    for unsafe in ("../secret", "tasks/../../secret", "/absolute/path", "C:/absolute/path"):
        try:
            module.safe_repo_path(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe path accepted: {unsafe}")


def test_build_manifest_records_task_sources(tmp_path: Path) -> None:
    module = _load_subset_module()

    manifest = module.build_manifest(
        output_dir=tmp_path,
        task_ids=["citation-check", "software-dependency-audit"],
        downloaded=[
            {"path": "README.md", "size": 3030, "sha": "readme"},
            {"path": "tasks/citation-check/task.yaml", "size": 100, "sha": "task"},
        ],
        skipped=[{"path": "tasks/large/file.bin", "reason": "too large"}],
        repo_head="d86a55c",
    )

    assert manifest["repo"] == "benchflow-ai/skillsbench"
    assert manifest["repo_head"] == "d86a55c"
    assert manifest["task_ids"] == ["citation-check", "software-dependency-audit"]
    assert manifest["downloaded_count"] == 2
    assert manifest["skipped"][0]["reason"] == "too large"
    assert str(tmp_path) in manifest["output_dir"]


def test_select_task_entries_prefers_core_files_and_limits_count() -> None:
    module = _load_subset_module()
    entries = [
        {"path": "tasks/demo/assets/large.bin", "size": 1},
        {"path": "tasks/demo/task.yaml", "size": 1},
        {"path": "tasks/demo/README.md", "size": 1},
        {"path": "tasks/demo/verifier.py", "size": 1},
    ]

    selected, skipped = module.select_task_entries("demo", entries, max_files_per_task=2)

    assert [item["path"] for item in selected] == ["tasks/demo/task.yaml", "tasks/demo/README.md"]
    assert {"path": "tasks/demo/verifier.py", "reason": "max files per task reached"} in skipped
    assert {"path": "tasks/demo/assets/large.bin", "reason": "max files per task reached"} in skipped
