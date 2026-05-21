"""Repository-backed Wiki Manager for SkillOS API.

This module adapts a local Git skill repository to the API layer.

Design:
- The FastAPI routes talk to app_state.wiki.
- app_state.wiki is RepositoryWikiManager.
- RepositoryWikiManager reads/writes Skill objects from/to a local git repository.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

import yaml

from ..models.skill_model import Skill

logger = logging.getLogger(__name__)


class RepositoryWikiManager:
    """
    Local-git-repository backed Wiki Manager.

    Public methods intentionally match the MemoryWikiManager-like interface used by routes:
    - create
    - get
    - list
    - update
    - delete
    - exists

    The repository may contain JSON/YAML skill files.
    New or updated skills are written as JSON under:

        <repo_root>/skills/<skill_id>.json
    """

    SKILL_FILE_SUFFIXES = {".json", ".yaml", ".yml"}

    IGNORED_DIR_NAMES = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
        ".idea",
        ".vscode",
    }

    def __init__(
        self,
        base_dir: Path,
        write_subdir: str = "skills",
        auto_git_add: bool = False,
        auto_git_commit: bool = False,
    ):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.write_dir = self.base_dir / write_subdir
        self.write_dir.mkdir(parents=True, exist_ok=True)

        self.auto_git_add = auto_git_add
        self.auto_git_commit = auto_git_commit

        logger.info("RepositoryWikiManager initialized: %s", self.base_dir)
        logger.info("Skill write directory: %s", self.write_dir)

    # -------------------------------------------------------------------------
    # Public API used by FastAPI routes
    # -------------------------------------------------------------------------

    async def create(self, skill: Skill) -> Skill:
        skill_id = self._get_skill_id(skill)

        existing = await self.get(skill_id)
        if existing is not None:
            raise ValueError(f"Skill 已存在: {skill_id}")

        path = self._default_write_path(skill_id)
        self._write_skill_file(path, skill)

        self._maybe_git_track(path, message=f"create skill: {skill_id}")

        logger.info("Skill 已创建到本地 git repository: %s -> %s", skill_id, path)
        return skill

    async def get(self, skill_id: str) -> Optional[Skill]:
        path = self._find_skill_path(skill_id)

        if path is None:
            return None

        return self._read_skill_file(path)

    async def list(
        self,
        state: Any = None,
        skill_type: Any = None,
        tags: Optional[list[str]] = None,
        visibility: Any = None,
        query: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[Skill]:
        skills: list[Skill] = []

        for path in self._iter_skill_files():
            try:
                skill = self._read_skill_file(path)
            except Exception as exc:
                logger.warning("跳过无法解析的 skill 文件: %s, error=%s", path, exc)
                continue

            if not self._matches_filters(
                skill=skill,
                state=state,
                skill_type=skill_type,
                tags=tags,
                visibility=visibility,
                query=query,
            ):
                continue

            skills.append(skill)

        skills.sort(key=lambda s: str(getattr(s, "name", "") or getattr(s, "id", "")))

        if offset < 0:
            offset = 0

        if limit is None or limit <= 0:
            return skills[offset:]

        return skills[offset : offset + limit]

    async def update(self, skill_id: str, skill: Skill) -> Skill:
        existing_path = self._find_skill_path(skill_id)

        if existing_path is None:
            path = self._default_write_path(skill_id)
        else:
            path = existing_path

        self._write_skill_file(path, skill)

        self._maybe_git_track(path, message=f"update skill: {skill_id}")

        logger.info("Skill 已更新到本地 git repository: %s -> %s", skill_id, path)
        return skill

    async def delete(self, skill_id: str) -> bool:
        path = self._find_skill_path(skill_id)

        if path is None:
            return False

        path.unlink()

        self._maybe_git_track(path, message=f"delete skill: {skill_id}", deleted=True)

        logger.info("Skill 已从本地 git repository 删除: %s -> %s", skill_id, path)
        return True

    async def exists(self, skill_id: str) -> bool:
        return self._find_skill_path(skill_id) is not None

    # -------------------------------------------------------------------------
    # File discovery
    # -------------------------------------------------------------------------

    def _iter_skill_files(self) -> list[Path]:
        files: list[Path] = []

        for path in self.base_dir.rglob("*"):
            if not path.is_file():
                continue

            if path.suffix.lower() not in self.SKILL_FILE_SUFFIXES:
                continue

            if self._is_ignored_path(path):
                continue

            files.append(path)

        return sorted(files)

    def _is_ignored_path(self, path: Path) -> bool:
        parts = set(path.relative_to(self.base_dir).parts)
        return bool(parts & self.IGNORED_DIR_NAMES)

    def _find_skill_path(self, skill_id: str) -> Optional[Path]:
        target = str(skill_id)

        # Fast path: default write location.
        default_path = self._default_write_path(target)
        if default_path.exists():
            return default_path

        # Full scan: supports existing git repo layouts.
        for path in self._iter_skill_files():
            try:
                skill = self._read_skill_file(path)
            except Exception:
                continue

            candidate_ids = self._candidate_skill_ids(skill)

            if target in candidate_ids:
                return path

        return None

    def _default_write_path(self, skill_id: str) -> Path:
        safe_id = self._safe_filename(skill_id)
        return self.write_dir / f"{safe_id}.json"

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def _read_skill_file(self, path: Path) -> Skill:
        raw = path.read_text(encoding="utf-8")

        if path.suffix.lower() == ".json":
            data = json.loads(raw)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(raw)
        else:
            raise ValueError(f"不支持的 skill 文件格式: {path}")

        if not isinstance(data, dict):
            raise ValueError(f"Skill 文件内容必须是 object/dict: {path}")

        data = self._normalize_skill_payload(data)

        return self._skill_from_dict(data)

    def _write_skill_file(self, path: Path, skill: Skill) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        data = self._skill_to_dict(skill)

        if path.suffix.lower() == ".json":
            text = json.dumps(data, ensure_ascii=False, indent=2)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        else:
            raise ValueError(f"不支持的写入格式: {path}")

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(path)

    def _normalize_skill_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize common repository layouts.

        Supported examples:

        1. Direct Skill object:
            {
              "name": "...",
              "description": "...",
              ...
            }

        2. Wrapped object:
            {
              "skill": {
                "name": "...",
                ...
              }
            }

        3. Metadata wrapper:
            {
              "metadata": {...},
              "spec": {
                "name": "...",
                ...
              }
            }

        If your local git repository uses a different schema, add conversion here.
        """

        if "skill" in data and isinstance(data["skill"], dict):
            return data["skill"]

        if "spec" in data and isinstance(data["spec"], dict):
            spec = dict(data["spec"])

            metadata = data.get("metadata")
            if isinstance(metadata, dict):
                for key in ("name", "description", "tags"):
                    if key not in spec and key in metadata:
                        spec[key] = metadata[key]

            return spec

        return data

    def _skill_to_dict(self, skill: Skill) -> dict[str, Any]:
        if hasattr(skill, "model_dump"):
            return skill.model_dump(mode="json")

        return skill.dict()

    def _skill_from_dict(self, data: dict[str, Any]) -> Skill:
        if hasattr(Skill, "model_validate"):
            return Skill.model_validate(data)

        return Skill.parse_obj(data)

    # -------------------------------------------------------------------------
    # Skill identity and filters
    # -------------------------------------------------------------------------

    def _get_skill_id(self, skill: Skill) -> str:
        for attr in ("id", "skill_id", "name"):
            value = getattr(skill, attr, None)
            if value:
                return str(value)

        raise ValueError("Skill 缺少 id / skill_id / name，无法生成 repository 文件名")

    def _candidate_skill_ids(self, skill: Skill) -> set[str]:
        ids: set[str] = set()

        for attr in ("id", "skill_id", "name"):
            value = getattr(skill, attr, None)
            if value:
                ids.add(str(value))

        return ids

    def _matches_filters(
        self,
        skill: Skill,
        state: Any = None,
        skill_type: Any = None,
        tags: Optional[list[str]] = None,
        visibility: Any = None,
        query: Optional[str] = None,
    ) -> bool:
        if state is not None:
            current_state = getattr(skill, "state", None)
            if not self._loosely_equal(current_state, state):
                return False

        if skill_type is not None:
            current_type = getattr(skill, "skill_type", None)
            if not self._loosely_equal(current_type, skill_type):
                return False

        if tags:
            skill_tags = set(getattr(skill, "tags", []) or [])
            if not set(tags).issubset(skill_tags):
                return False

        if visibility is not None and visibility != "all":
            current_visibility = getattr(skill, "visibility", None)
            if not self._loosely_equal(current_visibility, visibility):
                return False

        if query:
            q = query.lower()

            name = str(getattr(skill, "name", "") or "").lower()
            description = str(getattr(skill, "description", "") or "").lower()
            tag_text = " ".join(getattr(skill, "tags", []) or []).lower()

            if q not in name and q not in description and q not in tag_text:
                return False

        return True

    def _loosely_equal(self, current: Any, expected: Any) -> bool:
        if current == expected:
            return True

        if str(current) == str(expected):
            return True

        current_value = getattr(current, "value", None)
        expected_value = getattr(expected, "value", None)

        if current_value == expected:
            return True

        if current == expected_value:
            return True

        if current_value is not None and expected_value is not None:
            return current_value == expected_value

        return False

    def _safe_filename(self, value: str) -> str:
        safe = str(value).strip()
        safe = safe.replace("/", "_").replace("\\", "_")
        safe = safe.replace(":", "_")
        safe = safe.replace(" ", "_")
        return safe

    # -------------------------------------------------------------------------
    # Git integration
    # -------------------------------------------------------------------------

    def _is_git_repo(self) -> bool:
        return (self.base_dir / ".git").exists()

    def _run_git(self, args: list[str]) -> None:
        if not self._is_git_repo():
            return

        subprocess.run(
            ["git", *args],
            cwd=str(self.base_dir),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _maybe_git_track(self, path: Path, message: str, deleted: bool = False) -> None:
        if not self._is_git_repo():
            return

        rel_path = path.relative_to(self.base_dir)

        if self.auto_git_add:
            if deleted:
                self._run_git(["rm", "--ignore-unmatch", str(rel_path)])
            else:
                self._run_git(["add", str(rel_path)])

        if self.auto_git_commit:
            self._run_git(["commit", "-m", message])
