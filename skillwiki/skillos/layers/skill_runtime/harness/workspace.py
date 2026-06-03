"""Filesystem evidence store for harness verification loops."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .base import VerificationLoopResult


def default_harness_root() -> Path:
    """Return the default local evidence root."""

    return Path(__file__).resolve().parents[5] / "artifacts" / "harness-runs"


class HarnessWorkspace:
    """Small JSON evidence workspace for one verification loop."""

    def __init__(self, loop_id: str, root: Path | None = None) -> None:
        self.loop_id = loop_id
        self.root = (root or default_harness_root()).resolve()
        self.loop_dir = self.root / loop_id
        self.loop_dir.mkdir(parents=True, exist_ok=True)

    def attempt_dir(self, attempt: int) -> Path:
        path = self.loop_dir / f"attempt-{attempt:03d}"
        path.mkdir(parents=True, exist_ok=True)
        (path / "artifacts").mkdir(exist_ok=True)
        return path

    def write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def save_loop_result(self, result: VerificationLoopResult) -> None:
        self.write_json(self.loop_dir / "result.json", result.model_dump(mode="json"))


class HarnessEvidenceStore:
    """Read/list helper for saved verification loops."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_harness_root()).resolve()

    def get(self, loop_id: str) -> Dict[str, Any] | None:
        path = self.root / loop_id / "result.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for path in sorted(
            self.root.glob("*/result.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return rows
