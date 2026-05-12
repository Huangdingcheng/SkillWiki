"""Git-backed version store for Skill governance.

Git remains the source of truth for branches, commits, history, and diffs.
SkillOS adds Skill-level meaning above this adapter in later stages.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


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
        self._run(["branch", branch, start_point])

    def checkout(self, branch_name: str) -> None:
        """Checkout an existing local branch."""
        self._require_repo()
        branch = self._normalize_branch_name(branch_name)
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
        author_name: str = "SkillOS",
        author_email: str = "skillos@example.local",
    ) -> str:
        """Commit selected repo-relative paths and return the new HEAD hash."""
        self._require_repo()
        normalized_paths = [self._normalize_repo_path(path) for path in paths]
        if not normalized_paths:
            raise ValueError("At least one path is required to create a commit.")
        if not message.strip():
            raise ValueError("Commit message cannot be empty.")

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

    def _require_repo(self) -> None:
        if not self.is_git_repo():
            raise GitVersionStoreError(f"{self.repo_path} is not a Git repository.")

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
