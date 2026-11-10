"""Read-only mapping of native BenchFlow run evidence.

This module never executes BenchFlow or a verifier. It only reads persisted
JSON/sidecar files and reports what those files actually contain.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping as ABCMapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

from .benchflow_runtime_evidence import (
    FIXED_WHEEL_FILENAME,
    FIXED_WHEEL_SHA256,
    RUNTIME_ATTESTATION_FILENAME,
    RUNTIME_ATTESTATION_KIND,
    RUNTIME_ATTESTATION_SCHEMA,
    SOURCE_RUNTIME_FILENAME,
    SOURCE_RUNTIME_SCHEMA,
    SOURCE_RUNTIME_KIND,
    source_manifest_digest,
    SUPPORTED_BENCHFLOW_VERSIONS,
)


SCHEMA_VERSION = "skillwiki.m3.native-benchflow-evidence.v2"
NATIVE_SCOPE = "native_runtime"
DEVELOPMENT_SCOPE = "development_fixture"
READER_SUPPORTED_VERSION = "0.6.3"
_IDENTITY_FIELDS = ("task_id", "task_digest", "skill_mode", "agent", "model", "sandbox")
_NATIVE_VERIFIER_SIDECAR = "trainer/verifiers.jsonl"
_BLOCKED_ERROR_MARKERS = ("sandbox", "locked path", "environment", "dependency", "not started")


@dataclass(frozen=True)
class BenchFlowEvidence(ABCMapping[str, Any]):
    """Observed evidence record; it is not a public SkillWiki schema."""

    scope: str
    run_dir: str
    status: str
    score: Optional[float]
    observed: Dict[str, Any]
    raw_artifacts: Dict[str, Dict[str, Any]]
    rewards: Any
    error: Optional[str]
    verifier_error: Optional[str]
    unknown_reason: str
    errors: List[str] = field(default_factory=list)
    expected_task_digest: Optional[str] = None
    expected_skill_mode: Optional[str] = None
    is_oracle: bool = False
    model_claimable: bool = False
    cost: Dict[str, Any] = field(default_factory=dict)
    sidecars: List[str] = field(default_factory=list)
    reader_supported_version: str = READER_SUPPORTED_VERSION
    expected_task_id: Optional[str] = None
    expected_runtime_version: Optional[str] = None
    model_result_eligible: bool = False
    sidecar_valid: bool = False
    evidence_completeness: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    runtime_attestation_valid: bool = False

    def _legacy_mapping(self) -> dict[str, Any]:
        """Expose the pre-C08 mapping surface without weakening C08 fields."""

        raw_files: list[dict[str, Any]] = []
        for relative, metadata in self.raw_artifacts.items():
            if relative in {"result.json", "config.json"}:
                kind = relative[:-5]
            elif relative in self.sidecars:
                kind = "verifier_sidecar"
            elif relative in {RUNTIME_ATTESTATION_FILENAME, SOURCE_RUNTIME_FILENAME}:
                kind = "runtime_attestation"
            else:
                continue
            raw_files.append({"kind": kind, "path": relative, **metadata})
        observed = dict(self.observed)
        observed.update(
            {
                "error": self.error,
                "verifier_error": self.verifier_error,
                "rewards": self.rewards,
                "is_oracle": self.is_oracle,
                "fixture_scope": self.scope if self.scope == DEVELOPMENT_SCOPE else None,
            }
        )
        reasons = list(self.errors)
        if self.unknown_reason and self.unknown_reason not in reasons:
            reasons.append(self.unknown_reason)
        return {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "kind": "benchflow_native_rollout",
                "root": self.run_dir,
                "reader_supported_version": self.reader_supported_version,
                "benchflow_version": self.observed.get("benchflow_version"),
                "raw_files": raw_files,
            },
            "expected": {
                "task_digest": self.expected_task_digest,
                "task_id": self.expected_task_id,
                "skill_mode": self.expected_skill_mode,
                "runtime_version": self.expected_runtime_version,
            },
            "observed": observed,
            "result": {
                "status": "pass" if self.status == "passed" else self.status,
                "score": self.score,
                "unknown_reasons": reasons,
                "model_result_eligible": self.model_result_eligible,
            },
            "cost": {
                "usd": self.cost.get("usd"),
                "total_tokens": self.cost.get("total_tokens"),
            },
        }

    def __getitem__(self, key: str) -> Any:
        return self._legacy_mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._legacy_mapping())

    def __len__(self) -> int:
        return len(self._legacy_mapping())


def read_benchflow_evidence(
    run_dir: str | Path,
    *,
    expected_task_digest: str | None = None,
    expected_skill_mode: str | None = None,
    expected_task_id: str | None = None,
    expected_runtime_version: str | None = None,
    scope: str = NATIVE_SCOPE,
) -> BenchFlowEvidence:
    """Read one native rollout directory without inferring missing facts.

    ``expected_*`` values are validation inputs only. They are never copied
    into ``observed`` when the run files omit or contradict them.
    """

    root = Path(run_dir).resolve()
    missing_run = not root.is_dir()
    raw_artifacts = _hash_files(root)
    errors: List[str] = []
    ambiguous_identity_files = _ambiguous_identity_files(raw_artifacts)
    if ambiguous_identity_files:
        errors.append(
            "ambiguous nested result/config files: " + ", ".join(ambiguous_identity_files)
        )
    result, result_error = _read_object(root / "result.json")
    config, config_error = _read_object(root / "config.json")
    if result_error:
        errors.append(result_error)
    if config_error:
        errors.append(config_error)

    result = result or {}
    config = config or {}
    observed, identity_errors = _observed_identity(result, config)
    errors.extend(identity_errors)
    task_source_evidence, task_source_errors = _read_task_source_evidence(
        root, result, config, expected_task_digest=expected_task_digest
    )
    errors.extend(task_source_errors)
    if task_source_evidence is not None:
        observed["task_source_evidence"] = task_source_evidence
    runtime_attestation, runtime_attestation_errors, runtime_attestation_present = (
        _read_runtime_attestation(root, raw_artifacts)
    )
    errors.extend(runtime_attestation_errors)
    runtime_attestation_valid = bool(runtime_attestation and not runtime_attestation_errors)
    if runtime_attestation_valid:
        attested_version = runtime_attestation["benchflow_version"]
        native_version = observed.get("benchflow_version")
        if native_version is not None and native_version != attested_version:
            errors.append("result/config identity conflict with runtime attestation version")
            observed["benchflow_version"] = None
        elif native_version is None:
            observed["benchflow_version"] = attested_version
            observed["benchflow_version_origin"] = "runtime_attestation"
        observed["runtime_attestation"] = dict(runtime_attestation)

    result_scope = result.get("scope")
    config_scope = config.get("scope")
    result_fixture_scope = result.get("fixture_scope")
    config_fixture_scope = config.get("fixture_scope")
    declared_scope = result_scope or config_scope
    if result_scope is not None and config_scope is not None and result_scope != config_scope:
        errors.append("result/config identity conflict for scope")
    if declared_scope and declared_scope != scope:
        errors.append(f"scope mismatch: observed={declared_scope!r}, requested={scope!r}")
    if result_fixture_scope is not None and config_fixture_scope is not None and result_fixture_scope != config_fixture_scope:
        errors.append("result/config identity conflict for fixture_scope")
    fixture_scope = result_fixture_scope or config_fixture_scope
    if fixture_scope == DEVELOPMENT_SCOPE and scope == NATIVE_SCOPE:
        errors.append("development_fixture marker cannot be read as native runtime")

    if expected_task_id is not None and observed.get("task_id") != expected_task_id:
        errors.append(
            f"task_id mismatch: observed={observed.get('task_id')!r}, expected={expected_task_id!r}"
        )
    if expected_task_digest is not None and observed.get("task_digest") != expected_task_digest:
        errors.append(
            f"task_digest mismatch: observed={observed.get('task_digest')!r}, expected={expected_task_digest!r}"
        )
    if expected_skill_mode is not None and observed.get("skill_mode") != expected_skill_mode:
        errors.append(
            f"skill_mode mismatch: observed={observed.get('skill_mode')!r}, expected={expected_skill_mode!r}"
        )

    rewards = result.get("rewards")
    reward = rewards.get("reward") if isinstance(rewards, Mapping) else None
    score = _finite_number(reward)
    error = _optional_text(result.get("error"))
    verifier_error = _optional_text(result.get("verifier_error"))
    is_oracle = str(observed.get("agent") or "").strip().lower() == "oracle"
    model_claimable = (
        scope == NATIVE_SCOPE
        and declared_scope != DEVELOPMENT_SCOPE
        and fixture_scope != DEVELOPMENT_SCOPE
        and not is_oracle
        and _present_text(observed.get("agent"))
        and _present_text(observed.get("model"))
    )
    cost = _cost_envelope(result)
    sidecars = [
        path
        for path in raw_artifacts
        if path not in {"result.json", "config.json"}
        and (path.startswith("verifier/") or path.startswith("trainer/") or path == "timing.json")
    ]
    native_unknowns: List[str] = []
    evidence_completeness = _collect_evidence_completeness(root)
    evidence_unknowns = [
        entry["error"]
        for entry in evidence_completeness.values()
        if entry.get("error")
    ]
    sidecar_valid = scope != NATIVE_SCOPE
    if scope == NATIVE_SCOPE:
        manifest_errors = _validate_native_source_manifest(result, config)
        if task_source_evidence is not None and not task_source_errors:
            missing_only = {
                "result.json source manifest is missing",
                "config.json source manifest is missing",
            }
            manifest_errors = [error for error in manifest_errors if error not in missing_only]
        errors.extend(manifest_errors)
        if not runtime_attestation_present:
            native_unknowns.append("BenchFlow runtime attestation is unverified")
        if not observed.get("benchflow_version"):
            native_unknowns.append("BenchFlow runtime version is unverified")
        elif observed.get("benchflow_version") not in SUPPORTED_BENCHFLOW_VERSIONS:
            native_unknowns.append(
                "unsupported observed BenchFlow runtime version: "
                f"{observed.get('benchflow_version')!r}"
            )
        elif expected_runtime_version is not None and observed.get("benchflow_version") != expected_runtime_version:
            errors.append(
                "benchflow_version mismatch: "
                f"observed={observed.get('benchflow_version')!r}, expected={expected_runtime_version!r}"
            )
        sidecar_errors, sidecar_valid = _validate_native_verifier_sidecar(root, observed, score)
        native_unknowns.extend(sidecar_errors)

    if missing_run:
        status = "unknown"
        unknown_reason = "run directory missing"
    elif errors:
        status = "blocked"
        unknown_reason = "; ".join(errors)
    elif native_unknowns or evidence_unknowns:
        status = "unknown"
        unknown_reason = "; ".join([*native_unknowns, *evidence_unknowns])
    elif verifier_error:
        status = "unknown"
        unknown_reason = f"verifier_error observed: {verifier_error}"
    elif error:
        status = "blocked" if _looks_blocked(result, error) else "unknown"
        unknown_reason = f"runtime error observed: {error}"
    elif score is None:
        status = "unknown"
        unknown_reason = "rewards.reward is missing or not a finite number"
    else:
        status = "passed" if score == 1.0 else "failed"
        unknown_reason = ""

    return BenchFlowEvidence(
        scope=scope,
        run_dir=str(root),
        status=status,
        score=score if status in {"passed", "failed"} else None,
        observed=observed,
        raw_artifacts=raw_artifacts,
        rewards=rewards,
        error=error,
        verifier_error=verifier_error,
        unknown_reason=unknown_reason,
        errors=errors,
        reader_supported_version=READER_SUPPORTED_VERSION,
        expected_task_digest=expected_task_digest,
        expected_skill_mode=expected_skill_mode,
        expected_task_id=expected_task_id,
        expected_runtime_version=expected_runtime_version,
        is_oracle=is_oracle,
        model_claimable=(
            status in {"passed", "failed"}
            and model_claimable
            and sidecar_valid
            and not error
            and not verifier_error
            and not errors
            and runtime_attestation_valid
            and score is not None
        ),
        model_result_eligible=(
            status in {"passed", "failed"}
            and scope == NATIVE_SCOPE
            and sidecar_valid
            and not is_oracle
            and _present_text(observed.get("agent"))
            and _present_text(observed.get("model"))
            and not error
            and not verifier_error
            and not errors
            and runtime_attestation_valid
            and score is not None
        ),
        cost=cost,
        sidecars=sidecars,
        sidecar_valid=sidecar_valid,
        evidence_completeness=evidence_completeness,
        runtime_attestation_valid=runtime_attestation_valid,
    )


def _read_object(path: Path) -> tuple[Dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"missing required evidence file: {path.name}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable {path.name}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{path.name} must contain a JSON object"
    return payload, None


def _hash_files(root: Path) -> Dict[str, Dict[str, Any]]:
    if not root.is_dir():
        return {}
    artifacts: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        artifacts[relative] = {
            "path": str(path),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return artifacts


def _read_runtime_attestation(
    root: Path, raw_artifacts: Mapping[str, Mapping[str, Any]]
) -> tuple[Dict[str, Any] | None, List[str], bool]:
    """Read and bind the unsigned runtime witness to this rollout's bytes."""

    source_path = root / SOURCE_RUNTIME_FILENAME
    wheel_path = root / RUNTIME_ATTESTATION_FILENAME
    if source_path.exists() and wheel_path.exists():
        return None, ["ambiguous source and wheel runtime attestations"], True
    is_source = source_path.exists()
    path = source_path if is_source else wheel_path
    if not path.is_file() or path.is_symlink():
        return None, [], False
    payload, error = _read_object(path)
    if error:
        return None, [f"invalid runtime attestation: {error}"], True
    assert payload is not None
    errors: List[str] = []
    expected_schema = SOURCE_RUNTIME_SCHEMA if is_source else RUNTIME_ATTESTATION_SCHEMA
    expected_kind = SOURCE_RUNTIME_KIND if is_source else RUNTIME_ATTESTATION_KIND
    if payload.get("schema_version") != expected_schema:
        errors.append("runtime attestation schema_version is unsupported")
    if payload.get("kind") != expected_kind:
        errors.append("runtime attestation kind is invalid")
    if payload.get("run_id") != root.name:
        errors.append("runtime attestation run_id does not match rollout directory")
    if payload.get("run_path") != str(root):
        errors.append("runtime attestation run_path does not match rollout directory")
    version = payload.get("benchflow_version")
    if not _present_text(version):
        errors.append("runtime attestation benchflow_version is missing")
    elif version not in SUPPORTED_BENCHFLOW_VERSIONS:
        errors.append(f"runtime attestation has unsupported BenchFlow version: {version!r}")
    for field_name in ("package_name", "package_location", "python_executable", "python_version"):
        if not _present_text(payload.get(field_name)):
            errors.append(f"runtime attestation {field_name} is missing")
    package_location = payload.get("package_location")
    if _present_text(package_location) and not Path(package_location).is_absolute():
        errors.append("runtime attestation package_location must be absolute")
    for field_name, relative in (("result_sha256", "result.json"), ("config_sha256", "config.json")):
        digest = payload.get(field_name)
        expected = raw_artifacts.get(relative, {}).get("sha256")
        if not _is_sha256(digest):
            errors.append(f"runtime attestation {field_name} is invalid")
        elif expected is None or digest != expected:
            errors.append(f"runtime attestation {field_name} does not match {relative}")
        size = payload.get(relative[:-5] + "_size")
        expected_size = raw_artifacts.get(relative, {}).get("size")
        if not isinstance(size, int) or size < 0 or expected_size is None or size != expected_size:
            errors.append(f"runtime attestation {relative} size does not match raw bytes")
    if is_source:
        errors.extend(_validate_source_runtime(payload))
        return (payload if not errors else None), errors, True
    wheel_filename = payload.get("wheel_filename")
    wheel_sha256 = payload.get("wheel_sha256")
    if version == "0.6.3":
        if wheel_sha256 != FIXED_WHEEL_SHA256:
            errors.append("runtime attestation wheel_sha256 does not match the fixed BenchFlow 0.6.3 wheel")
        if wheel_filename != FIXED_WHEEL_FILENAME:
            errors.append("runtime attestation wheel_filename does not match the fixed BenchFlow 0.6.3 wheel")
    elif version in SUPPORTED_BENCHFLOW_VERSIONS:
        if not _is_sha256(wheel_sha256):
            errors.append("runtime attestation wheel_sha256 is missing or invalid for the current BenchFlow version")
        expected_prefix = f"benchflow-{version}"
        if not isinstance(wheel_filename, str) or not wheel_filename.startswith(expected_prefix):
            errors.append("runtime attestation wheel_filename does not identify the observed BenchFlow version")
    return (payload if not errors else None), errors, True


