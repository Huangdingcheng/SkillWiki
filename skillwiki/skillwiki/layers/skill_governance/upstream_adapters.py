"""Small, provenance preserving seams for optional upstream components.

The adapters deliberately stop at an explicit ``not_available`` or
``interface_only`` result when an upstream package is not installed.  They do
not provide fallback implementations or turn an import/fixture check into a
successful component run.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import subprocess
import re
import stat
from typing import Any, Mapping


@dataclass(frozen=True)
class UpstreamComponent:
    name: str
    repository: str | None
    commit: str | None
    version: str | None
    license: str | None
    entrypoint: str | None
    disposition: str


@dataclass(frozen=True)
class AdapterProbe:
    component: str
    status: str
    imported: bool
    entrypoint: str | None
    reason: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContentTransferArtifact:
    """A provenance-preserving handoff from reference content to a future run.

    This records an input identity and an explicit not-yet-executed state.  It
    deliberately does not copy, execute, or approve a candidate skill.
    """

    artifact_id: str
    reference_pack_path: str
    reference_pack_sha256: str
    ledger_path: str
    ledger_sha256: str
    candidate_skill_path: str | None
    candidate_skill_sha256: str | None
    source_commit: str
    task_digest: str
    upstream_component: str
    license_state: str
    transfer_status: str
    successfully_run: bool
    fairly_compared: bool
    paper_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OfficialValidatorResult:
    """Raw result from an external upstream validator.

    The adapter records the command and output but does not reproduce the
    validator's checks.  A zero exit code without the validator's explicit
    success marker remains ``failed``.
    """

    component: str
    status: str
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    checked_files: int | None
    all_hashes_verified: bool
    skipped_files: int | None = None
    unlisted_files: int | None = None
    errors: int | None = None
    termination_confirmed: bool | None = None
    cwd: str | None = None
    environment: Mapping[str, str] | None = None
    checked_files_hash: str | None = None
    evidence_path: str | None = None
    source_identity: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceBoundValidatorReceipt:
    """Persistable, source-bound record for one official validator call.

    ``integrated`` means only that this SkillWiki adapter accepted and stored
    the upstream receipt.  It never means task quality, model improvement,
    fair comparison, or paper eligibility.
    """

    schema_version: str
    source_identity: Mapping[str, Any]
    validator: Mapping[str, Any]
    evidence_path: str
    integrated: bool
    promotion_allowed: bool
    successfully_run: bool
    fairly_compared: bool
    paper_eligible: bool
    explicit_non_claims: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["explicit_non_claims"] = list(self.explicit_non_claims)
        return result


def persist_source_bound_validator_receipt(
    *,
    result: OfficialValidatorResult | Mapping[str, Any],
    source_identity: Mapping[str, Any],
    evidence_path: str | Path,
) -> SourceBoundValidatorReceipt:
    """Validate and persist an upstream receipt without promoting a skill.

    The source identity and raw command evidence are mandatory.  Unknown,
    blocked, timeout, tampered, and missing-manifest states are retained as
    distinct states and are never promotion-eligible.
    """
    payload = result.to_dict() if isinstance(result, OfficialValidatorResult) else dict(result)
    identity = dict(source_identity)
    if not any(identity.get(key) for key in ("repository", "source", "repo")):
        raise ValueError("source_identity requires repository, source, or repo")
    if not any(identity.get(key) for key in ("commit", "version")):
        raise ValueError("source_identity requires commit or version")
    if not identity.get("license"):
        raise ValueError("source_identity requires license")
    for key in ("command", "cwd", "stdout", "stderr", "exit_code", "checked_files", "all_hashes_verified"):
        if key not in payload:
            raise ValueError(f"validator receipt missing {key}")
    status = str(payload.get("status", "unknown"))
    allowed = {"success", "tampered", "missing_manifest", "blocked_environment", "timeout", "failed", "unknown"}
    if status not in allowed:
        status = "unknown"
    out = str(payload.get("stdout", ""))
    err = str(payload.get("stderr", ""))
    text = (out + "\n" + err).lower()
    if status in {"failed", "unknown"}:
        if "manifest" in text and any(token in text for token in ("missing", "not found", "no such")):
            status = "missing_manifest"
        elif ("hash mismatch" in text or "tamper" in text
              or "hashes do not match" in text
              or re.search(r"(?im)^\s*MISMATCH\s+\S+", text)):
            status = "tampered"
    receipt_path = Path(evidence_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    payload["status"] = status
    payload["source_identity"] = identity
    payload["evidence_path"] = str(receipt_path)
    promotion_allowed = status == "success" and payload.get("all_hashes_verified") is True
    receipt = SourceBoundValidatorReceipt(
        schema_version="skillwiki.source_bound_validator_receipt.v1",
        source_identity=identity,
        validator=payload,
        evidence_path=str(receipt_path),
        integrated=True,
        promotion_allowed=promotion_allowed,
        successfully_run=status == "success",
        fairly_compared=False,
        paper_eligible=False,
        explicit_non_claims=(
            "Integrated means only that the SkillWiki adapter stored the validator receipt.",
            "Validator success is not task quality, model improvement, or a fair comparison.",
            "No paper eligibility is implied by provenance validation.",
        ),
    )
    receipt_path.write_text(json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


@dataclass(frozen=True)
class ReferenceRunReceipt:
    """Structured boundary receipt for an upstream reference component.

    Each stage stays separate so validator, adapter, product integration, and
    task evidence cannot be mistaken for one another.  The receipt records
    evidence; it does not execute or promote a component.
    """

    source_identity: Mapping[str, Any]
    upstream_validator_result: Mapping[str, Any]
    adapter_materialization: Mapping[str, Any]
    skillwiki_integration: Mapping[str, Any]
    task_execution: Mapping[str, Any]
    fair_comparison: Mapping[str, Any]
    paper_eligibility: Mapping[str, Any]
    explicit_non_claims: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["explicit_non_claims"] = list(self.explicit_non_claims)
        return result


def build_reference_run_receipt(
    *,
    source_identity: Mapping[str, Any],
    upstream_validator_result: Mapping[str, Any] | OfficialValidatorResult,
    adapter_materialization: Mapping[str, Any],
    skillwiki_integration: Mapping[str, Any],
    task_execution: Mapping[str, Any],
    fair_comparison: Mapping[str, Any],
    paper_eligibility: Mapping[str, Any],
    explicit_non_claims: tuple[str, ...] = (),
) -> ReferenceRunReceipt:
    """Build a receipt while enforcing the promotion boundary.

    This helper accepts already-collected evidence only.  A failed, timed out,
    or blocked upstream validator may never be represented as a promoted
    SkillWiki integration.  Task and comparison stages remain explicit even
    when they are ``not_run``.
    """
    validator = (upstream_validator_result.to_dict()
                 if isinstance(upstream_validator_result, OfficialValidatorResult)
                 else dict(upstream_validator_result))
    identity = dict(source_identity)
    if not any(identity.get(key) for key in ("repository", "source", "repo")):
        raise ValueError("source_identity requires repository, source, or repo")
    if not any(identity.get(key) for key in ("commit", "version")):
        raise ValueError("source_identity requires commit or version")
    if not identity.get("license"):
        raise ValueError("source_identity requires license")
    missing_evidence = [key for key in ("command", "stdout", "stderr", "exit_code")
                        if key not in validator]
    if missing_evidence:
        raise ValueError("upstream_validator_result missing raw evidence: "
                         + ", ".join(missing_evidence))
    command = validator["command"]
    if (not isinstance(command, (list, tuple)) or not command
            or any(not isinstance(arg, str) or not arg for arg in command)):
        raise ValueError("upstream_validator_result command must be a nonempty argv sequence")
    for field in ("stdout", "stderr"):
        if not isinstance(validator[field], str):
            raise ValueError(f"upstream_validator_result {field} must be a string")
    if type(validator["exit_code"]) is not int:
        raise ValueError("upstream_validator_result exit_code must be an integer")
    integration = dict(skillwiki_integration)
    materialization = dict(adapter_materialization)
    aliases = [integration[key] for key in ("promoted", "integrated") if key in integration]
    if any(type(value) is not bool for value in aliases):
        raise ValueError("skillwiki_integration promotion aliases must be booleans")
    if len(aliases) == 2 and aliases[0] != aliases[1]:
        raise ValueError("skillwiki_integration promoted and integrated conflict")
    validator_status = str(validator.get("status", ""))
    derived_text = materialization.get("status") == "derived_text"
    upstream_integrated = materialization.get("upstream_package_integrated", True)
    if derived_text or upstream_integrated is False:
        promoted = False
    else:
        promoted = aliases[0] if aliases else False
    integration["promoted"] = promoted
    integration["integrated"] = promoted
    if validator_status != "success" and promoted:
        raise ValueError("failed or blocked upstream validation cannot be promoted")
    task_status = task_execution.get("status")
    compared = fair_comparison.get("compared") is True
    eligibility = dict(paper_eligibility)
    if task_status != "completed" or not compared:
        eligibility["eligible"] = False
    if not explicit_non_claims:
        explicit_non_claims = (
            "No task-quality or model-improvement claim is made.",
            "Adapter materialization is not upstream package integration.",
            "No fair comparison or paper eligibility is implied.",
        )
    return ReferenceRunReceipt(
        source_identity=identity,
        upstream_validator_result=validator,
        adapter_materialization=materialization,
        skillwiki_integration=integration,
        task_execution=dict(task_execution),
        fair_comparison=dict(fair_comparison),
        paper_eligibility=eligibility,
        explicit_non_claims=tuple(explicit_non_claims),
    )


def materialize_source_bound_bundle(
    *,
    source_dir: str | Path,
    output_dir: str | Path,
    source_files: tuple[str, ...],
    source_repository: str,
    source_commit: str,
    task_id: str,
    task_digest: str,
    upstream_validator: str,
) -> dict[str, Any]:
    """Create a derived manifest bundle without changing source file bytes.

    This is an interface adapter for provenance tools.  The output directory
    must be new, and the caller must run the upstream validator separately.
    """
    source = Path(source_dir).resolve()
    output = Path(output_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {source}")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing bundle: {output}")
    if output.resolve().is_relative_to(source):
        raise ValueError("output_dir must be outside source_dir")
    if not source_files:
        raise ValueError("source_files must not be empty")
    # The upstream manifest uses a constrained, unquoted path grammar. Admit
    # a portable subset, checking raw strings before Path normalizes them.
    safe_files = []
    seen = {"manifest.yaml", "source_identity.json"}
    for item in source_files:
        if (not isinstance(item, str)
                or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. /-]*", item)
                or PureWindowsPath(item).anchor
                or any(part in {"", ".", ".."} or part != part.strip()
                       or part.endswith(".") for part in item.split("/"))):
            raise ValueError("source_files must use normalized relative portable paths")
        relative = PurePosixPath(item)
        if item.casefold() in seen:
            raise ValueError(f"duplicate or reserved source path: {item}")
        seen.add(item.casefold())
        current = source
        for part in relative.parts:
            current = current / part
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
            if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError(f"source path may not contain links: {item}")
        if not current.resolve().is_relative_to(source):
            raise ValueError(f"source path escapes source_dir: {item}")
        if not current.is_file():
            raise FileNotFoundError(f"source file does not exist: {current}")
        safe_files.append(Path(*relative.parts))
    identity = {
        "source_repository": source_repository,
        "source_commit": source_commit,
        "task_id": task_id,
        "task_digest": task_digest,
        "skillwiki_transfer": "manifest_bridge_only",
        "upstream_validator": upstream_validator,
    }
    if any(not isinstance(value, str) or not value.strip()
           or any(ord(char) < 32 for char in value) for value in identity.values()):
        raise ValueError("identity fields must be nonempty single-line strings")
    output.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, str]] = []
    for relative in safe_files:
        original = source / relative
        if not original.is_file():
            raise FileNotFoundError(f"source file does not exist: {original}")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        entries.append({"path": relative.as_posix(), "hash": _sha256_file(target)})
    (output / "source_identity.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    entries.append({"path": "source_identity.json", "hash": _sha256_file(output / "source_identity.json")})
    manifest_lines = [
        "bundle: skillwiki-derived",
        "bundle_version: 0.1.0-skillwiki-transfer",
        "description: Source-bound test bundle; source bytes are copied unchanged.",
        f"source_repository: {json.dumps(source_repository)}",
        f"source_commit: {json.dumps(source_commit)}",
        f"task_id: {json.dumps(task_id)}",
        "files:",
    ]
    for entry in entries:
        manifest_lines.extend([f"  - path: {entry['path']}", f"    hash: {entry['hash']}"])
    (output / "MANIFEST.yaml").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8", newline="\n")
    preserved = all((source / relative).read_bytes() == (output / relative).read_bytes() for relative in safe_files)
    return {
        "source_dir": str(source),
        "output_dir": str(output),
        "source_files": [item.as_posix() for item in safe_files],
        "source_bytes_preserved": preserved,
        "manifest_present": (output / "MANIFEST.yaml").is_file(),
        "identity_present": (output / "source_identity.json").is_file(),
        "state": "materialized_unvalidated",
    }


def run_skill_provenance_validator(
    *,
    validator_script: str | Path,
    bundle_dir: str | Path,
    runner: tuple[str, ...] = ("bash",),
    timeout_seconds: float = 60,
) -> OfficialValidatorResult:
    """Run the official Skill Provenance validator and preserve its semantics."""
    if not runner or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("runner and positive timeout_seconds are required")
    if any(not (PurePosixPath(str(item)).is_absolute()
                or PureWindowsPath(str(item)).is_absolute())
           for item in (validator_script, bundle_dir)):
        raise ValueError("validator and bundle paths must be explicit absolute paths")
    command = tuple(str(item) for item in runner) + (str(validator_script), str(bundle_dir))
    cwd = str(Path(validator_script).resolve().parent)
    environment = {key: value for key, value in __import__("os").environ.items()
                   if key in {"PATH", "PYTHONPATH", "LANG", "LC_ALL"}}
    try:
        completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                                   capture_output=True, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        def decoded(value: bytes | str | None) -> str:
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        return OfficialValidatorResult("skill-provenance", "timeout", command, None,
                                       decoded(exc.stdout), decoded(exc.stderr), None, False,
                                       termination_confirmed=False, cwd=cwd,
                                       environment=environment)
    except OSError as exc:
        return OfficialValidatorResult(
            component="skill-provenance",
            status="blocked_environment",
            command=command,
            exit_code=None,
            stdout="",
            stderr=str(exc),
            checked_files=None,
            all_hashes_verified=False,
            cwd=cwd,
            environment=environment,
        )
    summaries = re.findall(r"^Checked (\d+) files, skipped (\d+), unlisted (\d+), errors (\d+)\r?$",
                           completed.stdout, re.MULTILINE)
    counts = tuple(map(int, summaries[0])) if len(summaries) == 1 else (None,) * 4
    all_hashes_verified = (counts[0] is not None and counts[0] > 0
                          and counts[1:] == (0, 0, 0)
                          and "All hashes verified." in completed.stdout.splitlines())
    combined = (completed.stdout + "\n" + completed.stderr).lower()
    if completed.returncode == 0 and all_hashes_verified:
        status = "success"
    elif "manifest" in combined and any(token in combined for token in ("missing", "not found", "no such")):
        status = "missing_manifest"
    elif ("hash mismatch" in combined or "tamper" in combined
          or "hashes do not match" in combined
          or re.search(r"(?im)^\s*MISMATCH\s+\S+", combined)):
        status = "tampered"
    else:
        status = "failed"
    return OfficialValidatorResult(
        component="skill-provenance",
        status=status,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        checked_files=counts[0],
        all_hashes_verified=all_hashes_verified,
        skipped_files=counts[1],
        unlisted_files=counts[2],
        errors=counts[3],
        termination_confirmed=True,
        cwd=cwd,
        environment=environment,
    )


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def build_content_transfer_artifact(
    *,
    reference_pack_path: str,
    ledger_path: str,
    source_commit: str,
    task_digest: str,
    upstream_component: str,
    license_state: str,
    candidate_skill_path: str | None = None,
) -> ContentTransferArtifact:
    """Build an identity-only handoff manifest for the next execution stage."""
    reference_hash = _sha256_file(reference_pack_path)
    ledger_hash = _sha256_file(ledger_path)
    candidate_hash = _sha256_file(candidate_skill_path) if candidate_skill_path else None
    artifact_id = hashlib.sha256(
        (reference_hash + ledger_hash + source_commit + task_digest + upstream_component).encode("utf-8")
    ).hexdigest()[:16]
    return ContentTransferArtifact(
        artifact_id=f"content-transfer-{artifact_id}",
        reference_pack_path=str(Path(reference_pack_path)),
        reference_pack_sha256=reference_hash,
        ledger_path=str(Path(ledger_path)),
        ledger_sha256=ledger_hash,
        candidate_skill_path=str(Path(candidate_skill_path)) if candidate_skill_path else None,
        candidate_skill_sha256=candidate_hash,
        source_commit=source_commit,
        task_digest=task_digest,
        upstream_component=upstream_component,
        license_state=license_state,
        transfer_status="prepared_unexecuted",
        successfully_run=False,
        fairly_compared=False,
        paper_eligible=False,
    )


FIRST_BATCH_COMPONENTS: Mapping[str, UpstreamComponent] = {
    "skillnet-fabric": UpstreamComponent(
        "skillnet-fabric", "zjunlp/SkillNet-Fabric", "e0bc52856e8244ef6eecc0fe8c1b5f481b4190a3",
        "0.2.0", "MIT", "skillfabric:SkillFabric", "experimental_control"
    ),
    "gepa": UpstreamComponent(
        "gepa", "gepa-ai/gepa", "d771eb21b5dd3228bc3f567293d2ccfc423fc900",
        "0.1.4", "MIT", "gepa.optimize", "experimental_control"
    ),
    "skillopt": UpstreamComponent(
        "skillopt", "microsoft/SkillOpt", "79124b37e9a6371e13b753f8bcd7adb1e493ade1",
        "0.2.0", "MIT; exact pinned LICENSE retrieved and hash-recorded; task adapter/runtime remain unverified", "skillopt-train", "experimental_control"
    ),
    "arex-skill-refresh": UpstreamComponent(
        "arex-skill-refresh", "VectorSpaceLab/AREX-Skill", "ac3fe1afa80fb9a09775ecfb2b6cc3ba850a2db6",
        None, "Apache-2.0 (API metadata only)", "refresh manifest/events (event schema only)", "conditional_ingestion"
    ),
    "skillguard": UpstreamComponent(
        "skillguard", None, None, "arXiv:2605.10990v1", None, None, "experimental_control"
    ),
}


def probe_component(name: str) -> AdapterProbe:
    """Probe the declared official entrypoint without executing or faking it."""
    spec = FIRST_BATCH_COMPONENTS[name]
    entrypoint = spec.entrypoint or ""
    module_name = entrypoint.split(":", 1)[0]
    if not entrypoint:
        return AdapterProbe(name, "interface_only", False, spec.entrypoint,
                            "no verified executable entrypoint or package")
    if ":" not in entrypoint:
        if name == "arex-skill-refresh":
            return AdapterProbe(name, "interface_only", False, spec.entrypoint,
                                "refresh workflow is represented by an event schema; no verified local CLI")
        if "." in entrypoint and "-" not in entrypoint and " " not in entrypoint:
            package_name = entrypoint.split(".", 1)[0]
            try:
                available = importlib.util.find_spec(package_name) is not None
            except (ModuleNotFoundError, ValueError):
                available = False
            if not available:
                return AdapterProbe(name, "not_available", False, spec.entrypoint,
                                    "official package is not installed in this isolated worker")
            return AdapterProbe(name, "available_unexecuted", True, spec.entrypoint,
                                "official package resolved; real worker execution is deferred to the fair-test task")
        if shutil.which(entrypoint) is None:
            return AdapterProbe(name, "not_available", False, spec.entrypoint,
                                "official CLI is not installed in this isolated worker")
        return AdapterProbe(name, "available_unexecuted", True, spec.entrypoint,
                            "official CLI resolved; real worker execution is deferred to the fair-test task")
    try:
        available = importlib.util.find_spec(module_name) is not None
    except (ModuleNotFoundError, ValueError):
        available = False
    if not available:
        return AdapterProbe(name, "not_available", False, spec.entrypoint,
                            "official package is not installed in this isolated worker")
    return AdapterProbe(name, "available_unexecuted", True, spec.entrypoint,
                        "entrypoint resolved; real worker execution is deferred to the fair-test task")


def source_refresh_event(component: str, *, source_commit: str, observed_state: str,
                         evidence: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Create an auditable refresh event; no source is silently replaced."""
    if observed_state not in {"stale", "updated", "removed", "unknown", "unchanged"}:
        raise ValueError(f"unsupported refresh state: {observed_state}")
    return {
        "event": "source_refresh_observed",
        "component": component,
        "source_commit": source_commit,
        "observed_state": observed_state,
        "evidence": list(evidence),
        "action": "defer_until_review" if observed_state in {"stale", "removed", "unknown"} else "retain",
    }
