"""Real Git/process evidence for the source/environment governance seam."""
import hashlib
import importlib.metadata
import json
import inspect
import subprocess
from pathlib import Path

import pytest

from skillwiki.layers.skill_governance.arex_skillguard_adapter import (
    observe_arex_source_refresh, build_arex_refresh_prompt, inspect_skillguard_environment,
)
from skillwiki.layers.skill_governance.component_checks import arex_source_receipts


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.fixture
def source_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "local-test@example.invalid")
    git(repo, "config", "user.name", "Local source fixture")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "api.py").write_text("def query(value):\n    return value\n", encoding="utf-8")
    (repo / "README.md").write_text("Unrelated documentation\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline")
    return repo, git(repo, "rev-parse", "HEAD")


def commit(repo, message="change"):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def test_unchanged_and_unrelated_commit_use_real_upstream_subprocess(source_repo):
    repo, baseline = source_repo
    observed = observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline,
                                            relevant_paths=["api.py", "api.py"])
    assert observed["observed_state"] == "unchanged"
    assert observed["relevant_paths"] == ["api.py"]
    repeated = observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline, relevant_paths=["api.py"])
    assert repeated["revision_key"] == observed["revision_key"]
    assert observed["upstream_provenance"]["returncode"] == 0
    assert observed["upstream_provenance"]["result"]["status"] == "current"
    assert "-I" in observed["upstream_provenance"]["argv"]
    (repo / "README.md").write_text("Updated unrelated docs\n", encoding="utf-8")
    commit(repo)
    observed = observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline, relevant_paths=["api.py"])
    assert observed["observed_state"] == "unchanged"
    assert observed["upstream_provenance"]["result"]["status"] == "stale"
    assert observed["delta"]["changed_paths"] == []


def test_two_commit_api_delta_drives_candidate_prompt_without_model(source_repo):
    repo, baseline = source_repo
    (repo / "api.py").write_text("def query(value, *, timeout=10):\n    return value\n", encoding="utf-8")
    head = commit(repo)
    observed = observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline, relevant_paths=["api.py"])
    assert observed["observed_state"] == "updated"
    assert observed["delta"]["baseline_commit"] == baseline
    assert observed["delta"]["observed_commit"] == head
    assert observed["delta"]["changed_paths"] == ["api.py"]
    assert "+def query(value, *, timeout=10):" in observed["delta"]["diff"]
    prompt = build_arex_refresh_prompt(observation=observed, current_skill_content="# Existing identity\nquery(value)")
    assert "Refresh Repo Skill" in prompt
    assert "change-detection-and-staleness-audit.md" in prompt
    assert "timeout=10" in prompt
    assert "candidate_content" in prompt
    assert observed["model_calls"] == 0
    assert observed["native_cases_executed"] == 0
    assert observed["promotion_allowed"] is False
    assert git(repo, "status", "--porcelain") == ""
    json.dumps(observed)  # Persistence-ready, including actual raw process output.


def test_dirty_removed_and_invalid_provenance_are_distinct(source_repo):
    repo, baseline = source_repo
    (repo / "api.py").write_text("# uncommitted replacement\n", encoding="utf-8")
    dirty = observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline, relevant_paths=["api.py"])
    assert dirty["observed_state"] == "stale"
    assert dirty["delta"]["dirty_status"]
    (repo / "api.py").unlink()
    commit(repo)
    removed = observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline, relevant_paths=["api.py"])
    assert removed["observed_state"] == "removed"
    assert removed["delta"]["removed_paths"] == ["api.py"]
    unknown = observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline, relevant_paths=["api.py"],
                                         skill_provenance={"repository": {"commit": "wrong"}})
    assert unknown["observed_state"] == "unknown"
    assert unknown["upstream_provenance"]["returncode"] == 1
    with pytest.raises(ValueError, match="known source change"):
        build_arex_refresh_prompt(observation=unknown, current_skill_content="old")


def test_invalid_baseline_and_path_escape_fail_closed(source_repo):
    repo, baseline = source_repo
    unknown = observe_arex_source_refresh(repo_path=repo, baseline_commit="missing-ref", relevant_paths=["api.py"])
    assert unknown["observed_state"] == "unknown"
    with pytest.raises(ValueError, match="repository-relative"):
        observe_arex_source_refresh(repo_path=repo, baseline_commit=baseline, relevant_paths=["../outside"])


