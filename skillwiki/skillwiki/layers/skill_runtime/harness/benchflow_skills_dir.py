"""Deterministic SkillWiki -> BenchFlow ``skills_dir`` materialization.

The adapter supports two explicit paths: projection of a released Skill model
and byte-preserving export of a separately verified raw Skill package. Neither
path executes BenchFlow; the raw path never treats a projection as source
bytes and records Windows/POSIX mode differences separately.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from ....models.skill_model import Skill, SkillState
from .benchflow_runtime_evidence import CURRENT_BENCHFLOW_VERSION, SUPPORTED_BENCHFLOW_VERSIONS


ADAPTER_VERSION = "skillwiki-benchflow-skills-dir-v1"
BENCHFLOW_VERSION = CURRENT_BENCHFLOW_VERSION
CURRENT_CITATION_VERSION = "0.6.9"
BENCHFLOW_SKILLS_DIR_LAYOUT = "<skills_dir>/<safe-name>/SKILL.md"
PROJECTION_ONLY_NOT_BYTE_FAITHFUL = "projection_only_not_byte_faithful"
BYTE_FAITHFUL = "byte_faithful"
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


@dataclass(frozen=True)
class SkillsDirManifest:
    """Audit manifest for one deterministic skills_dir export."""

    adapter_version: str
    benchflow_version: str
    benchflow_layout: str
    output_dir: str
    safe_name: str
    skill_id: str
    skill_version: str
    source_snapshot: str
    source_artifact_hash: str
    provenance: dict[str, Any]
    fidelity: str
    files: list[dict[str, Any]]
    manifest_path: str
    source_repo: str | None = None
    source_commit: str | None = None
    source_manifest_hash: str | None = None
    raw_package_root: str | None = None
    version_evidence: dict[str, Any] | None = None
    license_state: str = "unresolved_task_local_license_metadata"

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_version": self.adapter_version,
            "benchflow_version": self.benchflow_version,
            "benchflow_layout": self.benchflow_layout,
            "output_dir": self.output_dir,
            "safe_name": self.safe_name,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "source_snapshot": self.source_snapshot,
            "source_artifact_hash": self.source_artifact_hash,
            "provenance": self.provenance,
            "fidelity": self.fidelity,
            "files": self.files,
            "manifest_path": self.manifest_path,
            "source_repo": self.source_repo,
            "source_commit": self.source_commit,
            "source_manifest_hash": self.source_manifest_hash,
            "raw_package_root": self.raw_package_root,
            "version_evidence": self.version_evidence,
            "license_state": self.license_state,
        }


def export_released_skill_to_skills_dir(
    skill: Skill,
    skills_dir: str | Path,
    *,
    source_artifact_hash: str,
    source_snapshot: str,
    artifact_root: str | Path,
    declared_files: Sequence[Mapping[str, Any]] = (),
    benchflow_version: str = BENCHFLOW_VERSION,
    source_version_evidence: Mapping[str, Any] | None = None,
) -> SkillsDirManifest:
    """Materialize one released Skill and explicitly declared artifacts.

    ``declared_files`` entries contain ``source``, ``target``, ``sha256`` and
    optional ``mode``. No directory scan or implicit artifact copy occurs.
    """

    if skill.state != SkillState.RELEASED:
        raise ValueError("only RELEASED Skills may be exported")
    if skill.provenance is None:
        raise ValueError("released Skill provenance is required")
    normalized_source_hash = _normalize_hash(source_artifact_hash, "source artifact hash")
    source_snapshot = str(source_snapshot or "").strip()
    if not source_snapshot:
        raise ValueError("source snapshot is required")
    benchflow_version = _validate_benchflow_version(benchflow_version)

    safe_name = _safe_name(skill.name)
    output_root = Path(skills_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    skill_dir = output_root / safe_name
    if skill_dir.exists():
        if not skill_dir.is_dir() or any(skill_dir.iterdir()):
            raise ValueError(f"output skill directory is not empty: {skill_dir}")
    else:
        skill_dir.mkdir()

    skill_path = skill_dir / "SKILL.md"
    skill_bytes = _projection_bytes(skill, normalized_source_hash, source_snapshot)
    skill_path.write_bytes(skill_bytes)
    os.chmod(skill_path, 0o644)
    files = [_file_record(skill_path, output_root, mode=0o644)]

    root = Path(artifact_root).resolve()
    seen_targets: set[str] = {f"{safe_name}/SKILL.md"}
    for entry in declared_files:
        source_rel = _safe_relative(entry.get("source"), "source")
        target_rel = _safe_relative(entry.get("target", entry.get("source")), "target")
        target_path = skill_dir / Path(*PurePosixPath(target_rel).parts)
        package_rel = (PurePosixPath(safe_name) / PurePosixPath(target_rel)).as_posix()
        if package_rel in seen_targets:
            raise ValueError(f"duplicate target path: {package_rel}")
        if target_rel == "SKILL.md":
            raise ValueError("declared artifact cannot overwrite SKILL.md")
        seen_targets.add(package_rel)

        source_path = root.joinpath(*PurePosixPath(source_rel).parts)
        _ensure_inside(root, source_path, "source")
        _reject_symlink_components(root, source_path)
        if not source_path.is_file():
            raise ValueError(f"declared source is not a regular file: {source_rel}")
        declared_hash = _normalize_hash(entry.get("sha256"), f"hash for {source_rel}")
        actual_hash = _sha256(source_path.read_bytes())
        if declared_hash != f"sha256:{actual_hash}":
            raise ValueError(f"hash mismatch for {source_rel}")
        actual_mode = stat.S_IMODE(source_path.stat().st_mode)
        declared_mode = entry.get("mode", actual_mode)
        try:
            declared_mode = int(declared_mode)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid mode for {source_rel}") from exc
        if declared_mode != actual_mode:
            raise ValueError(f"mode mismatch for {source_rel}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())
        os.chmod(target_path, declared_mode)
        files.append(_file_record(target_path, output_root, mode=declared_mode))

    files.sort(key=lambda item: item["path"])
    manifest_path = output_root / "skillwiki_skills_dir_manifest.json"
    manifest = SkillsDirManifest(
        adapter_version=ADAPTER_VERSION,
        benchflow_version=benchflow_version,
        benchflow_layout=BENCHFLOW_SKILLS_DIR_LAYOUT,
        output_dir=str(output_root),
        safe_name=safe_name,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        source_snapshot=source_snapshot,
        source_artifact_hash=normalized_source_hash,
        provenance=skill.provenance.model_dump(mode="json"),
        fidelity=PROJECTION_ONLY_NOT_BYTE_FAITHFUL,
        files=files,
        manifest_path=str(manifest_path),
        version_evidence=_version_evidence(benchflow_version, source_version_evidence),
        license_state="unresolved_task_local_license_metadata",
    )
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def export_raw_skill_package_to_skills_dir(
    raw_skill_dir: str | Path,
    skills_dir: str | Path,
    *,
    source_repo: str,
    source_commit: str,
    source_manifest: Mapping[str, Any],
    safe_name: str | None = None,
    benchflow_version: str = BENCHFLOW_VERSION,
    source_version_evidence: Mapping[str, Any] | None = None,
) -> SkillsDirManifest:
    """Copy a verified raw Skill package without changing its file bytes.

    The source manifest is authoritative for Git modes and must enumerate every
    regular file. The adapter still re-hashes and size-checks each local file,
    rejects symlinks and extra files, and records observed filesystem mode
    separately because Windows may not expose Git executable bits.
    """

    root = Path(raw_skill_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"raw Skill package directory is missing: {raw_skill_dir}")
    source_repo = str(source_repo or "").strip()
    source_commit = str(source_commit or "").strip()
    benchflow_version = _validate_benchflow_version(benchflow_version)
    if not source_repo or not re.fullmatch(r"[0-9a-fA-F]{40}", source_commit):
        raise ValueError("raw package source repo and commit are required")
    if source_manifest.get("repo") != source_repo or source_manifest.get("commit") != source_commit:
        raise ValueError("raw package source manifest identity mismatch")
    skill_path = str(source_manifest.get("skill_path") or "").strip()
    if not skill_path:
        raise ValueError("raw package source manifest skill_path is required")
    license_info = source_manifest.get("license")
    if not isinstance(license_info, Mapping) or not license_info.get("path"):
        raise ValueError("raw package license provenance is required")
    entries = source_manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("raw package source manifest files are required")

    normalized_entries: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("raw package manifest file entry must be an object")
        relative = _safe_relative(entry.get("path"), "raw package")
        if relative in normalized_entries:
            raise ValueError(f"duplicate raw package path: {relative}")
        if not entry.get("sha256") or entry.get("size") is None or entry.get("mode") is None:
            raise ValueError(f"raw package manifest lacks hash/size/mode: {relative}")
        normalized_entries[relative] = entry

    actual_paths = _enumerate_raw_package_files(root)
    if actual_paths != set(normalized_entries):
        missing = sorted(set(normalized_entries) - actual_paths)
        extra = sorted(actual_paths - set(normalized_entries))
        raise ValueError(f"raw package path set mismatch: missing={missing}, extra={extra}")

    source_manifest_hash = _manifest_hash(source_manifest)
    output_root = Path(skills_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    selected_name = _safe_name(safe_name or root.name)
    skill_dir = output_root / selected_name
    if skill_dir.exists():
        if not skill_dir.is_dir() or any(skill_dir.iterdir()):
            raise ValueError(f"output skill directory is not empty: {skill_dir}")
    else:
        skill_dir.mkdir()

    files: list[dict[str, Any]] = []
    for relative in sorted(normalized_entries):
        entry = normalized_entries[relative]
        source_path = root.joinpath(*PurePosixPath(relative).parts)
        _reject_symlink_components(root, source_path)
        data = source_path.read_bytes()
        actual_hash = _sha256(data)
        expected_hash = _normalize_hash(entry.get("sha256"), f"hash for {relative}")
        if expected_hash != f"sha256:{actual_hash}":
            raise ValueError(f"hash mismatch for raw package file {relative}")
        if int(entry["size"]) != len(data):
            raise ValueError(f"size mismatch for raw package file {relative}")
        expected_mode = _mode_permissions(entry["mode"])
        target = skill_dir.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, expected_mode)
        observed_mode = stat.S_IMODE(target.stat().st_mode)
        files.append(
            {
                "path": (PurePosixPath(selected_name) / PurePosixPath(relative)).as_posix(),
                "mode": expected_mode,
                "mode_observed": observed_mode,
                "mode_preserved": observed_mode == expected_mode,
                "size": len(data),
                "sha256": actual_hash,
                "symlink": False,
            }
        )

    manifest_path = output_root / "skillwiki_skills_dir_manifest.json"
    manifest = SkillsDirManifest(
        adapter_version=ADAPTER_VERSION,
        benchflow_version=benchflow_version,
        benchflow_layout=BENCHFLOW_SKILLS_DIR_LAYOUT,
        output_dir=str(output_root),
        safe_name=selected_name,
        skill_id=str(source_manifest.get("skill_id") or selected_name),
        skill_version=str(source_manifest.get("skill_version") or "raw"),
        source_snapshot=source_commit,
        source_artifact_hash=f"sha256:{source_manifest_hash}",
        provenance={
            "repo": source_repo,
            "commit": source_commit,
            "skill_path": skill_path,
            "license": dict(license_info),
        },
        fidelity=BYTE_FAITHFUL,
        files=files,
        manifest_path=str(manifest_path),
        source_repo=source_repo,
        source_commit=source_commit,
        source_manifest_hash=f"sha256:{source_manifest_hash}",
        raw_package_root=str(root),
        version_evidence=_version_evidence(benchflow_version, source_version_evidence),
        license_state=_license_state(license_info, normalized_entries),
    )
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _safe_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text or ".." in text:
        raise ValueError("unsafe skill safe-name")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    if not safe:
        raise ValueError("unsafe skill safe-name")
    return safe


def _validate_benchflow_version(value: Any) -> str:
    version = str(value or "").strip()
    if version not in SUPPORTED_BENCHFLOW_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_BENCHFLOW_VERSIONS))
        raise ValueError(f"unsupported BenchFlow version {version!r}; expected one of: {supported}")
    return version


def _version_evidence(version: str, supplied: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Keep conflicting upstream metadata visible without changing identity."""

    if supplied is not None:
        return dict(supplied)
    if version == "0.7.9.dev0":
        return {
            "declared_target_version": version,
            "citation_cff_version": CURRENT_CITATION_VERSION,
            "pyproject_version": version,
            "conflict": True,
            "resolved_identity": "pyproject",
            "source_metadata_basis": "frozen_adapter_metadata_not_remeasured_by_exporter",
            "decision": "do_not_use_citation_cff_version_as_current_package_identity",
        }
    return {"declared_target_version": version, "resolved_identity": "explicit_legacy_target", "conflict": False}


