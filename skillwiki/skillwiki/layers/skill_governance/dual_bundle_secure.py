"""Descriptor-bound storage for the opt-in dual bundle on POSIX hosts."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_api = sys.modules[__name__.removesuffix("_secure")]
DIGEST_VERSION, FILES, PHASES = _api.DIGEST_VERSION, _api.FILES, _api.PHASES
AttemptCollisionError, DualBundleReceipt = _api.AttemptCollisionError, _api.DualBundleReceipt
EvidencePersistenceError, UnsafeEvidencePathError = _api.EvidencePersistenceError, _api.UnsafeEvidencePathError
_json, _linked_path_or_parent, _sha = _api._json, _api._linked_path_or_parent, _api._sha

DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
READ = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
COMMIT_PROTOCOL = "dual-bundle-commit-v1"
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
IDENTITY_KEYS = ("skill_id", "artifact_id", "skill_version", "source_repository",
                 "source_commit", "source_version", "historical_package_digest")


def available() -> bool:
    return (os.name == "posix" and hasattr(os, "O_NOFOLLOW") and
            os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd and
            os.rename in os.supports_dir_fd)


def open_dir(fd: int, name: str) -> int:
    return os.open(name, DIR, dir_fd=fd)


def open_path(path: Path) -> int:
    """Walk every absolute path component without following a link."""
    fd = os.open(path.anchor, DIR)
    try:
        for part in path.parts[1:]:
            next_fd = open_dir(fd, part)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def new_dir(fd: int, name: str) -> int:
    os.mkdir(name, dir_fd=fd)
    return open_dir(fd, name)


def put(fd: int, name: str, data: bytes) -> None:
    file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(file_fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(file_fd)
    finally:
        os.close(file_fd)


def get(fd: int, name: str) -> bytes:
    file_fd = os.open(name, READ, dir_fd=fd)
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise EvidencePersistenceError(f"non-regular evidence file: {name}")
        with os.fdopen(file_fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(file_fd)


def replace(fd: int, name: str, data: bytes) -> None:
    pending = f".{name}.{uuid.uuid4().hex}.pending"
    put(fd, pending, data)
    os.rename(pending, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


def publish_completion(fd: int, data: bytes) -> None:
    """The rename is the final commit operation; no fallible sync follows it."""
    pending = f".commit.json.{uuid.uuid4().hex}.pending"
    put(fd, pending, data)
    os.fsync(fd)
    os.rename(pending, "commit.json", src_dir_fd=fd, dst_dir_fd=fd)


def same_entry(parent_fd: int, name: str, owned_fd: int) -> bool:
    try:
        observed_fd = open_dir(parent_fd, name)
    except OSError:
        return False
    try:
        observed, owned = os.fstat(observed_fd), os.fstat(owned_fd)
        return (observed.st_dev, observed.st_ino) == (owned.st_dev, owned.st_ino)
    finally:
        os.close(observed_fd)


def still_owned(parent: Path, parent_fd: int, attempt_fd: int, attempt_name: str,
                output_fd: int | None, output_name: str) -> bool:
    if _linked_path_or_parent(parent):
        return False
    try:
        observed, owned = parent.stat(), os.fstat(parent_fd)
    except OSError:
        return False
    return ((observed.st_dev, observed.st_ino) == (owned.st_dev, owned.st_ino)
            and same_entry(parent_fd, attempt_name, attempt_fd)
            and (output_fd is None or same_entry(parent_fd, output_name, output_fd)))


def materialize(*, source_dir: str | Path, output_dir: str | Path,
                expected_declaration: Mapping[str, Any], task_id: str, task_digest: str,
                validator_plan: Mapping[str, Any]) -> DualBundleReceipt:
    if not available():
        raise EvidencePersistenceError("unsupported: stable no-follow directory-relative evidence operations unavailable")
    if not isinstance(source_dir, (str, Path)) or not isinstance(output_dir, (str, Path)):
        raise ValueError("source_dir and output_dir must be paths")
    source, output = Path(source_dir).resolve(), Path(os.path.abspath(output_dir))
    attempt = output.with_name(output.name + ".evidence")
    if _linked_path_or_parent(output) or _linked_path_or_parent(attempt):
        raise UnsafeEvidencePathError("output/evidence path or parent is a symlink/reparse point")
    if output.exists() or output.is_relative_to(source):
        raise ValueError("source must be separate and output must be new")
    parent_fd = open_path(output.parent)
    fds = [parent_fd]
    final_published = False
    try:
        try:
            attempt_fd = new_dir(parent_fd, attempt.name)
        except FileExistsError as exc:
            raise AttemptCollisionError(f"evidence attempt collision: {attempt}") from exc
        fds.append(attempt_fd)
        raw_fd = new_dir(attempt_fd, "raw")
        fds.append(raw_fd)
        tracked: dict[str, bytes] = {}

        def record(fd: int, name: str, data: bytes, key: str) -> None:
            put(fd, name, data)
            tracked[key] = data

        record(raw_fd, "stdout.bin", b"", "raw/stdout.bin")
        record(raw_fd, "stderr.bin", b"", "raw/stderr.bin")
        started = datetime.now(timezone.utc).isoformat()
        state, decision, reason = "stopped", "defer", "materialization incomplete"
        identity: dict[str, Any] = {}
        license_record: dict[str, Any] = {}
        validator: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        source_root = core_hash = bundle_root = None
        output_fd = payload_fd = metadata_fd = managed_metadata_fd = None
        try:
            if not source.is_dir():
                raise ValueError("source directory does not exist")
            if not isinstance(task_id, str) or not task_id.strip() or not isinstance(task_digest, str) or not task_digest.strip():
                raise ValueError("task identity must contain nonempty strings")
            if not isinstance(expected_declaration, Mapping):
                raise ValueError("expected declaration must be a mapping")
            record(raw_fd, "expected-declaration.json", _json(dict(expected_declaration)), "raw/expected-declaration.json")
            identity = {key: expected_declaration.get(key) for key in
                        ("skill_id", "artifact_id", "skill_version", "source_repository", "source_commit", "source_version", "historical_package_digest")}
            license_value = expected_declaration.get("license")
            if not isinstance(license_value, Mapping):
                raise ValueError(f"license record must be a mapping: {license_value!r}")
            license_record = dict(license_value)
            if not isinstance(validator_plan, Mapping):
                raise ValueError("validator plan must be a mapping")
            validator = dict(validator_plan)
            declared = expected_declaration.get("declared_files")
            if not isinstance(declared, (list, tuple)) or len(declared) != 3 or any(not isinstance(item, Mapping) for item in declared) or {item.get("path") for item in declared} != set(FILES):
                raise ValueError("expected declaration must contain exactly three fixed files")
            if any(not isinstance(value, str) or not value.strip() for value in identity.values()) or identity["artifact_id"] != f"{identity['skill_id']}@{identity['skill_version']}":
                raise ValueError("incomplete or conflicting skill/source identity")
            if (license_record.get("scope") != "task-local source package" or
                    license_record.get("evidence_path") != "source_payload/source_identity.json" or
                    license_record.get("state") != "resolved_fixture_metadata" or
                    not isinstance(license_record.get("value"), str) or not license_record["value"].strip() or
                    not isinstance(license_record.get("evidence_sha256"), str) or not license_record["evidence_sha256"].strip()):
                raise ValueError("license evidence is unresolved")
            if (validator.get("target_namespace") != "source_payload" or validator.get("target_path") != "source_payload/" or
                    not validator.get("id") or not validator.get("schema")):
                raise ValueError("validator target or identity is unsupported")

            output_fd = new_dir(parent_fd, output.name)
            fds.append(output_fd)
            payload_fd = new_dir(output_fd, "source_payload")
            fds.append(payload_fd)
            raw_source_fd = new_dir(raw_fd, "source_payload")
            fds.append(raw_source_fd)
            raw_managed_fd = new_dir(raw_fd, "managed_partial")
            fds.append(raw_managed_fd)
            managed_source_fd = new_dir(raw_managed_fd, "source_payload")
            fds.append(managed_source_fd)
            for name in FILES:
                try:
                    item = source / name
                    if item.is_symlink() or not item.is_file():
                        continue
                    data = item.read_bytes()
                except OSError:
                    continue
                record(raw_source_fd, name, data, f"raw/source_payload/{name}")
                put(payload_fd, name, data)
                record(managed_source_fd, name, data, f"raw/managed_partial/source_payload/{name}")
                rows.append({"path": name, "bytes": len(data), "sha256": _sha(data)})
            missing = [name for name in FILES if name not in {row["path"] for row in rows}]
            if missing:
                raise ValueError(f"missing or unreadable declared source files: {missing}")
            if {p.name for p in source.iterdir()} != set(FILES):
                raise ValueError("source path set differs from pinned three-file declaration")
            declaration = {item["path"]: item for item in declared}
            for row in rows:
                if row["bytes"] != declaration[row["path"]].get("bytes") or row["sha256"] != declaration[row["path"]].get("sha256"):
                    raise ValueError(f"declared byte/hash mismatch: {row['path']}")
            raw_identity = json.loads(get(payload_fd, "source_identity.json"))
            for expected_key, raw_key in (("source_repository", "repository"), ("source_commit", "commit"), ("source_version", "version")):
                if raw_identity.get(raw_key) != identity[expected_key]:
                    raise ValueError(f"source identity conflict: {expected_key}: {raw_identity.get(raw_key)!r} != {identity[expected_key]!r}")
            if raw_identity.get("license") != license_record["value"] or license_record["evidence_sha256"] != declaration["source_identity.json"]["sha256"]:
                raise ValueError(f"license conflict: source={raw_identity.get('license')!r}, expected={license_record['value']!r}")

            source_rows = sorted(rows, key=lambda item: item["path"].encode())
            source_root = _sha(_json(source_rows))
            metadata_fd = new_dir(output_fd, "management_metadata")
            fds.append(metadata_fd)
            managed_metadata_fd = new_dir(raw_managed_fd, "management_metadata")
            fds.append(managed_metadata_fd)
            manifest = {"digest_version": DIGEST_VERSION, "source_inventory": source_rows,
                        "source_payload_root_sha256": source_root, "identity": identity, "license": license_record,
                        "historical_package_digest": identity["historical_package_digest"],
                        "task_id": task_id, "task_digest": task_digest,
                        "validator_plan": {**validator, "status": "not_run/defer", "compatibility": "unverified"}}
            manifest_bytes = _json(manifest)
            put(metadata_fd, "transfer-manifest.json", manifest_bytes)
            record(managed_metadata_fd, "transfer-manifest.json", manifest_bytes, "raw/managed_partial/management_metadata/transfer-manifest.json")
            core_hash = _sha(manifest_bytes)
            bundle_rows = sorted([{"path": f"source_payload/{row['path']}", "bytes": row["bytes"], "sha256": row["sha256"]} for row in rows] +
                                 [{"path": "management_metadata/transfer-manifest.json", "bytes": len(manifest_bytes), "sha256": core_hash}],
                                 key=lambda item: item["path"].encode())
            bundle_root = _sha(_json(bundle_rows))
            state, decision, reason = "materialized", "materialized", "source payload preserved; later phases not run"
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            replace(raw_fd, "stderr.bin", reason.encode())
            tracked["raw/stderr.bin"] = reason.encode()

        phases = {phase: {"status": "not_run", "reason": "materializer does not execute this phase" if state == "materialized" else "materialization stopped"} for phase in PHASES}
        receipt = {"argv": ["materialize_dual_bundle"], "cwd": os.getcwd(), "started_at": started,
                   "ended_at": datetime.now(timezone.utc).isoformat(), "state": "pending" if state == "materialized" else "stopped",
                   "decision": "pending" if state == "materialized" else "defer", "exit_code": None if state == "materialized" else 1,
                   "reason": reason, "rerun": False, "stdout_path": "raw/stdout.bin", "stderr_path": "raw/stderr.bin",
                   "stdout_sha256": _sha(tracked["raw/stdout.bin"]), "stderr_sha256": _sha(tracked["raw/stderr.bin"]),
                   "source_payload_root_sha256": source_root, "management_core_sha256": core_hash,
                   "materialized_bundle_root_sha256": bundle_root, "phases": phases,
                   "identity": identity, "license": license_record}
        receipt_bytes = _json(receipt)
        if state == "materialized":
            if metadata_fd is None or managed_metadata_fd is None:
                raise EvidencePersistenceError("materialized state lacks management metadata")
            put(metadata_fd, "provenance-receipt.json", receipt_bytes)
            record(managed_metadata_fd, "provenance-receipt.json", receipt_bytes,
                   "raw/managed_partial/management_metadata/provenance-receipt.json")
        record(raw_fd, "receipt.json", receipt_bytes, "raw/receipt.json")
        expected = [f"raw/source_payload/{name}" for name in FILES] + ["raw/stdout.bin", "raw/stderr.bin", "raw/receipt.json"]
        evidence_rows = [{"path": name, "present": True, "bytes": len(data), "sha256": _sha(data)}
                         for name, data in sorted(tracked.items(), key=lambda item: item[0].encode())]
        evidence_rows += [{"path": name, "present": False, "bytes": None, "sha256": None, "reason": "not generated before stop"}
                          for name in expected if name not in tracked]
        files_bytes = _json({"digest_version": DIGEST_VERSION, "files": evidence_rows})
        record(raw_fd, "files.json", files_bytes, "raw/files.json")
        index_rows = evidence_rows + [{"path": "raw/files.json", "present": True, "bytes": len(files_bytes), "sha256": _sha(files_bytes)}]
        index_bytes = _json({"digest_version": DIGEST_VERSION, "files": index_rows})
        put(attempt_fd, "evidence-index.json", index_bytes)
        if not still_owned(attempt.parent, parent_fd, attempt_fd, attempt.name, output_fd, output.name):
            raise EvidencePersistenceError("owned attempt/output identity changed before final status")
        status = {"state": state, "decision": decision, "exit_code": 0 if state == "materialized" else 1,
                  "attempt_id": attempt.name, "attempt_dev": os.fstat(attempt_fd).st_dev, "attempt_ino": os.fstat(attempt_fd).st_ino,
                  "output_dev": os.fstat(output_fd).st_dev if output_fd is not None else None,
                  "output_ino": os.fstat(output_fd).st_ino if output_fd is not None else None,
                  "receipt_sha256": _sha(receipt_bytes), "index_sha256": _sha(index_bytes),
                  "identity": identity,
                  "reason": reason, "ended_at": datetime.now(timezone.utc).isoformat()}
        status_bytes = _json(status)
        replace(attempt_fd, "final.json" if state == "materialized" else "failed.json", status_bytes)
        final_published = state == "materialized"
        if not still_owned(attempt.parent, parent_fd, attempt_fd, attempt.name, output_fd, output.name):
            raise EvidencePersistenceError("owned attempt/output identity changed after final status")
        result = DualBundleReceipt(state, attempt.name, str(output / "source_payload") if state == "materialized" else None,
                                 str(output / "management_metadata") if state == "materialized" else None,
                                 tuple(rows), source_root, core_hash, bundle_root, identity, license_record,
                                 {**validator, "status": "not_run/defer", "compatibility": "unverified"},
                                 str(attempt / "evidence-index.json"), decision, reason, phases)
        if state == "materialized":
            publish_completion(attempt_fd, _json({"protocol": COMMIT_PROTOCOL, "final_sha256": _sha(status_bytes),
                "attempt_dev": status["attempt_dev"], "attempt_ino": status["attempt_ino"],
                "output_dev": status["output_dev"], "output_ino": status["output_ino"]}))
        return result
    except Exception as exc:
        failure = exc if isinstance(exc, (EvidencePersistenceError, AttemptCollisionError, UnsafeEvidencePathError)) else EvidencePersistenceError(f"evidence-persistence failure: {type(exc).__name__}: {exc}")
        try:
            if "attempt_fd" in locals():
                if final_published:
                    try:
                        os.unlink("final.json", dir_fd=attempt_fd)
                        os.fsync(attempt_fd)
                    except OSError:
                        pass
                replace(attempt_fd, "failed.json", _json({"state": "stopped", "decision": "defer", "exit_code": 1,
                    "reason": str(failure), "attempt_id": attempt.name}))
        except OSError:
            pass
        raise failure from exc
    finally:
        for descriptor in reversed(fds):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _object(data: bytes, label: str) -> dict[str, Any]:
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _digest(value: Any) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _identity(value: Any) -> bool:
    return (isinstance(value, dict) and
            all(isinstance(value.get(key), str) and value[key].strip() for key in IDENTITY_KEYS) and
            value["artifact_id"] == f"{value['skill_id']}@{value['skill_version']}")


def _indexed_receipt(index: dict[str, Any], receipt_bytes: bytes) -> bool:
    if index.get("digest_version") != DIGEST_VERSION or not isinstance(index.get("files"), list):
        return False
    seen: set[str] = set()
    receipt_row = None
    for row in index["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not row["path"] or row["path"] in seen:
            return False
        seen.add(row["path"])
        if type(row.get("present")) is not bool:
            return False
        if row["present"]:
            if not _integer(row.get("bytes")) or not _digest(row.get("sha256")):
                return False
        elif row.get("bytes") is not None or row.get("sha256") is not None:
            return False
        if row["path"] == "raw/receipt.json":
            receipt_row = row
    return (receipt_row is not None and receipt_row["present"] is True and
            receipt_row["bytes"] == len(receipt_bytes) and receipt_row["sha256"] == _sha(receipt_bytes))


def read_state(attempt_path: str | Path) -> dict[str, Any]:
    """Only a still-owned final marker bound to receipt/index hashes is success."""
    try:
        if not available():
            return {"success": False, "reason": "unsupported platform"}
        attempt = Path(os.path.abspath(attempt_path))
        if not attempt.name.endswith(".evidence") or attempt.name == ".evidence":
            return {"success": False, "reason": "invalid attempt name"}
        if _linked_path_or_parent(attempt):
            return {"success": False, "reason": "unsafe attempt path"}
        parent_fd = open_path(attempt.parent)
        try:
            attempt_fd = open_dir(parent_fd, attempt.name)
            try:
                if not still_owned(attempt.parent, parent_fd, attempt_fd, attempt.name, None, ""):
                    return {"success": False, "reason": "attempt identity changed"}
                try:
                    get(attempt_fd, "failed.json")
                    return {"success": False, "reason": "failed state present"}
                except FileNotFoundError:
                    pass
                final_bytes = get(attempt_fd, "final.json")
                final = _object(final_bytes, "final")
                if (final.get("state") != "materialized" or final.get("decision") != "materialized" or
                        type(final.get("exit_code")) is not int or final["exit_code"] != 0):
                    return {"success": False, "reason": "final state is not materialized"}
                own = os.fstat(attempt_fd)
                if (final.get("attempt_id") != attempt.name or
                        not _integer(final.get("attempt_dev")) or not _integer(final.get("attempt_ino")) or
                        (final["attempt_dev"], final["attempt_ino"]) != (own.st_dev, own.st_ino) or
                        not _integer(final.get("output_dev")) or not _integer(final.get("output_ino")) or
                        not _digest(final.get("receipt_sha256")) or not _digest(final.get("index_sha256")) or
                        not _identity(final.get("identity"))):
                    return {"success": False, "reason": "final identity or digest is invalid"}
                raw_fd = open_dir(attempt_fd, "raw")
                try:
                    receipt_bytes = get(raw_fd, "receipt.json")
                finally:
                    os.close(raw_fd)
                index_bytes = get(attempt_fd, "evidence-index.json")
                if final.get("receipt_sha256") != _sha(receipt_bytes) or final.get("index_sha256") != _sha(index_bytes):
                    return {"success": False, "reason": "receipt/index digest mismatch"}
                receipt, index = _object(receipt_bytes, "receipt"), _object(index_bytes, "index")
                if ((receipt.get("state"), receipt.get("decision"), receipt.get("exit_code")) !=
                        ("pending", "pending", None) or not _identity(receipt.get("identity")) or
                        receipt["identity"] != final["identity"] or not _indexed_receipt(index, receipt_bytes)):
                    return {"success": False, "reason": "receipt/index contract mismatch"}
                output_name = attempt.name.removesuffix(".evidence")
                output_fd = open_dir(parent_fd, output_name)
                try:
                    observed = os.fstat(output_fd)
                    if (final.get("output_dev"), final.get("output_ino")) != (observed.st_dev, observed.st_ino):
                        return {"success": False, "reason": "output identity mismatch"}
                    commit = _object(get(attempt_fd, "commit.json"), "commit")
                    if (commit.get("protocol") != COMMIT_PROTOCOL or commit.get("final_sha256") != _sha(final_bytes) or
                            (commit.get("attempt_dev"), commit.get("attempt_ino")) != (own.st_dev, own.st_ino) or
                            (commit.get("output_dev"), commit.get("output_ino")) != (observed.st_dev, observed.st_ino) or
                            not all(_integer(commit.get(key)) for key in
                                    ("attempt_dev", "attempt_ino", "output_dev", "output_ino"))):
                        return {"success": False, "reason": "commit contract mismatch"}
                    if not still_owned(attempt.parent, parent_fd, attempt_fd, attempt.name,
                                       output_fd, output_name):
                        return {"success": False, "reason": "attempt/output identity changed"}
                finally:
                    os.close(output_fd)
                return {"success": True, "reason": "owned committed final and evidence contract match"}
            finally:
                os.close(attempt_fd)
        finally:
            os.close(parent_fd)
    except (OSError, ValueError, TypeError, KeyError, EvidencePersistenceError) as exc:
        return {"success": False, "reason": f"unreadable state: {type(exc).__name__}: {exc}"}