def test_pinned_assets_all_match_recorded_raw_receipts():
    receipts = arex_source_receipts()
    assert len(receipts) == 7
    assert all(len(row["sha256"]) == 64 for row in receipts)
    assert any(row["path"].endswith("run_native_cases.py") for row in receipts)


def test_local_dependency_observation_dedup_drift_missing_unknown():
    version = importlib.metadata.version("pytest")
    contract = {"distribution": "pytest", "module": "pytest", "expected_version": version}
    present = inspect_skillguard_environment(dependencies=[contract, contract])
    assert present["status"] == "pass"
    assert len(present["observations"]) == 1
    assert present["observations"][0]["observed_version"] == version
    assert present["observations"][0]["module_state"] == "present"
    assert present["probe_receipt"]["returncode"] == 0
    drift = inspect_skillguard_environment(dependencies=[{**contract, "expected_version": "0.0.0-impossible"}])
    assert drift["status"] == "block"
    missing = inspect_skillguard_environment(dependencies=[{"distribution": "skillwiki_nonexistent_82828",
        "module": "skillwiki_nonexistent_82828", "expected_version": "1"}])
    assert missing["status"] == "block"
    unknown = inspect_skillguard_environment(dependencies=[{"module": "json"}])
    assert unknown["status"] == "defer"
    assert inspect_skillguard_environment()["status"] == "defer"


def test_actual_api_verifier_bytes_and_missing_identity(source_repo):
    repo, baseline = source_repo
    digest = hashlib.sha256((repo / "api.py").read_bytes()).hexdigest()
    api = {"path": "api.py", "symbol": "query", "expected_sha256": digest}
    verifier = {"path": "README.md", "expected_sha256": hashlib.sha256((repo / "README.md").read_bytes()).hexdigest()}
    assert inspect_skillguard_environment(repo_path=repo, api_contracts=[api], verifier_contracts=[verifier])["status"] == "pass"
    (repo / "api.py").write_text("def replacement():\n    pass\n", encoding="utf-8")
    drift = inspect_skillguard_environment(repo_path=repo, api_contracts=[api])
    assert drift["status"] == "block"
    assert drift["observations"][0]["symbol_present"] is False
    assert inspect_skillguard_environment(repo_path=repo, verifier_contracts=[{"path":"absent", "expected_sha256":digest}])["status"] == "block"
    assert inspect_skillguard_environment(repo_path=repo, api_contracts=[{"path":"api.py"}])["status"] == "defer"


def test_installed_api_signature_observed_in_isolated_process():
    contract = {"module": "json", "attribute": "loads", "expected_signature": str(inspect.signature(json.loads))}
    actual = inspect_skillguard_environment(api_contracts=[contract, contract])
    assert actual["status"] == "pass"
    assert len(actual["observations"]) == 1
    assert actual["observations"][0]["observed_signature"] == contract["expected_signature"]
    assert actual["api_probe_receipt"]["returncode"] == 0
    drift = inspect_skillguard_environment(api_contracts=[{**contract, "expected_signature": "()"}])
    assert drift["status"] == "block"
    assert inspect_skillguard_environment(api_contracts=[{"module":"json", "attribute":"nonexistent_api", "expected_signature":"()"}])["status"] == "block"


def test_real_comment_only_commit_passes_without_masking_runtime_missing(source_repo):
    repo, baseline = source_repo
    original = (repo / "api.py").read_text(encoding="utf-8")
    (repo / "api.py").write_text("# documentation comment\n" + original, encoding="utf-8")
    commit(repo)
    report = inspect_skillguard_environment(repo_path=repo, baseline_commit=baseline)
    assert report["comment_only"] is True
    assert report["status"] == "pass"
    missing = inspect_skillguard_environment(repo_path=repo, baseline_commit=baseline,
         dependencies=[{"distribution":"nonexistent_skillwiki_dependency", "module":"nonexistent_skillwiki_dependency", "expected_version":"1"}])
    assert missing["comment_only"] is True
    assert missing["status"] == "block"
    (repo / "api.py").write_text("def changed_behavior():\n    return False\n", encoding="utf-8")
    dirty = inspect_skillguard_environment(repo_path=repo, baseline_commit=baseline)
    assert dirty["comment_only"] is None
    assert dirty["status"] == "defer"