def _license_state(license_info: Mapping[str, Any], files: Mapping[str, Any]) -> str:
    """Task-local SPDX is independent of any repository-root license."""

    spdx = license_info.get("spdx")
    path = license_info.get("path")
    if (isinstance(spdx, str) and spdx.strip()
            and license_info.get("scope") in {"skill", "task"}
            and isinstance(path, str) and path in files):
        return "resolved_task_local_spdx"
    return "unresolved_task_local_license_metadata"


def _enumerate_raw_package_files(root: Path) -> set[str]:
    paths: set[str] = set()
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            if (current_path / directory).is_symlink():
                raise ValueError(f"symlink directory is not allowed: {current_path / directory}")
        for filename in files:
            path = current_path / filename
            if path.is_symlink():
                raise ValueError(f"symlink file is not allowed: {path}")
            paths.add(path.relative_to(root).as_posix())
    return paths


def _mode_permissions(value: Any) -> int:
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"100[0-7]{3}", text):
            return int(text[-3:], 8)
        if re.fullmatch(r"[0-7]{3,4}", text):
            return int(text[-3:], 8)
    try:
        mode = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid raw package mode: {value!r}") from exc
    return mode & 0o777


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(canonical.encode("utf-8"))


def _safe_relative(value: Any, label: str) -> str:
    text = str(value or "").replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ":" in text or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe {label} path: {value!r}")
    return path.as_posix()


