"""Real local process-root boundary and deterministic exception preservation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from skillwiki.layers.skill_governance.git_version_store import GitVersionStore, GitVersionStoreError
from skillwiki.storage.skill_repo.common import GitSkillStore


@pytest.mark.skipif(os.name != "nt", reason="Windows process-root boundary")
@pytest.mark.parametrize("backend", ["skill", "governance"])
@pytest.mark.parametrize("existing", [False, True])
def test_overlong_repository_root_reports_path_without_deleting_data(backend, existing):
    evidence = Path(__file__).resolve().parents[2] / "research/stage2-runtime-repair-20261030/evidence/independent-review-20261031-001"
    attempt = evidence / ("root-boundary-" + uuid.uuid4().hex[:10])
    attempt.mkdir(parents=True, exist_ok=False)
    root = attempt / ("a" * 65) / ("b" * 65)
    root = Path(chr(92) * 2 + "?" + chr(92) + str(root))
    root.mkdir(parents=True)
    sentinel = root / "preserved.txt"
    sentinel.write_bytes(b"independent existing data\n")
    global_before = subprocess.run(["git", "config", "--global", "--get-all", "core.longpaths"], capture_output=True)
    before_config = None
    if existing:
        seed = attempt / "seed"
        seed.mkdir()
        subprocess.run(["git", "init"], cwd=seed, capture_output=True, check=True)
        subprocess.run(["git", "config", "--local", "core.longpaths", "false"], cwd=seed, capture_output=True, check=True)
        shutil.copytree(seed / ".git", root / ".git")
        before_config = (root / ".git/config").read_bytes()
    error = None
    try:
        if backend == "skill":
            GitSkillStore(root).init_repo(initial_commit=False)
        else:
            GitVersionStore(root).repository_status()
    except RuntimeError as exc:
        error = str(exc)
    assert error is not None, "This test records the current Windows unsupported process-root boundary"
    assert "repository path" in error
    assert str(root) in error
    assert "process working-directory limits" in error
    assert "core.longpaths" in error
    assert sentinel.read_bytes() == b"independent existing data\n"
    if existing:
        assert (root / ".git/config").read_bytes() == before_config
    global_after = subprocess.run(["git", "config", "--global", "--get-all", "core.longpaths"], capture_output=True)
    assert (global_before.returncode, global_before.stdout, global_before.stderr) == (global_after.returncode, global_after.stdout, global_after.stderr)
    (attempt / "receipt.json").write_text(json.dumps({"backend": backend, "existing": existing, "root_length": len(str(root)), "error": error, "existing_data_preserved": True, "global_longpaths_unchanged": True}, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("backend", ["skill", "governance"])
def test_os_path_error_is_not_misreported_as_missing_repository(tmp_path, monkeypatch, backend):
    repo = tmp_path / "repo"
    repo.mkdir()
    def fail(*args, **kwargs):
        raise NotADirectoryError(267, "Invalid process working directory")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="Cannot start Git in repository path"):
        if backend == "skill":
            GitSkillStore(repo)._run_git(["status"])
        else:
            GitVersionStore(repo).repository_status()


def test_missing_repository_path_does_not_claim_git_executable_is_missing(tmp_path):
    with pytest.raises(GitVersionStoreError, match="repository path"):
        GitVersionStore(tmp_path / "absent").repository_status()
