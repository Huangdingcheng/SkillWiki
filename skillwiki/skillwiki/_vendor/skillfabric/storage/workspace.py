"""SkillFabric workspace storage utilities."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


class Workspace:
    """Manage the .skillfabric workspace directory."""

    def __init__(self, root: str | Path = ".skillfabric") -> None:
        self.root = Path(root)

    @property
    def graph_dir(self) -> Path:
        return self.root / "graph"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def wiki_dir(self) -> Path:
        return self.root / "wiki"

    @property
    def wiki_skills_dir(self) -> Path:
        return self.wiki_dir / "skills"

    @property
    def wiki_skill_cards_dir(self) -> Path:
        return self.wiki_skills_dir / "cards"

    @property
    def wiki_skill_sources_dir(self) -> Path:
        return self.wiki_skills_dir / "sources"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    @property
    def lock_path(self) -> Path:
        return self.root / "build.lock"

    def ensure(self) -> None:
        for path in (
            self.graph_dir,
            self.cache_dir,
            self.wiki_dir,
            self.wiki_skills_dir,
            self.wiki_skill_cards_dir,
            self.wiki_skill_sources_dir,
            self.runs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def write_json(self, path: str | Path, payload: Any) -> None:
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def write_jsonl(self, path: str | Path, rows: list[dict[str, Any]]) -> None:
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        atomic_write_text(path, text)

    def read_json(self, path: str | Path, default: Any = None) -> Any:
        target = Path(path)
        if not target.exists():
            return default
        return json.loads(target.read_text(encoding="utf-8"))

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                if attempt == 0 and self._reclaim_stale_lock():
                    continue
                raise RuntimeError(f"build lock already exists: {self.lock_path}") from exc
        else:
            raise RuntimeError(f"failed to acquire build lock: {self.lock_path}")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            yield
        finally:
            with suppress(FileNotFoundError):
                self.lock_path.unlink()

    def _reclaim_stale_lock(self) -> bool:
        """Remove a lock file if it does not point at a live process."""

        try:
            raw_pid = self.lock_path.read_text(encoding="utf-8").strip()
            pid = int(raw_pid)
        except (FileNotFoundError, OSError, ValueError):
            pid = -1
        if _pid_is_running(pid):
            return False
        with suppress(FileNotFoundError):
            self.lock_path.unlink()
        return True


def atomic_write_text(path: str | Path, text: str) -> None:
    """Atomically write a text file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
