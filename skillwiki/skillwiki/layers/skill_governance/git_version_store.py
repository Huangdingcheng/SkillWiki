"""Git-backed version store for Skill governance.

Git remains the source of truth for branches, commits, history, and diffs.
SkillWiki adds Skill-level meaning above this adapter in later stages.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


class GitVersionStoreError(RuntimeError):
    """Raised when a Git-backed version operation cannot be completed."""


@dataclass(frozen=True)
class GitCommit:
    """Small, JSON-friendly commit summary."""

    commit_hash: str
    author: str
    authored_at: str
    subject: str
    changed_paths: Tuple[str, ...] = ()


class GitVersionStore:
    """Thin wrapper around Git commands used by the governance layer."""

    def __init__(self, repo_path: str | Path, timeout_seconds: float = 10.0) -> None:
        self.repo_path = Path(repo_path)
        self.timeout_seconds = timeout_seconds
        self._lock_depth = 0

    def is_git_repo(self) -> bool:
        """Return True when repo_path is inside a Git work tree."""
        try:
            output = self._run(["rev-parse", "--is-inside-work-tree"], check=False)
        except GitVersionStoreError:
            return False
        return output.strip().lower() == "true"

    def current_branch(self) -> str:
        """Return the current branch name, or HEAD for detached checkouts."""
        self._require_repo()
        return self._run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()

    def branch_exists(self, branch_name: str) -> bool:
        """Return True when a local branch exists."""
        self._require_repo()
        branch = self._normalize_branch_name(branch_name)
        try:
            self._run(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
        except GitVersionStoreError:
            return False
        return True

    def create_branch(self, branch_name: str, start_point: str = "HEAD") -> None:
        """Create a local branch at start_point."""
        self._require_repo()
        branch = self._normalize_branch_name(branch_name)
        if self.branch_exists(branch):
            raise GitVersionStoreError(f"Git branch already exists: {branch}")
        with self.lock():
            self._run(["branch", branch, start_point])

    def checkout(self, branch_name: str) -> None:
        """Checkout an existing local branch."""
        self._require_repo()
        branch = self._normalize_branch_name(branch_name)
        with self.lock():
            self._run(["checkout", branch])

    def tag_exists(self, tag_name: str) -> bool:
        """Return True when a local tag exists."""
        self._require_repo()
        tag = self._normalize_tag_name(tag_name)
        try:
            self._run(["show-ref", "--verify", "--quiet", f"refs/tags/{tag}"])
        except GitVersionStoreError:
            return False
        return True

    def create_tag(self, tag_name: str, ref: str = "HEAD") -> None:
        """Create a lightweight local tag for a ref."""
        self._require_repo()
        tag = self._normalize_tag_name(tag_name)
        if self.tag_exists(tag):
            raise GitVersionStoreError(f"Git tag already exists: {tag}")
        with self.lock():
            self._run(["tag", tag, ref])

    def read_file_at_ref(self, ref: str, path: str | Path) -> str:
        """Read a repo-relative file from a commit, branch, or tag."""
        self._require_repo()
        git_path = self._normalize_repo_path(path)
        clean_ref = ref.strip()
        if not clean_ref:
            raise ValueError("Git ref cannot be empty.")
        return self._run(["show", f"{clean_ref}:{git_path}"])

    def head_commit(self) -> str:
        """Return the current HEAD commit hash."""
        self._require_repo()
        return self._run(["rev-parse", "HEAD"]).strip()

    def commit_paths(
        self,
        paths: Sequence[str | Path],
        message: str,
        author_name: str = "SkillWiki",
        author_email: str = "skillos@example.local",
    ) -> str:
        """Commit selected repo-relative paths and return the new HEAD hash."""
        self._require_repo()
        normalized_paths = [self._normalize_repo_path(path) for path in paths]
        if not normalized_paths:
            raise ValueError("At least one path is required to create a commit.")
        if not message.strip():
            raise ValueError("Commit message cannot be empty.")

        with self.lock():
            self._ensure_no_unrelated_staged_paths(normalized_paths)
            self._run(["add", "--", *normalized_paths])
            self._run(
                [
                    "-c",
                    f"user.name={author_name}",
                    "-c",
                    f"user.email={author_email}",
                    "commit",
                    "-m",
                    message,
                    "--",
                    *normalized_paths,
                ]
            )
        return self.head_commit()

    def ensure_paths_clean(self, paths: Sequence[str | Path]) -> None:
        """Reject writes that would overwrite existing uncommitted path changes."""
        self._require_repo()
        normalized_paths = [self._normalize_repo_path(path) for path in paths]
        if not normalized_paths:
            return
        output = self._run(
            ["status", "--porcelain=v1", "--untracked-files=all", "--", *normalized_paths]
        )
        dirty_paths = [
            line[3:].strip()
            for line in output.splitlines()
            if len(line) >= 4
        ]
        if dirty_paths:
            raise GitVersionStoreError(
                "Refusing governance write over uncommitted path changes: "
                + ", ".join(dirty_paths)
            )

    def commit_history(self, path: str | Path, max_count: int = 20) -> List[GitCommit]:
        """Return newest-first commit history for a repo-relative path."""
        self._require_repo()
        if max_count <= 0:
            raise ValueError("max_count must be greater than zero.")

        repo_path = self._normalize_repo_path(path)
        marker = "--SKILLOS-COMMIT--"
        fmt = f"{marker}%x1f%H%x1f%an%x1f%aI%x1f%s"
        output = self._run(
            [
                "log",
                f"--max-count={max_count}",
                f"--format={fmt}",
                "--name-only",
                "--",
                repo_path,
            ]
        )
        return self._parse_history(output, marker)

    def commit_histories(
        self,
        paths: Sequence[str | Path],
        max_count: int = 20,
    ) -> Dict[str, List[GitCommit]]:
        """Return newest-first commit histories for multiple repo-relative paths."""
        self._require_repo()
        if max_count <= 0:
            raise ValueError("max_count must be greater than zero.")
        normalized_paths = list(dict.fromkeys(self._normalize_repo_path(path) for path in paths))
        if not normalized_paths:
            return {}

        marker = "--SKILLOS-COMMIT--"
        fmt = f"{marker}%x1f%H%x1f%an%x1f%aI%x1f%s"
        scan_count = max_count * len(normalized_paths) * 3
        output = self._run(
            [
                "log",
                f"--max-count={scan_count}",
                f"--format={fmt}",
                "--name-only",
                "--",
                *normalized_paths,
            ]
        )
        histories: Dict[str, List[GitCommit]] = {path: [] for path in normalized_paths}
        for commit in self._parse_history(output, marker):
            changed_paths = set(commit.changed_paths)
            for path in normalized_paths:
                if path in changed_paths and len(histories[path]) < max_count:
                    histories[path].append(commit)
        return histories

    def diff_between(
        self,
        from_ref: str,
        to_ref: str,
        path: Optional[str | Path] = None,
    ) -> str:
        """Return a unified, no-color Git diff between two refs."""
        self._require_repo()
        args = ["diff", "--no-color", "--no-ext-diff", f"{from_ref}..{to_ref}"]
        if path is not None:
            args.extend(["--", self._normalize_repo_path(path)])
        return self._run(args)

    def diff_between_paths(
        self,
        from_ref: str,
        to_ref: str,
        paths: Sequence[str | Path],
    ) -> str:
        """Return a unified, no-color Git diff for selected repo-relative paths."""
        self._require_repo()
        normalized_paths = [self._normalize_repo_path(path) for path in paths]
        if not normalized_paths:
            raise ValueError("At least one path is required to create a diff.")
        return self._run(
            [
                "diff",
                "--no-color",
                "--no-ext-diff",
                f"{from_ref}..{to_ref}",
                "--",
                *normalized_paths,
            ]
        )

    def _require_repo(self) -> None:
        if not self.is_git_repo():
            raise GitVersionStoreError(f"{self.repo_path} is not a Git repository.")

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Acquire a small repo-local lock for governance write operations."""
        self._require_repo()
        if self._lock_depth > 0:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return

        lock_path = self.repo_path / self._run(["rev-parse", "--git-path", "skillos-governance.lock"]).strip()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise GitVersionStoreError(f"Governance Git repository is locked: {lock_path}") from exc

        self._lock_depth = 1
        try:
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            yield
        finally:
            self._lock_depth = 0
            os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def repository_status(self) -> Dict[str, object]:
        """Return read-only local/remote status for the governance Git repository."""
        self._require_repo()
        try:
            branch = self.current_branch()
        except GitVersionStoreError:
            branch = self._run(["branch", "--show-current"], check=False).strip()
        try:
            head_commit = self.head_commit()
        except GitVersionStoreError:
            head_commit = ""
        status_lines = self._run(["status", "--porcelain=v1", "--untracked-files=all"]).splitlines()
        staged_paths = [
            line[3:].strip()
            for line in status_lines
            if len(line) >= 3 and line[:2] != "??" and line[0] != " "
        ]
        unstaged_paths = [
            line[3:].strip()
            for line in status_lines
            if len(line) >= 3 and line[:2] != "??" and line[1] != " "
        ]
        untracked_paths = [
            line[3:].strip()
            for line in status_lines
            if line.startswith("?? ")
        ]
        upstream = self._run(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            check=False,
        ).strip()
        ahead = 0
        behind = 0
        if upstream:
            counts = self._run(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], check=False).split()
            if len(counts) == 2 and all(part.isdigit() for part in counts):
                ahead = int(counts[0])
                behind = int(counts[1])

        return {
            "backend": "git",
            "is_git_repo": True,
            "branch": branch,
            "head_commit": head_commit,
            "dirty": bool(status_lines),
            "staged_paths": staged_paths,
            "unstaged_paths": unstaged_paths,
            "untracked_paths": untracked_paths,
            "upstream": upstream or None,
            "ahead": ahead,
            "behind": behind,
        }

    def _run(self, args: Sequence[str], check: bool = True) -> str:
        command = ["git", *args]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise GitVersionStoreError("Git executable was not found.") from exc
        except subprocess.TimeoutExpired as exc:
            printable = " ".join(command)
            raise GitVersionStoreError(f"Git command timed out: {printable}") from exc

        if check and completed.returncode != 0:
            printable = " ".join(command)
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise GitVersionStoreError(f"Git command failed: {printable}: {detail}")
        return completed.stdout

    def _ensure_no_unrelated_staged_paths(self, normalized_paths: Sequence[str]) -> None:
        staged = {
            path.strip()
            for path in self._run(["diff", "--cached", "--name-only"]).splitlines()
            if path.strip()
        }
        unrelated = sorted(staged.difference(normalized_paths))
        if unrelated:
            raise GitVersionStoreError(
                "Refusing governance commit while unrelated staged paths exist: "
                + ", ".join(unrelated)
            )

    @staticmethod
    def _normalize_repo_path(path: str | Path) -> str:
        raw = str(path).replace("\\", "/").strip()
        if not raw:
            raise ValueError("Git path cannot be empty.")
        parts = [part for part in raw.split("/") if part not in ("", ".")]
        if Path(raw).is_absolute() or ".." in parts:
            raise ValueError("Git path must be repo-relative and stay inside the repository.")
        return "/".join(parts)

    @staticmethod
    def _normalize_branch_name(branch_name: str) -> str:
        branch = branch_name.strip().replace("\\", "/")
        if not branch:
            raise ValueError("Git branch name cannot be empty.")
        parts = [part for part in branch.split("/") if part]
        if ".." in branch or branch.startswith("/") or branch.endswith("/") or any(part == "." for part in parts):
            raise ValueError(f"Invalid Git branch name: {branch_name!r}")
        return "/".join(parts)

    @staticmethod
    def _normalize_tag_name(tag_name: str) -> str:
        tag = tag_name.strip().replace("\\", "/")
        if not tag:
            raise ValueError("Git tag name cannot be empty.")
        parts = [part for part in tag.split("/") if part]
        if ".." in tag or tag.startswith("/") or tag.endswith("/") or any(part == "." for part in parts):
            raise ValueError(f"Invalid Git tag name: {tag_name!r}")
        return "/".join(parts)

    @staticmethod
    def _parse_history(output: str, marker: str) -> List[GitCommit]:
        commits: List[GitCommit] = []
        current: Optional[GitCommit] = None
        changed_paths: List[str] = []

        def flush() -> None:
            nonlocal current, changed_paths
            if current is None:
                return
            commits.append(
                GitCommit(
                    commit_hash=current.commit_hash,
                    author=current.author,
                    authored_at=current.authored_at,
                    subject=current.subject,
                    changed_paths=tuple(changed_paths),
                )
            )
            current = None
            changed_paths = []

        for line in output.splitlines():
            if line.startswith(marker):
                flush()
                parts = line.split("\x1f", 4)
                if len(parts) != 5:
                    continue
                current = GitCommit(
                    commit_hash=parts[1],
                    author=parts[2],
                    authored_at=parts[3],
                    subject=parts[4],
                )
            elif current is not None and line.strip():
                changed_paths.append(line.strip())

        flush()
        return commits