def _ensure_inside(root: Path, candidate: Path, label: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes artifact root") from exc


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    current = root
    for component in candidate.relative_to(root).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"symlink source is not allowed: {candidate}")


def _normalize_hash(value: Any, label: str) -> str:
    match = _SHA256_RE.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError(f"{label} must be a sha256 hash")
    return f"sha256:{match.group(1).lower()}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_record(path: Path, output_root: Path, *, mode: int) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(output_root).as_posix(),
        "mode": mode,
        "size": len(data),
        "sha256": _sha256(data),
        "symlink": False,
    }


def _projection_bytes(skill: Skill, source_hash: str, source_snapshot: str) -> bytes:
    implementation = skill.implementation.model_dump(mode="json") if skill.implementation else None
    projection = {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "type": skill.skill_type.value,
        "source_artifact_hash": source_hash,
        "source_snapshot": source_snapshot,
        "interface": skill.interface.model_dump(mode="json"),
        "implementation": implementation,
        "provenance": skill.provenance.model_dump(mode="json") if skill.provenance else None,
    }
    text = (
        f"# {skill.name}\n\n"
        f"{skill.description}\n\n"
        "## SkillWiki projection\n\n"
        "```json\n"
        f"{json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2)}\n"
        "```\n"
    )
    return text.encode("utf-8")
