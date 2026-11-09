"""Public opt-in dual-bundle API and durable-state reader."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

FILES = ("SKILL.md", "MANIFEST.yaml", "source_identity.json")
PHASES = ("transfer_verified", "governance", "pre_revalidation_defer", "restore", "rollback_verified")
DIGEST_VERSION = "dual-bundle-digest-v1"


class EvidencePersistenceError(RuntimeError):
    """A durable result cannot be safely produced or read."""


class AttemptCollisionError(FileExistsError):
    """The attempt name is already owned by another run."""


class UnsafeEvidencePathError(ValueError):
    """An output/evidence path contains a link or reparse point."""


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _linked_path_or_parent(path: Path) -> bool:
    for current in (path, *path.parents):
        try:
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
    return False


@dataclass(frozen=True)
class DualBundleReceipt:
    state: str
    attempt_id: str
    source_payload_dir: str | None
    management_metadata_dir: str | None
    source_files: tuple[dict[str, Any], ...]
    source_payload_root_sha256: str | None
    management_core_sha256: str | None
    materialized_bundle_root_sha256: str | None
    identity: dict[str, Any]
    license: dict[str, Any]
    validator_plan: dict[str, Any]
    raw_evidence_index: str
    decision: str
    reason: str
    phases: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _secure_core():
    name = f"{__name__}_secure"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("dual_bundle_secure.py"))
        if spec is None or spec.loader is None:
            raise EvidencePersistenceError("unsupported: secure dual bundle core unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def materialize_dual_bundle(
    *, source_dir: str | Path, output_dir: str | Path,
    expected_declaration: Mapping[str, Any], task_id: str, task_digest: str,
    validator_plan: Mapping[str, Any],
) -> DualBundleReceipt:
    return _secure_core().materialize(source_dir=source_dir, output_dir=output_dir,
                                      expected_declaration=expected_declaration, task_id=task_id,
                                      task_digest=task_digest, validator_plan=validator_plan)


def read_dual_bundle_state(attempt_path: str | Path) -> dict[str, Any]:
    return _secure_core().read_state(attempt_path)
