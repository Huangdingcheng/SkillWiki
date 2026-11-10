"""Store-scoped immutable execution receipts, with atomic complete-file writes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from ..utils.filesystem_paths import filesystem_path

_lock = threading.RLock()


def history_directory(app: Any) -> Path | None:
    # The trace path is already set per configured SkillStorage at startup.
    trace = getattr(app, "execution_trace_path", None)
    return Path(trace).parent / "execution-history" if trace else None


def _path(directory: Path, execution_id: str) -> Path:
    return directory / (hashlib.sha256(execution_id.encode("utf-8")).hexdigest() + ".json")


def save(directory: Path, row: dict[str, Any]) -> dict[str, Any]:
    ident = row.get("execution_id")
    if not isinstance(ident, str) or not ident:
        raise ValueError("execution identity is required")
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    directory = filesystem_path(directory)
    target = _path(directory, ident)
    with _lock:
        directory.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = json.loads(target.read_bytes())
            comparable = dict(existing)
            comparable["created_at"] = row["created_at"]
            if comparable != row:
                raise ValueError("execution identity already has different immutable evidence")
            return existing
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Same-directory hard link publishes the complete flushed inode
            # atomically and refuses an existing identity, even across processes.
            # Windows NTFS and the local filesystems used by this Demo support it.
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing = json.loads(target.read_bytes())
                comparable = dict(existing)
                comparable["created_at"] = row["created_at"]
                if comparable != row:
                    raise ValueError("execution identity already has different immutable evidence")
                return existing
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return row


def load(directory: Path) -> list[dict[str, Any]]:
    directory = filesystem_path(directory)
    rows = []
    if not directory.exists():
        return rows
    for path in directory.glob("*.json"):
        row = json.loads(path.read_bytes())
        if not isinstance(row, dict) or not isinstance(row.get("execution_id"), str) or path != _path(directory, row["execution_id"]):
            raise ValueError("execution receipt identity is corrupt")
        rows.append(row)
    return sorted(rows, key=lambda row: (row["created_at"], row["execution_id"]))