def _validate_source_runtime(payload: Mapping[str, Any]) -> List[str]:
    errors = []
    if payload.get("runtime_kind") != "editable_source_checkout":
        errors.append("source runtime kind is invalid")
    if payload.get("source_dirty") is not False:
        errors.append("source runtime must identify committed sources")
    commit = payload.get("checkout_commit")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        errors.append("source runtime checkout commit is invalid")
    checkout = payload.get("checkout_root")
    imported = payload.get("import_path")
    package = payload.get("package_name")
    if package != "benchflow":
        errors.append("source runtime package must be benchflow")
    if not isinstance(checkout, str) or not Path(checkout).is_absolute():
        errors.append("source runtime checkout_root must be absolute")
    elif not isinstance(imported, str) or not Path(imported).is_absolute() or not Path(imported).is_relative_to(Path(checkout) / "src" / "benchflow"):
        errors.append("source runtime import_path is outside checkout package")
    elif payload.get("package_location") != str(Path(checkout) / "src" / "benchflow"):
        errors.append("source runtime package_location does not match checkout")
    files = payload.get("source_files")
    if not isinstance(files, list) or not files:
        errors.append("source runtime manifest is missing")
        return errors
    names = []
    for item in files:
        if not isinstance(item, dict):
            errors.append("source runtime manifest item is invalid")
            continue
        name = item.get("path")
        size = item.get("size")
        if not isinstance(name, str) or name.startswith("/") or "\\" in name or ".." in PurePosixPath(name).parts or not (name == "pyproject.toml" or name.startswith("src/benchflow/") and name.endswith(".py")):
            errors.append("source runtime manifest path is invalid")
        else:
            names.append(name)
        if not _is_sha256(item.get("sha256")) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append("source runtime manifest hash or size is invalid")
    if names != sorted(set(names)) or "pyproject.toml" not in names or "src/benchflow/__init__.py" not in names:
        errors.append("source runtime manifest is unordered, incomplete or duplicated")
    if payload.get("source_tree_sha256") != source_manifest_digest(files):
        errors.append("source runtime manifest digest does not match")
    return errors


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _ambiguous_identity_files(raw_artifacts: Mapping[str, Mapping[str, Any]]) -> List[str]:
    return sorted(
        path
        for path in raw_artifacts
        if path not in {"result.json", "config.json"}
        and Path(path).name in {"result.json", "config.json"}
    )


