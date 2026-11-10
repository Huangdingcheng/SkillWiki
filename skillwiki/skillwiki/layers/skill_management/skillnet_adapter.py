"""Source-bound SkillNet-Fabric route probe receipts.

This module records a narrow adapter boundary without selecting or executing a
replacement route. Upstream execution remains an explicit caller decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

SKILLNET_REPOSITORY = "https://github.com/zjunlp/SkillNet-Fabric"
SKILLNET_COMMIT = "e0bc52856e8244ef6eecc0fe8c1b5f481b4190a3"
SKILLNET_VERSION = "0.2.0"
SKILLNET_LICENSE = "MIT"
RECEIPT_SCHEMA = "skillwiki.skillnet.route_probe_receipt.v1"


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


@dataclass(frozen=True)
class SkillNetRouteReceipt:
    """Auditable result of one prepared or attempted SkillNet probe."""

    source_commit: str
    source_version: str
    source_license: str
    catalog_digest: str
    task_digest: str
    candidate_id: str | None
    candidate_version: str | None
    candidate_source: str | None
    command: tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_code: int | None
    duration_seconds: float | None
    status: str
    provenance: Mapping[str, Any]
    recorded_at: str

    def __post_init__(self) -> None:
        for name in ("source_commit", "source_version", "source_license", "catalog_digest", "task_digest"):
            _require_text(getattr(self, name), name)
        if self.source_commit != SKILLNET_COMMIT:
            raise ValueError("source_commit is not the frozen SkillNet-Fabric identity")
        if self.source_version != SKILLNET_VERSION or self.source_license != SKILLNET_LICENSE:
            raise ValueError("source version/license does not match frozen identity")
        if self.status not in {"prepared", "blocked", "completed", "failed"}:
            raise ValueError("status must be prepared, blocked, completed, or failed")
        if self.exit_code is not None and not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer or null")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if self.status == "completed" and self.exit_code != 0:
            raise ValueError("completed receipt requires exit_code=0")
        if self.status in {"prepared", "blocked"} and self.candidate_id is not None:
            raise ValueError("prepared/blocked receipts cannot claim a selected candidate")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RECEIPT_SCHEMA,
            "adapter": "skillnet-fabric-narrow-route",
            "source": {
                "repository": SKILLNET_REPOSITORY,
                "commit": self.source_commit,
                "version": self.source_version,
                "license": self.source_license,
            },
            "catalog_digest": self.catalog_digest,
            "task_digest": self.task_digest,
            "candidate": {
                "id": self.candidate_id,
                "version": self.candidate_version,
                "source": self.candidate_source,
            },
            "raw": {
                "command": list(self.command),
                "stdout": self.raw_stdout,
                "stderr": self.raw_stderr,
                "exit_code": self.exit_code,
                "duration_seconds": self.duration_seconds,
            },
            "status": self.status,
            "provenance": dict(self.provenance),
            "recorded_at": self.recorded_at,
        }


def build_skillnet_receipt(
    *,
    catalog_digest: str,
    task_digest: str,
    expected_task_digest: str | None = None,
    command: Sequence[str],
    raw_stdout: str = "",
    raw_stderr: str = "",
    exit_code: int | None = None,
    duration_seconds: float | None = None,
    status: str = "prepared",
    candidate_id: str | None = None,
    candidate_version: str | None = None,
    candidate_source: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Build a source-bound receipt without invoking SkillNet-Fabric."""

    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must contain non-empty strings")
    if expected_task_digest is not None and expected_task_digest != task_digest:
        raise ValueError("task_digest does not match expected_task_digest")
    timestamp = recorded_at or datetime.now(timezone.utc).isoformat()
    return SkillNetRouteReceipt(
        source_commit=SKILLNET_COMMIT,
        source_version=SKILLNET_VERSION,
        source_license=SKILLNET_LICENSE,
        catalog_digest=catalog_digest,
        task_digest=task_digest,
        candidate_id=candidate_id,
        candidate_version=candidate_version,
        candidate_source=candidate_source,
        command=tuple(command),
        raw_stdout=raw_stdout,
        raw_stderr=raw_stderr,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        status=status,
        provenance=provenance or {},
        recorded_at=timestamp,
    ).to_dict()
