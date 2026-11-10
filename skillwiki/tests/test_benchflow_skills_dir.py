from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from skillwiki.models.skill_model import Skill, SkillImplementation, SkillProvenance, SkillState
from skillwiki.layers.skill_runtime.harness.benchflow_skills_dir import (
    BYTE_FAITHFUL,
    PROJECTION_ONLY_NOT_BYTE_FAITHFUL,
    export_raw_skill_package_to_skills_dir,
    export_released_skill_to_skills_dir,
)


def _released_skill() -> Skill:
    return Skill(
        skill_id="parse_dialogue",
        name="parse_dialogue",
        version="1.2.3",
        description="Parse a dialogue file into a graph.",
        state=SkillState.RELEASED,
        implementation=SkillImplementation(prompt_template="Parse {input} into a graph."),
        provenance=SkillProvenance(
            source_type="manual",
            source_ids=["source-snapshot-1"],
            creation_context={"source_snapshot": "release-snapshot-1"},
        ),
    )


def _declared_file(root: Path, relative: str, content: bytes) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "source": relative,
        "target": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "mode": stat.S_IMODE(path.stat().st_mode),
    }


def test_export_projects_released_skill_and_records_hashes(tmp_path: Path):
    artifact_root = tmp_path / "artifact"
    declared = _declared_file(artifact_root, "scripts/parse.py", b"print('parse')\n")
    output = tmp_path / "skills_dir"

    manifest = export_released_skill_to_skills_dir(
        _released_skill(),
        output,
        source_artifact_hash="sha256:" + "a" * 64,
        source_snapshot="release-snapshot-1",
        artifact_root=artifact_root,
        declared_files=[declared],
    )

    skill_path = output / "parse_dialogue" / "SKILL.md"
    copied_path = output / "parse_dialogue" / "scripts" / "parse.py"
    manifest_path = output / "skillwiki_skills_dir_manifest.json"
    assert skill_path.is_file()
    assert copied_path.read_bytes() == b"print('parse')\n"
    assert manifest_path.is_file()
    assert manifest.fidelity == PROJECTION_ONLY_NOT_BYTE_FAITHFUL
    assert manifest.benchflow_version == "0.7.9.dev0"
    assert manifest.version_evidence["declared_target_version"] == "0.7.9.dev0"
    assert "cli_version" not in manifest.version_evidence
    assert manifest.source_artifact_hash == "sha256:" + "a" * 64
    assert manifest.files[0]["path"] == "parse_dialogue/SKILL.md"
    assert manifest.files[1]["path"] == "parse_dialogue/scripts/parse.py"
    assert manifest.files[1]["sha256"] == hashlib.sha256(copied_path.read_bytes()).hexdigest()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["fidelity"] == PROJECTION_ONLY_NOT_BYTE_FAITHFUL


def test_export_projects_historical_benchflow_version_when_explicit(tmp_path: Path):
    output = tmp_path / "skills_dir"
    manifest = export_released_skill_to_skills_dir(
        _released_skill(),
        output,
        source_artifact_hash="sha256:" + "a" * 64,
        source_snapshot="release-snapshot-1",
        artifact_root=tmp_path / "artifact",
        benchflow_version="0.6.3",
    )

    assert manifest.benchflow_version == "0.6.3"
    assert manifest.version_evidence["declared_target_version"] == "0.6.3"
    assert manifest.version_evidence["conflict"] is False
    assert manifest.license_state == "unresolved_task_local_license_metadata"
    manifest_json = json.loads((output / "skillwiki_skills_dir_manifest.json").read_text(encoding="utf-8"))
    assert manifest_json["benchflow_version"] == "0.6.3"


def test_export_rejects_unknown_benchflow_version(tmp_path: Path):
    with pytest.raises(ValueError, match="unsupported BenchFlow version"):
        export_released_skill_to_skills_dir(
            _released_skill(),
            tmp_path / "skills_dir",
            source_artifact_hash="sha256:" + "a" * 64,
            source_snapshot="release-snapshot-1",
            artifact_root=tmp_path,
            benchflow_version="9.9.9",
        )


