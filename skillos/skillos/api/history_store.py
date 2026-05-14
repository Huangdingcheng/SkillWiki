"""Execution history persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class FileExecutionHistoryRepository:
    """Small JSONL-backed execution history repository.

    This is used as the durable local fallback when PostgreSQL is unavailable.
    It preserves the same async interface as ExecutionHistoryRepository so the
    API route can use either implementation transparently.
    """

    def __init__(self, path: Path, max_items: int = 500) -> None:
        self._path = path
        self._max_items = max_items
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def save_plan_history(self, history_item: Dict[str, Any]) -> None:
        item = dict(history_item)
        existing = self._read_all()
        existing.append(item)
        if len(existing) > self._max_items:
            existing = existing[-self._max_items:]
        self._write_all(existing)

    async def list_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(reversed(self._read_all()[-limit:]))

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        items: List[Dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    items.append(item)
        return items

    def _write_all(self, items: List[Dict[str, Any]]) -> None:
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False, default=str))
                fh.write("\n")
        tmp_path.replace(self._path)
