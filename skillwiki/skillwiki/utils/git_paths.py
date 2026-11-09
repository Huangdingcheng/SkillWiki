"""Repository-local Git preparation for Windows long paths."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


def git_process_cwd(repo_path: str | Path) -> str:
    """Keep Windows extended paths out of Git's loose-object mmap paths."""
    path = str(repo_path)
    if os.name == "nt":
        if path.startswith("\\\\?\\UNC\\"):
            return "\\\\" + path[8:]
        if path.startswith("\\\\?\\"):
            return path[4:]
    return path


def git_process_path_error(repo_path: str | Path, exc: OSError) -> str:
    """Explain OS process-path failures without claiming long paths are usable."""
    cwd = git_process_cwd(repo_path)
    return (
        f"Cannot start Git in repository path {repo_path} "
        f"(process cwd length {len(cwd)}, OS error {getattr(exc, 'winerror', None) or exc.errno}): "
        f"{exc}. The repository path may be inaccessible or exceed this platform's "
        "process working-directory limits; repository-local core.longpaths cannot "
        "repair a process startup failure."
    )


def ensure_local_longpaths(repo_path: str | Path, *, timeout_seconds: float = 10.0) -> None:
    """Enable and confirm long paths without changing any global Git setting."""
    if os.name != "nt":
        return

    commands = (
        ["git", "config", "--local", "core.longpaths", "true"],
        ["git", "config", "--local", "--bool", "--get", "core.longpaths"],
    )
    for command in commands:
        try:
            result = subprocess.run(
                command, cwd=git_process_cwd(repo_path), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_seconds,
            )
        except OSError as exc:
            raise RuntimeError(git_process_path_error(repo_path, exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Cannot prepare repository-local core.longpaths for {repo_path}: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(
                f"Cannot prepare repository-local core.longpaths for {repo_path}: "
                f"{' '.join(command)} exited {result.returncode}: {detail}"
            )
    if result.stdout.strip().lower() != "true":
        raise RuntimeError(
            f"Repository-local core.longpaths was not confirmed true for {repo_path}: "
            f"{result.stdout.strip()!r}"
        )