def test_export_rejects_nonreleased_skill_and_missing_source_hash(tmp_path: Path):
    skill = _released_skill()
    skill.state = SkillState.VERIFIED
    with pytest.raises(ValueError, match="RELEASED"):
        export_released_skill_to_skills_dir(
            skill,
            tmp_path / "skills_dir",
            source_artifact_hash="sha256:" + "a" * 64,
            source_snapshot="release-snapshot-1",
            artifact_root=tmp_path,
        )

    with pytest.raises(ValueError, match="source artifact hash"):
        export_released_skill_to_skills_dir(
            _released_skill(),
            tmp_path / "skills_dir-2",
            source_artifact_hash="",
            source_snapshot="release-snapshot-1",
            artifact_root=tmp_path,
        )


@pytest.mark.parametrize("bad_path", ["../escape.py", "/absolute.py", "scripts/../escape.py"])
def test_export_rejects_path_traversal(tmp_path: Path, bad_path: str):
    artifact_root = tmp_path / "artifact"
    declared = _declared_file(artifact_root, "safe.py", b"safe\n")
    declared["source"] = bad_path
    with pytest.raises(ValueError, match="path"):
        export_released_skill_to_skills_dir(
            _released_skill(),
            tmp_path / "skills_dir",
            source_artifact_hash="sha256:" + "a" * 64,
            source_snapshot="release-snapshot-1",
            artifact_root=artifact_root,
            declared_files=[declared],
        )


def test_export_rejects_missing_or_wrong_hash(tmp_path: Path):
    artifact_root = tmp_path / "artifact"
    declared = _declared_file(artifact_root, "scripts/parse.py", b"parse\n")
    declared.pop("sha256")
    with pytest.raises(ValueError, match="hash"):
        export_released_skill_to_skills_dir(
            _released_skill(),
            tmp_path / "skills_dir",
            source_artifact_hash="sha256:" + "a" * 64,
            source_snapshot="release-snapshot-1",
            artifact_root=artifact_root,
            declared_files=[declared],
        )

    declared = _declared_file(artifact_root, "scripts/parse2.py", b"parse2\n")
    declared["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        export_released_skill_to_skills_dir(
            _released_skill(),
            tmp_path / "skills_dir-2",
            source_artifact_hash="sha256:" + "a" * 64,
            source_snapshot="release-snapshot-1",
            artifact_root=artifact_root,
            declared_files=[declared],
        )


def test_export_rejects_symlink_source_when_supported(tmp_path: Path):
    artifact_root = tmp_path / "artifact"
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside\n")
    artifact_root.mkdir()
    link = artifact_root / "linked.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    declared = {
        "source": "linked.py",
        "target": "scripts/linked.py",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError, match="symlink"):
        export_released_skill_to_skills_dir(
            _released_skill(),
            tmp_path / "skills_dir",
            source_artifact_hash="sha256:" + "a" * 64,
            source_snapshot="release-snapshot-1",
            artifact_root=artifact_root,
            declared_files=[declared],
        )


def test_export_raw_package_preserves_bytes_and_git_provenance(tmp_path: Path):
    raw = tmp_path / "webapp-testing"
    (raw / "scripts").mkdir(parents=True)
    (raw / "SKILL.md").write_bytes(b"raw skill bytes\n")
    (raw / "LICENSE.txt").write_bytes(b"Apache License, Version 2.0\n")
    (raw / "scripts" / "with_server.py").write_bytes(b"#!/usr/bin/env python3\n")
    repo = "https://github.com/anthropics/skills.git"
    commit = "f" * 40
    entries = []
    for path in sorted(raw.rglob("*")):
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(raw).as_posix(),
                    "mode": stat.S_IMODE(path.stat().st_mode),
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    source_manifest = {
        "repo": repo,
        "commit": commit,
        "skill_path": "skills/webapp-testing",
        "license": {"path": "LICENSE.txt", "scope": "skill", "spdx": "Apache-2.0"},
        "files": entries,
    }

    manifest = export_raw_skill_package_to_skills_dir(
        raw,
        tmp_path / "skills_dir",
        source_repo=repo,
        source_commit=commit,
        source_manifest=source_manifest,
    )

    output = tmp_path / "skills_dir" / "webapp-testing"
    assert manifest.fidelity == BYTE_FAITHFUL
    assert manifest.source_repo == repo
    assert manifest.source_commit == commit
    assert (output / "SKILL.md").read_bytes() == b"raw skill bytes\n"
    assert (output / "LICENSE.txt").read_bytes() == b"Apache License, Version 2.0\n"
    assert manifest.files[0]["sha256"] == hashlib.sha256((output / "LICENSE.txt").read_bytes()).hexdigest()
    assert all(item["symlink"] is False for item in manifest.files)
    assert manifest.license_state == "resolved_task_local_spdx"
    assert manifest.benchflow_version == "0.7.9.dev0"
    assert manifest.version_evidence["conflict"] is True
    assert "cli_version" not in manifest.version_evidence