def _observed_identity(result: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    result_sandbox = result.get("sandbox")
    config_sandbox = config.get("sandbox", config.get("environment"))
    observed: Dict[str, Any] = {
        "task_name": result.get("task_name"),
        "task_id": result.get("task_id"),
        "rollout_name": result.get("rollout_name"),
        "agent": result.get("agent"),
        "model": result.get("model"),
        "skill_mode": result.get("skill_mode"),
        "skill_source": result.get("skill_source"),
        "sandbox": result_sandbox,
        "task_digest": result.get("task_digest"),
        "benchflow_version": result.get("benchflow_version"),
        "source": result.get("source"),
        "config_task_path": config.get("task_path"),
    }
    errors: List[str] = []
    for field in _IDENTITY_FIELDS:
        result_value = result_sandbox if field == "sandbox" else result.get(field)
        config_value = config_sandbox if field == "sandbox" else config.get(field)
        result_declared = ("sandbox" in result if field == "sandbox" else field in result)
        config_declared = (("sandbox" in config or "environment" in config) if field == "sandbox" else field in config)
        if result_value is not None and config_value is not None and result_value != config_value:
            errors.append(f"result/config identity conflict for {field}")
            observed[field] = None
        elif result_declared and result_value is None and config_declared and config_value is not None:
            errors.append(f"result/config identity conflict for {field}")
            observed[field] = None
        if result_value is None and config_value is not None:
            if not (result_declared and result_value is None):
                observed[field] = config_value
    # 0.6.3 writes task_name, not task_id. Its config writer records either
    # source.path or task_path.name. Map that native alias only after agreement;
    # the verifier sidecar independently cross-checks info.task_id below.
    task_name = result.get("task_name")
    config_path_name = _task_path_name(config.get("task_path"))
    task_identity_conflict = any("conflict for task_id" in error for error in errors)
    for label, payload in (("result", result), ("config", config)):
        if "task_id" in payload and not _present_text(payload["task_id"]):
            errors.append(f"{label} task_id is explicitly null, empty, or not text")
            task_identity_conflict = True
    if task_name is not None and not _present_text(task_name):
        errors.append("result task_name is empty or not text")
        task_identity_conflict = True
    if task_name is not None and observed.get("task_id") is not None and task_name != observed["task_id"]:
        errors.append("result/config identity conflict between task_name and task_id")
        task_identity_conflict = True
    if task_name is not None and "task_id" not in result and not task_identity_conflict:
        if config_path_name != task_name:
            errors.append("native task_name cannot be mapped without matching config task_path")
            task_identity_conflict = True
        else:
            observed["task_id"] = task_name
            observed["task_id_origin"] = "result.task_name+config.task_path"
    if "task_path" in config and observed.get("task_id") is not None and config_path_name != observed["task_id"]:
        errors.append("result/config identity conflict for task_path/task_id")
        task_identity_conflict = True
    if task_identity_conflict:
        observed["task_id"] = None
    result_source = result.get("source")
    config_source = config.get("source")
    if result_source is None and config_source is not None:
        observed["source"] = config_source

    result_manifest, result_manifest_errors = _source_manifest_summary(result)
    config_manifest, config_manifest_errors = _source_manifest_summary(config)
    errors.extend(result_manifest_errors)
    errors.extend(config_manifest_errors)
    observed_manifest: Dict[str, Any] | None = None
    if result_manifest is not None or config_manifest is not None:
        observed_manifest = {}
        for field in ("repo", "resolved_sha", "commit", "dirty", "task_digest"):
            result_value = result_manifest.get(field) if result_manifest else None
            config_value = config_manifest.get(field) if config_manifest else None
            if result_value is not None and config_value is not None and result_value != config_value:
                errors.append(f"result/config source manifest conflict for {field}")
                observed_manifest[field] = None
            else:
                observed_manifest[field] = result_value if result_value is not None else config_value
    observed["source_manifest"] = observed_manifest
    result_version = result.get("benchflow_version")
    config_version = config.get("benchflow_version")
    if result_version is not None and config_version is not None and result_version != config_version:
        errors.append("result/config identity conflict for benchflow_version")
        observed["benchflow_version"] = None
    elif result_version is None and config_version is not None:
        observed["benchflow_version"] = config_version
    if not observed.get("task_digest"):
        errors.append("observed task_digest is missing")
    if not observed.get("task_id"):
        errors.append("observed task_id is missing")
    if not observed.get("skill_mode"):
        errors.append("observed skill_mode is missing")
    return observed, errors


def _task_path_name(value: Any) -> str | None:
    """Read the portable leaf of a recorded path without resolving local files."""

    if not _present_text(value):
        return None
    parts = PurePosixPath(value.replace("\\", "/")).parts
    if not parts or ".." in parts or parts[-1] in {".", "/"}:
        return None
    return parts[-1]


def _source_manifest_summary(payload: Mapping[str, Any]) -> tuple[Dict[str, Any] | None, List[str]]:
    source = payload.get("source")
    if not isinstance(source, Mapping):
        source = payload.get("source_manifest")
    if not isinstance(source, Mapping):
        return None, []

    resolved_sha = source.get("resolved_sha")
    commit = source.get("commit")
    errors: List[str] = []
    if resolved_sha is not None and commit is not None and resolved_sha != commit:
        errors.append("source manifest contains conflicting resolved_sha/commit")
    source_digest = source.get("task_digest")
    payload_digest = payload.get("task_digest")
    if source_digest is not None and payload_digest is not None and source_digest != payload_digest:
        errors.append("source manifest task_digest conflicts with top-level task_digest")
    return {
        "repo": source.get("repo"),
        "resolved_sha": resolved_sha,
        "commit": commit,
        "dirty": source.get("dirty"),
        "task_digest": source_digest if source_digest is not None else payload_digest,
    }, errors


def _validate_native_source_manifest(
    result: Mapping[str, Any], config: Mapping[str, Any]
) -> List[str]:
    errors: List[str] = []
    for label, payload in (("result.json", result), ("config.json", config)):
        summary, summary_errors = _source_manifest_summary(payload)
        errors.extend(f"{label}: {error}" for error in summary_errors)
        if summary is None:
            errors.append(f"{label} source manifest is missing")
            continue
        if not summary.get("repo"):
            errors.append(f"{label} source manifest repo is missing")
        if not (summary.get("resolved_sha") or summary.get("commit")):
            errors.append(f"{label} source manifest resolved_sha/commit is missing")
        if not isinstance(summary.get("dirty"), bool):
            errors.append(f"{label} source manifest dirty is missing or not boolean")
        if not summary.get("task_digest"):
            errors.append(f"{label} source manifest task_digest is missing")
    return errors


def _read_task_source_evidence(
    root: Path,
    result: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    expected_task_digest: str | None,
) -> tuple[Dict[str, Any] | None, List[str]]:
    """Validate separate immutable task provenance when BenchFlow omits it."""
    path = root / "task-source-evidence.json"
    if not path.is_file() or path.is_symlink():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ["task source evidence is unreadable"]
    if not isinstance(payload, dict):
        return None, ["task source evidence must be an object"]
    errors: List[str] = []
    if payload.get("schema") != "skillwiki.m2.task-source-evidence.v1":
        errors.append("task source evidence schema is unsupported")
    task_id = result.get("task_name") or config.get("task_id")
    if payload.get("task_id") != task_id:
        errors.append("task source evidence task_id mismatch")
    digest = result.get("task_digest") or config.get("task_digest")
    if not digest or payload.get("task_digest") != digest:
        errors.append("task source evidence task_digest mismatch")
    if expected_task_digest is not None and digest != expected_task_digest:
        errors.append("task source evidence differs from expected task_digest")
    repo = payload.get("repo")
    commit = payload.get("resolved_sha")
    if not isinstance(repo, str) or not repo.startswith("https://"):
        errors.append("task source evidence repo is invalid")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        errors.append("task source evidence resolved_sha is invalid")
    if payload.get("dirty") is not False:
        errors.append("task source evidence must attest a clean frozen source")
    for name in ("result", "config"):
        target = root / f"{name}.json"
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if payload.get(f"{name}_sha256") != actual:
            errors.append(f"task source evidence {name}.json hash mismatch")
    if errors:
        return None, errors
    return {
        "repo": repo,
        "resolved_sha": commit,
        "dirty": False,
        "task_digest": digest,
        "evidence_file": path.name,
    }, []


def _collect_evidence_completeness(root: Path) -> Dict[str, Dict[str, Any]]:
    candidates = {
        "prompts": _artifact_candidates(root, "prompts"),
        "trajectory": _artifact_candidates(root, "trajectory"),
        "timing": _artifact_candidates(root, "timing"),
    }
    result: Dict[str, Dict[str, Any]] = {}
    for kind, paths in candidates.items():
        entry: Dict[str, Any] = {
            "paths": [path.relative_to(root).as_posix() for path in paths],
            "present": bool(paths),
            "parseable": False,
            "error": None,
        }
        if not paths:
            entry["error"] = f"{kind} evidence is missing"
        else:
            parse_errors = [_parse_evidence_file(path, kind) for path in paths]
            parse_errors = [error for error in parse_errors if error]
            entry["parseable"] = not parse_errors
            if parse_errors:
                entry["error"] = f"{kind} evidence is not parseable: " + "; ".join(parse_errors)
        result[kind] = entry
    return result


def _artifact_candidates(root: Path, kind: str) -> List[Path]:
    if kind == "prompts":
        paths = [path for directory in (root / "prompts",) if directory.is_dir() for path in directory.rglob("*")]
        paths.extend(path for path in (root / "prompt.md", root / "prompt.txt", root / "prompt.json", root / "prompts.json", root / "prompts.jsonl") if path.is_file())
    elif kind == "trajectory":
        paths = [path for directory in (root / "trajectory",) if directory.is_dir() for path in directory.rglob("*")]
        paths.extend(path for path in (root / "trajectory.jsonl", root / "trajectory.json") if path.is_file())
    else:
        paths = [root / "timing.json"] if (root / "timing.json").is_file() else []
        if (root / "timing").is_dir():
            paths.extend((root / "timing").rglob("*"))
    return sorted(path for path in paths if path.is_file() and not path.is_symlink())


def _parse_evidence_file(path: Path, kind: str) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"{path.name}: {exc}"
    if not text.strip():
        return f"{path.name}: empty"
    if kind == "timing" or path.suffix.lower() == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"{path.name}: {exc}"
    elif path.suffix.lower() == ".jsonl":
        for index, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                return f"{path.name} line {index}: {exc}"
    return None


def _validate_native_verifier_sidecar(
    root: Path, observed: Mapping[str, Any], result_score: float | int | None
) -> tuple[List[str], bool]:
    """Validate the native reward sidecar without treating it as a score source."""

    path = root / _NATIVE_VERIFIER_SIDECAR
    if not path.is_file():
        return [f"missing required native verifier sidecar: {_NATIVE_VERIFIER_SIDECAR}"], False
    if not (root / "verifier" / "reward.txt").is_file() and not (root / "verifier" / "reward.json").is_file():
        return ["missing native verifier reward artifact: verifier/reward.txt or verifier/reward.json"], False
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError) as exc:
        return [f"unreadable native verifier sidecar: {exc}"], False
    if not lines:
        return ["native verifier sidecar is empty"], False

    errors: List[str] = []
    for index, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"native verifier sidecar line {index} is invalid JSON: {exc}")
            continue
        if not isinstance(row, Mapping):
            errors.append(f"native verifier sidecar line {index} is not an object")
            continue
        info = row.get("info") if isinstance(row.get("info"), Mapping) else {}
        reward_valid = row.get("reward_valid")
        if reward_valid is None:
            reward_valid = info.get("reward_valid")
        if reward_valid is not True:
            errors.append(f"native verifier sidecar line {index} reward_valid is not true")
        sidecar_task_id = row.get("task_id")
        if sidecar_task_id is not None and info.get("task_id") is not None and sidecar_task_id != info["task_id"]:
            errors.append(f"native verifier sidecar line {index} has conflicting task_id fields")
        if sidecar_task_id is None:
            sidecar_task_id = info.get("task_id")
        observed_task_id = observed.get("task_id") or observed.get("task_name")
        if observed_task_id is None or sidecar_task_id != observed_task_id:
            errors.append(f"native verifier sidecar line {index} task identity mismatch")
        sidecar_reward = row.get("reward")
        if sidecar_reward is None and isinstance(row.get("reward_metadata"), Mapping):
            sidecar_reward = row["reward_metadata"].get("reward")
        if sidecar_reward is None and isinstance(info.get("reward_metadata"), Mapping):
            sidecar_reward = info["reward_metadata"].get("reward")
        sidecar_reward = _finite_number(sidecar_reward)
        if result_score is not None and sidecar_reward is None:
            errors.append(f"native verifier sidecar line {index} reward is missing or non-finite")
        elif result_score is not None and sidecar_reward != result_score:
            errors.append(f"native verifier sidecar line {index} reward mismatch")
    return errors, not errors


def _cost_envelope(result: Mapping[str, Any]) -> Dict[str, Any]:
    agent_result = result.get("agent_result")
    if not isinstance(agent_result, Mapping):
        agent_result = {}
    return {
        "tool_calls": _finite_number(agent_result.get("n_tool_calls")),
        "model_calls": _finite_number(agent_result.get("n_prompts")),
        "input_tokens": _finite_number(agent_result.get("n_input_tokens")),
        "output_tokens": _finite_number(agent_result.get("n_output_tokens")),
        "total_tokens": _finite_number(agent_result.get("total_tokens")),
        "usd": _finite_number(agent_result.get("cost_usd")),
        "usage_source": agent_result.get("usage_source"),
    }


def _finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _present_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _looks_blocked(result: Mapping[str, Any], error: str) -> bool:
    if result.get("sandbox_id") is None:
        return True
    haystack = error.lower()
    return any(marker in haystack for marker in _BLOCKED_ERROR_MARKERS)