@pytest.mark.parametrize("license_info", [
    {"path": "LICENSE.txt", "scope": "repository", "spdx": "Apache-2.0"},
    {"path": "LICENSE.txt", "spdx": "Apache-2.0"},
    {"path": "missing.txt", "scope": "task", "spdx": "Apache-2.0"},
])
def test_export_raw_package_does_not_promote_root_or_unverified_license(tmp_path: Path, license_info):
    raw = tmp_path / "skill"
    raw.mkdir()
    (raw / "SKILL.md").write_bytes(b"raw\n")
    (raw / "LICENSE.txt").write_bytes(b"Apache License, Version 2.0\n")
    entries = [{"path": p.name, "mode": stat.S_IMODE(p.stat().st_mode), "size": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(raw.iterdir())]
    source_manifest = {"repo": "https://example.invalid/source", "commit": "a" * 40,
                       "skill_path": "tasks/example/environment/skills/skill", "license": license_info,
                       "files": entries}
    manifest = export_raw_skill_package_to_skills_dir(
        raw, tmp_path / "out", source_repo=source_manifest["repo"],
        source_commit=source_manifest["commit"], source_manifest=source_manifest,
    )
    assert manifest.license_state == "unresolved_task_local_license_metadata"
    assert manifest.provenance["license"] == license_info


def test_export_raw_package_keeps_missing_task_local_spdx_unresolved(tmp_path: Path):
    raw = tmp_path / "task"
    raw.mkdir()
    (raw / "SKILL.md").write_bytes(b"raw\n")
    (raw / "LICENSE").write_bytes(b"Apache License, Version 2.0\n")
    entries = [{"path": p.name, "mode": stat.S_IMODE(p.stat().st_mode), "size": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(raw.iterdir())]
    source_manifest = {"repo": "https://example.invalid/source", "commit": "a" * 40,
                       "skill_path": "tasks/example", "license": {"path": "LICENSE"}, "files": entries,
                       "root_license": {"spdx": "Apache-2.0"}}
    manifest = export_raw_skill_package_to_skills_dir(
        raw, tmp_path / "skills_dir", source_repo=source_manifest["repo"],
        source_commit=source_manifest["commit"], source_manifest=source_manifest,
        benchflow_version="0.7.9.dev0",
    )
    assert manifest.license_state == "unresolved_task_local_license_metadata"
    assert manifest.provenance["license"].get("spdx") is None
    assert manifest.version_evidence["conflict"] is True


def test_export_raw_package_rejects_unlisted_extra_file(tmp_path: Path):
    raw = tmp_path / "webapp-testing"
    raw.mkdir()
    (raw / "SKILL.md").write_bytes(b"raw\n")
    source_manifest = {
        "repo": "https://github.com/anthropics/skills.git",
        "commit": "f" * 40,
        "skill_path": "skills/webapp-testing",
        "license": {"path": "SKILL.md", "spdx": "Apache-2.0"},
        "files": [
            {
                "path": "SKILL.md",
                "mode": stat.S_IMODE((raw / "SKILL.md").stat().st_mode),
                "size": 4,
                "sha256": hashlib.sha256(b"raw\n").hexdigest(),
            }
        ],
    }
    (raw / "undeclared.txt").write_bytes(b"must reject\n")
    with pytest.raises(ValueError, match="path set mismatch"):
        export_raw_skill_package_to_skills_dir(
            raw,
            tmp_path / "skills_dir",
            source_repo=source_manifest["repo"],
            source_commit=source_manifest["commit"],
            source_manifest=source_manifest,
        )
