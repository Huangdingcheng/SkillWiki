"""Git-backed Skill storage common utilities.

Path suggestion:
    skillos/skillos/storage/skill_repo/common.py

Run from project package root, for example:
    cd /Users/liubingshuo/Desktop/code/py/skill/skillos/skillos
    python -m skillos.storage.skill_repo.common init --reset
    python -m skillos.storage.skill_repo.common seed
    python -m skillos.storage.skill_repo.common list

Repository layout:
    skillos/storage/skill_repo/SkillStorage/
    ├── .git/
    ├── README.md
    ├── skill_repo_config.json
    ├── skills/
    │   └── {skill_name}/
    │       ├── versions.json
    │       ├── 1.0.0.json
    │       ├── 1.0.1.json
    │       └── ...
    └── metadata/
        ├── skills_index.json
        └── events.jsonl

Design:
- Local Git repository is the server-side source of truth.
- Remote Git is only backup/sync storage.
- One skill = one directory.
- One version = one immutable JSON file by default.
- updates to an existing version require overwrite=True.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from ...models.skill_model import (
    MetaSkillCategory,
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillMetrics,
    SkillProvenance,
    SkillState,
    SkillType,
)


BASE_DIR = Path(os.getenv("SKILLOS_SKILL_STORAGE_DIR", "skillos/storage/skill_repo/SkillStorage"))

SKILLS_DIR = BASE_DIR / "skills"
METADATA_DIR = BASE_DIR / "metadata"
INDEX_FILE = METADATA_DIR / "skills_index.json"
EVENTS_FILE = METADATA_DIR / "events.jsonl"
CONFIG_FILE = BASE_DIR / "skill_repo_config.json"
README_FILE = BASE_DIR / "README.md"

VERSION_MANIFEST = "versions.json"

VersionBump = Literal["major", "minor", "patch"]
MergeStrategy = Literal["prefer_other", "prefer_base", "append_lists"]


DEFAULT_CONFIG: Dict[str, Any] = {
    "repo_name": "SkillStorage",
    "local_repo_path": str(BASE_DIR),
    "default_branch": "main",
    "remote_name": "origin",
    "remote_url": "",
    "git_user_name": "SkillOS Bot",
    "git_user_email": "skillos-bot@example.com",
    "auto_commit": True,
    "auto_push": False,
    "sync_interval_seconds": 3600,
    "storage_layout_version": 1,
}


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, data: Any) -> None:
    _ensure_parent(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")


def _append_event(event: Dict[str, Any]) -> None:
    _ensure_parent(EVENTS_FILE)
    event.setdefault("event_id", str(uuid.uuid4()))
    event.setdefault("time", _utc_now())
    with EVENTS_FILE.open("a", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False, default=str)
        f.write("\n")


def _run_git(
    args: List[str],
    *,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        check=check,
        capture_output=capture,
        text=True,
    )


def _is_git_repo() -> bool:
    return (BASE_DIR / ".git").exists()


def _has_git_changes() -> bool:
    if not _is_git_repo():
        return False
    result = _run_git(["status", "--porcelain"], check=False, capture=True)
    return bool(result.stdout.strip())


def _git_commit(message: str, allow_empty: bool = False) -> bool:
    if not _is_git_repo():
        return False

    _run_git(["add", "."], check=True)

    if not allow_empty and not _has_git_changes():
        return False

    result = _run_git(["commit", "-m", message], check=False, capture=True)
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        if "nothing to commit" in output.lower():
            return False
        raise RuntimeError(output)

    return True


def _parse_semver(version: str) -> Tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"非法版本号: {version!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _semver_key(version: str) -> Tuple[int, int, int]:
    return _parse_semver(version)


def _next_version(version: str, bump: VersionBump = "patch") -> str:
    major, minor, patch = _parse_semver(version)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _skill_dir(skill_name: str) -> Path:
    return SKILLS_DIR / skill_name


def _skill_file(skill_name: str, version: str) -> Path:
    return _skill_dir(skill_name) / f"{version}.json"


def _manifest_file(skill_name: str) -> Path:
    return _skill_dir(skill_name) / VERSION_MANIFEST


def _skill_to_dict(skill: Skill) -> Dict[str, Any]:
    if hasattr(skill, "model_dump"):
        return skill.model_dump(mode="json")
    return skill.dict()


def _dict_to_skill(data: Dict[str, Any]) -> Skill:
    if hasattr(Skill, "model_validate"):
        return Skill.model_validate(data)
    return Skill.parse_obj(data)


def _state_value(state: SkillState | str) -> str:
    return state.value if hasattr(state, "value") else str(state)


def _skill_type_value(skill_type: SkillType | str) -> str:
    return skill_type.value if hasattr(skill_type, "value") else str(skill_type)


def _latest_version_from_versions_dict(versions: Dict[str, Any]) -> Optional[str]:
    valid = [v for v, meta in versions.items() if not meta.get("deleted", False)]
    if not valid:
        return None
    return sorted(valid, key=_semver_key)[-1]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    config.update(_read_json(CONFIG_FILE, {}))
    return config


def save_config(config: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    merged["local_repo_path"] = str(BASE_DIR)
    _write_json(CONFIG_FILE, merged)
    return merged


# ---------------------------------------------------------------------------
# Manifest / index
# ---------------------------------------------------------------------------

def _load_manifest(skill_name: str) -> Dict[str, Any]:
    return _read_json(
        _manifest_file(skill_name),
        {
            "name": skill_name,
            "latest_version": None,
            "versions": {},
            "deleted": False,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        },
    )


def _save_manifest(skill_name: str, manifest: Dict[str, Any]) -> None:
    manifest["name"] = skill_name
    manifest["updated_at"] = _utc_now()
    _write_json(_manifest_file(skill_name), manifest)


def _load_index() -> Dict[str, Any]:
    return _read_json(INDEX_FILE, {})


def _save_index(index: Dict[str, Any]) -> None:
    _write_json(INDEX_FILE, index)


def _version_meta_from_skill(skill: Skill, source: str, old_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    old_meta = old_meta or {}
    return {
        "version": skill.version,
        "skill_id": skill.skill_id,
        "state": _state_value(skill.state),
        "skill_type": _skill_type_value(skill.skill_type),
        "domain": skill.domain,
        "display_name": skill.display_name,
        "description": skill.description,
        "tags": skill.tags,
        "file": f"{skill.version}.json",
        "deleted": False,
        "created_at": old_meta.get("created_at", _utc_now()),
        "updated_at": _utc_now(),
        "source": source,
    }


def _rebuild_index() -> Dict[str, Any]:
    index: Dict[str, Any] = {}
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted([p for p in SKILLS_DIR.iterdir() if p.is_dir()]):
        skill_name = skill_dir.name
        manifest = _load_manifest(skill_name)
        versions = manifest.setdefault("versions", {})

        for file_path in sorted(skill_dir.glob("*.json")):
            if file_path.name == VERSION_MANIFEST:
                continue

            version = file_path.stem
            data = _read_json(file_path, {})
            if not data:
                continue

            old_meta = versions.get(version, {})
            versions[version] = {
                "version": version,
                "skill_id": data.get("skill_id"),
                "state": data.get("state"),
                "skill_type": data.get("skill_type"),
                "domain": data.get("domain"),
                "display_name": data.get("display_name"),
                "description": data.get("description"),
                "tags": data.get("tags", []),
                "file": file_path.name,
                "deleted": old_meta.get("deleted", False),
                "created_at": old_meta.get("created_at", data.get("created_at", _utc_now())),
                "updated_at": data.get("updated_at", old_meta.get("updated_at", _utc_now())),
                "source": old_meta.get("source", "file_scan"),
            }

        manifest["versions"] = versions
        manifest["latest_version"] = _latest_version_from_versions_dict(versions)
        manifest.setdefault("deleted", False)
        if manifest["latest_version"] is not None:
            manifest["deleted"] = False
        _save_manifest(skill_name, manifest)

        latest_version = manifest.get("latest_version")
        latest_meta = versions.get(latest_version, {}) if latest_version else {}
        deleted = manifest.get("deleted", False) or latest_version is None

        index[skill_name] = {
            "name": skill_name,
            "latest_version": latest_version,
            "deleted": deleted,
            "state": latest_meta.get("state"),
            "skill_id": latest_meta.get("skill_id"),
            "skill_type": latest_meta.get("skill_type"),
            "domain": latest_meta.get("domain"),
            "display_name": latest_meta.get("display_name"),
            "description": latest_meta.get("description"),
            "tags": latest_meta.get("tags", []),
            "version_count": len([v for v, m in versions.items() if not m.get("deleted", False)]),
            "versions": versions,
            "updated_at": manifest.get("updated_at", _utc_now()),
        }

    _save_index(index)
    return index


def rebuild_index(*, commit: bool = True) -> Dict[str, Any]:
    init_repo(initial_commit=False)
    index = _rebuild_index()
    _append_event({"action": "rebuild_index", "skill_count": len(index)})
    if commit:
        _git_commit("Rebuild SkillStorage index")
    return index


# ---------------------------------------------------------------------------
# Repository init / status / reset
# ---------------------------------------------------------------------------

def init_repo(
    *,
    remote_url: Optional[str] = None,
    default_branch: str = "main",
    git_user_name: Optional[str] = None,
    git_user_email: Optional[str] = None,
    initial_commit: bool = True,
    reset: bool = False,
) -> None:
    """Initialize local SkillStorage repository.

    reset=True removes the whole SkillStorage directory first.
    Use it only for tests or local reinitialization.
    """
    if reset and BASE_DIR.exists():
        shutil.rmtree(BASE_DIR)

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config()
    config["default_branch"] = default_branch or config.get("default_branch", "main")
    if remote_url is not None:
        config["remote_url"] = remote_url
    if git_user_name:
        config["git_user_name"] = git_user_name
    if git_user_email:
        config["git_user_email"] = git_user_email
    save_config(config)

    if not _is_git_repo():
        _run_git(["init"], check=True)
        _run_git(["checkout", "-B", config["default_branch"]], check=False)

    if config.get("git_user_name"):
        _run_git(["config", "user.name", config["git_user_name"]], check=False)
    if config.get("git_user_email"):
        _run_git(["config", "user.email", config["git_user_email"]], check=False)

    if config.get("remote_url"):
        remote_name = config.get("remote_name", "origin")
        remotes = _run_git(["remote"], check=False, capture=True).stdout.splitlines()
        if remote_name in remotes:
            _run_git(["remote", "set-url", remote_name, config["remote_url"]], check=False)
        else:
            _run_git(["remote", "add", remote_name, config["remote_url"]], check=False)

    if not INDEX_FILE.exists():
        _write_json(INDEX_FILE, {})

    if not EVENTS_FILE.exists():
        EVENTS_FILE.touch()

    if not README_FILE.exists():
        README_FILE.write_text(
            "# SkillStorage\n\n"
            "Git-backed Skill storage repository for SkillOS.\n\n"
            "## Layout\n\n"
            "- `skills/{skill_name}/{version}.json`: skill version file\n"
            "- `skills/{skill_name}/versions.json`: per-skill version manifest\n"
            "- `metadata/skills_index.json`: global index\n"
            "- `metadata/events.jsonl`: lifecycle event log\n",
            encoding="utf-8",
        )

    if initial_commit:
        _append_event({"action": "init_repo", "reset": reset})
        _git_commit("Initialize SkillStorage repository")


def repo_status() -> Dict[str, Any]:
    init_repo(initial_commit=False)
    config = load_config()
    git_status = _run_git(["status", "--short"], check=False, capture=True).stdout
    branch = _run_git(["branch", "--show-current"], check=False, capture=True).stdout.strip()
    return {
        "base_dir": str(BASE_DIR),
        "is_git_repo": _is_git_repo(),
        "branch": branch,
        "remote_name": config.get("remote_name"),
        "remote_url": config.get("remote_url"),
        "dirty": bool(git_status.strip()),
        "status": git_status,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_skill(
    skill: Skill,
    *,
    author: str = "system",
    commit: bool = True,
    overwrite: bool = False,
    event_action: str = "add",
) -> Skill:
    """Add a normal skill version.

    Normal add behavior:
    - if skill name does not exist, create skills/{name}/
    - if version does not exist, create {version}.json
    - if same name+version already exists, raise unless overwrite=True
    """
    init_repo(initial_commit=False)

    skill_dir = _skill_dir(skill.name)
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_file = _skill_file(skill.name, skill.version)
    if skill_file.exists() and not overwrite:
        raise ValueError(f"Skill 已存在，不能重复 add: {skill.name} v{skill.version}")

    skill.updated_at = datetime.utcnow()
    _write_json(skill_file, _skill_to_dict(skill))

    manifest = _load_manifest(skill.name)
    manifest["deleted"] = False
    manifest.setdefault("versions", {})
    old_meta = manifest["versions"].get(skill.version)
    manifest["versions"][skill.version] = _version_meta_from_skill(skill, event_action, old_meta)
    manifest["latest_version"] = _latest_version_from_versions_dict(manifest["versions"])
    _save_manifest(skill.name, manifest)

    _rebuild_index()

    _append_event(
        {
            "action": event_action,
            "skill": skill.name,
            "version": skill.version,
            "skill_id": skill.skill_id,
            "state": _state_value(skill.state),
            "author": author,
            "file": str(skill_file.relative_to(BASE_DIR)),
            "overwrite": overwrite,
        }
    )

    if commit:
        _git_commit(f"{event_action}: {skill.name} v{skill.version}")

    return skill


def update_skill_version(
    skill: Skill,
    *,
    author: str = "system",
    commit: bool = True,
) -> Skill:
    """Update existing name+version file. This is not a new version."""
    return add_skill(skill, author=author, commit=commit, overwrite=True, event_action="update")


def get_skill_versions(
    skill_name: str,
    *,
    include_deleted: bool = False,
) -> List[str]:
    init_repo(initial_commit=False)
    manifest = _load_manifest(skill_name)
    versions = manifest.get("versions", {})

    result = [
        version
        for version, meta in versions.items()
        if include_deleted or not meta.get("deleted", False)
    ]

    if not result and _skill_dir(skill_name).exists():
        result = [
            p.stem
            for p in _skill_dir(skill_name).glob("*.json")
            if p.name != VERSION_MANIFEST
        ]

    return sorted(result, key=_semver_key)


def get_skill(
    skill_name: str,
    version: Optional[str] = None,
    *,
    include_deleted: bool = False,
) -> Optional[Skill]:
    init_repo(initial_commit=False)

    versions = get_skill_versions(skill_name, include_deleted=include_deleted)
    if not versions:
        return None

    target_version = version or versions[-1]

    manifest = _load_manifest(skill_name)
    meta = manifest.get("versions", {}).get(target_version)
    if meta and meta.get("deleted", False) and not include_deleted:
        return None

    skill_file = _skill_file(skill_name, target_version)
    if not skill_file.exists():
        return None

    data = _read_json(skill_file, None)
    if data is None:
        return None

    return _dict_to_skill(data)


def get_skill_by_id(skill_id: str, *, include_deleted: bool = False) -> Optional[Skill]:
    for row in list_skills(include_deleted=include_deleted, latest_only=False, limit=100000):
        if row.get("skill_id") == skill_id:
            return get_skill(row["name"], row["version"], include_deleted=include_deleted)
    return None


def list_skills(
    *,
    state: Optional[SkillState | str] = None,
    skill_type: Optional[SkillType | str] = None,
    domain: Optional[str] = None,
    name_like: Optional[str] = None,
    latest_only: bool = True,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    init_repo(initial_commit=False)
    _rebuild_index()

    state_value = _state_value(state) if state is not None else None
    skill_type_value = _skill_type_value(skill_type) if skill_type is not None else None
    index = _load_index()
    rows: List[Dict[str, Any]] = []

    for skill_name, skill_meta in index.items():
        if skill_meta.get("deleted", False) and not include_deleted:
            continue

        if latest_only:
            version = skill_meta.get("latest_version")
            if not version:
                continue
            versions = {version: skill_meta.get("versions", {}).get(version, {})}
        else:
            versions = skill_meta.get("versions", {})

        for version, version_meta in versions.items():
            if version_meta.get("deleted", False) and not include_deleted:
                continue

            row = {
                "name": skill_name,
                "version": version,
                **skill_meta,
                **version_meta,
            }

            if state_value and row.get("state") != state_value:
                continue
            if skill_type_value and row.get("skill_type") != skill_type_value:
                continue
            if domain and row.get("domain") != domain:
                continue
            if name_like and name_like not in skill_name:
                continue

            rows.append(row)

    rows.sort(key=lambda r: (r.get("name", ""), _semver_key(r.get("version", "0.0.0"))))
    return rows[offset : offset + limit]


def delete_skill(
    skill_name: str,
    version: Optional[str] = None,
    *,
    hard: bool = False,
    author: str = "system",
    reason: Optional[str] = None,
    commit: bool = True,
) -> bool:
    """Delete skill.

    Default is soft delete.
    hard=True physically removes files.
    """
    init_repo(initial_commit=False)

    skill_dir = _skill_dir(skill_name)
    if not skill_dir.exists():
        return False

    manifest = _load_manifest(skill_name)
    versions = manifest.get("versions", {})

    if version:
        if version not in versions and not _skill_file(skill_name, version).exists():
            return False

        if hard:
            file_path = _skill_file(skill_name, version)
            if file_path.exists():
                file_path.unlink()
            versions.pop(version, None)
        else:
            versions.setdefault(version, {})
            versions[version]["deleted"] = True
            versions[version]["deleted_at"] = _utc_now()
            versions[version]["delete_reason"] = reason

        manifest["versions"] = versions
        manifest["latest_version"] = _latest_version_from_versions_dict(versions)
        manifest["deleted"] = manifest["latest_version"] is None
        _save_manifest(skill_name, manifest)

    else:
        if hard:
            shutil.rmtree(skill_dir)
        else:
            manifest["deleted"] = True
            manifest["deleted_at"] = _utc_now()
            manifest["delete_reason"] = reason
            for item in versions.values():
                item["deleted"] = True
                item["deleted_at"] = _utc_now()
                item["delete_reason"] = reason
            manifest["versions"] = versions
            manifest["latest_version"] = None
            _save_manifest(skill_name, manifest)

    _rebuild_index()

    _append_event(
        {
            "action": "delete",
            "skill": skill_name,
            "version": version,
            "hard": hard,
            "author": author,
            "reason": reason,
        }
    )

    if commit:
        _git_commit(f"delete: {skill_name} {version or 'all'}")

    return True


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------

def create_new_version(
    skill_name: str,
    source_version: Optional[str] = None,
    *,
    bump: VersionBump = "patch",
    overrides: Optional[Dict[str, Any]] = None,
    author: str = "system",
    commit: bool = True,
) -> Skill:
    source = get_skill(skill_name, source_version)
    if not source:
        raise ValueError(f"源 Skill 不存在: {skill_name} v{source_version or '<latest>'}")

    new_skill = source.model_copy(deep=True)
    new_skill.skill_id = str(uuid.uuid4())
    new_skill.version = _next_version(source.version, bump)
    new_skill.state = SkillState.DRAFT
    new_skill.created_at = datetime.utcnow()
    new_skill.updated_at = datetime.utcnow()
    new_skill.released_at = None
    new_skill.deprecated_at = None
    new_skill.metrics = SkillMetrics()

    if source.provenance:
        parent_ids = set(source.provenance.parent_skill_ids)
        parent_ids.add(source.skill_id)
        new_skill.provenance.parent_skill_ids = list(parent_ids)
        new_skill.provenance.source_type = "adapt"
        new_skill.provenance.creation_context.update({"source_version": source.version})
    else:
        new_skill.provenance = SkillProvenance(
            source_type="adapt",
            parent_skill_ids=[source.skill_id],
            creation_context={"source_version": source.version},
        )

    for key, value in (overrides or {}).items():
        setattr(new_skill, key, value)

    return add_skill(new_skill, author=author, commit=commit, event_action="new_version")


def get_version_history(skill_name: str, *, include_deleted: bool = False) -> List[Skill]:
    history: List[Skill] = []
    for version in get_skill_versions(skill_name, include_deleted=include_deleted):
        skill = get_skill(skill_name, version, include_deleted=include_deleted)
        if skill:
            history.append(skill)
    return history


def transition_skill_state(
    skill_name: str,
    version: str,
    new_state: SkillState,
    *,
    author: str = "system",
    reason: Optional[str] = None,
    commit: bool = True,
) -> Skill:
    skill = get_skill(skill_name, version)
    if not skill:
        raise ValueError(f"Skill 不存在: {skill_name} v{version}")

    old_state = skill.state
    skill.transition_to(new_state)

    if new_state == SkillState.DEPRECATED and reason:
        skill.deprecation_reason = reason

    updated = update_skill_version(skill, author=author, commit=False)

    _append_event(
        {
            "action": "transition",
            "skill": skill_name,
            "version": version,
            "from_state": _state_value(old_state),
            "to_state": _state_value(new_state),
            "author": author,
            "reason": reason,
        }
    )

    if commit:
        _git_commit(f"transition: {skill_name} v{version} {_state_value(old_state)}->{_state_value(new_state)}")

    return updated


# ---------------------------------------------------------------------------
# Diff / history
# ---------------------------------------------------------------------------

def diff_versions(
    skill_name: str,
    v1: str,
    v2: str,
    *,
    use_git: bool = True,
    context_lines: int = 3,
) -> str:
    init_repo(initial_commit=False)

    file1 = _skill_file(skill_name, v1)
    file2 = _skill_file(skill_name, v2)

    if not file1.exists() or not file2.exists():
        raise FileNotFoundError(f"Skill version not found: {skill_name} {v1} or {v2}")

    if use_git:
        result = _run_git(
            [
                "diff",
                "--no-index",
                "--",
                str(file1.relative_to(BASE_DIR)),
                str(file2.relative_to(BASE_DIR)),
            ],
            check=False,
            capture=True,
        )
        if result.returncode in (0, 1):
            return result.stdout
        raise RuntimeError(result.stderr or result.stdout)

    a = file1.read_text(encoding="utf-8").splitlines(keepends=True)
    b = file2.read_text(encoding="utf-8").splitlines(keepends=True)

    return "".join(
        difflib.unified_diff(
            a,
            b,
            fromfile=f"{skill_name}/{v1}.json",
            tofile=f"{skill_name}/{v2}.json",
            n=context_lines,
        )
    )


def git_file_history(
    skill_name: str,
    version: Optional[str] = None,
    *,
    max_count: int = 20,
) -> str:
    init_repo(initial_commit=False)

    path = _skill_dir(skill_name)
    if version:
        path = _skill_file(skill_name, version)

    result = _run_git(
        [
            "log",
            f"--max-count={max_count}",
            "--oneline",
            "--",
            str(path.relative_to(BASE_DIR)),
        ],
        check=False,
        capture=True,
    )

    return result.stdout


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def _merge_values(base_value: Any, other_value: Any, strategy: MergeStrategy) -> Any:
    if strategy == "prefer_base":
        return base_value if base_value not in (None, [], {}) else other_value

    if strategy == "append_lists" and isinstance(base_value, list) and isinstance(other_value, list):
        result = list(base_value)
        for item in other_value:
            if item not in result:
                result.append(item)
        return result

    return other_value if other_value not in (None, [], {}) else base_value


def merge_skills(
    skill_name: str,
    base_version: str,
    other_version: str,
    new_version: Optional[str] = None,
    *,
    strategy: MergeStrategy = "prefer_other",
    manual_overrides: Optional[Dict[str, Any]] = None,
    author: str = "system",
    commit: bool = True,
) -> Skill:
    base = get_skill(skill_name, base_version)
    other = get_skill(skill_name, other_version)

    if not base or not other:
        raise ValueError(f"待合并 Skill 不存在: {skill_name} {base_version}, {other_version}")

    base_data = _skill_to_dict(base)
    other_data = _skill_to_dict(other)

    merged_data = dict(base_data)

    for key, other_value in other_data.items():
        if key in {
            "skill_id",
            "version",
            "created_at",
            "updated_at",
            "released_at",
            "deprecated_at",
        }:
            continue
        merged_data[key] = _merge_values(merged_data.get(key), other_value, strategy)

    if manual_overrides:
        merged_data.update(manual_overrides)

    max_source_version = max(base.version, other.version, key=_semver_key)

    merged_data["skill_id"] = str(uuid.uuid4())
    merged_data["version"] = new_version or _next_version(max_source_version, "patch")
    merged_data["state"] = SkillState.DRAFT.value
    merged_data["created_at"] = _utc_now()
    merged_data["updated_at"] = _utc_now()
    merged_data["released_at"] = None
    merged_data["deprecated_at"] = None

    merged_data["provenance"] = merged_data.get("provenance") or {}
    merged_data["provenance"].update(
        {
            "source_type": "merge",
            "parent_skill_ids": [base.skill_id, other.skill_id],
            "creation_context": {
                "base_version": base.version,
                "other_version": other.version,
                "strategy": strategy,
            },
        }
    )

    merged = _dict_to_skill(merged_data)
    added = add_skill(merged, author=author, commit=False, event_action="merge")

    _append_event(
        {
            "action": "merge",
            "skill": skill_name,
            "base_version": base_version,
            "other_version": other_version,
            "new_version": added.version,
            "strategy": strategy,
            "author": author,
        }
    )

    if commit:
        _git_commit(f"merge: {skill_name} {base_version}+{other_version} -> {added.version}")

    return added


# ---------------------------------------------------------------------------
# Remote sync
# ---------------------------------------------------------------------------

def pull_from_remote(
    *,
    remote_name: Optional[str] = None,
    branch: Optional[str] = None,
    rebase: bool = True,
) -> str:
    init_repo(initial_commit=False)

    config = load_config()
    remote = remote_name or config.get("remote_name", "origin")
    target_branch = branch or config.get("default_branch", "main")

    args = ["pull", remote, target_branch]
    if rebase:
        args.insert(1, "--rebase")

    result = _run_git(args, check=False, capture=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    _append_event({"action": "pull", "remote": remote, "branch": target_branch})
    _git_commit("Record remote pull event")
    return result.stdout + result.stderr


def push_to_remote(
    remote_name: Optional[str] = None,
    branch: Optional[str] = None,
    *,
    set_upstream: bool = True,
) -> str:
    init_repo(initial_commit=False)

    config = load_config()
    remote = remote_name or config.get("remote_name", "origin")
    target_branch = branch or config.get("default_branch", "main")

    if not config.get("remote_url"):
        raise ValueError("remote_url 为空，请先在 skill_repo_config.json 中配置远程仓库地址")

    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += [remote, target_branch]

    result = _run_git(args, check=False, capture=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    _append_event({"action": "push", "remote": remote, "branch": target_branch})
    _git_commit("Record remote push event")
    return result.stdout + result.stderr


def sync_to_remote() -> str:
    init_repo(initial_commit=False)
    _git_commit("Sync pending SkillStorage changes")
    return push_to_remote()


# ---------------------------------------------------------------------------
# Events / sample data
# ---------------------------------------------------------------------------

def read_events(limit: int = 100) -> List[Dict[str, Any]]:
    if not EVENTS_FILE.exists():
        return []

    lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
    selected = lines[-limit:]
    events: List[Dict[str, Any]] = []

    for line in selected:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return events


def create_sample_skills(*, commit: bool = True) -> List[Skill]:
    """Create five sample skills for local testing."""

    samples = [
        Skill(
            name="click_element",
            version="1.0.0",
            description="点击页面上的指定元素",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            state=SkillState.RELEASED,
            tags=["web", "click", "interaction"],
            interface=SkillInterface(
                input_schema={
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器或 XPath"},
                        "timeout_ms": {"type": "integer", "default": 5000},
                    },
                    "required": ["selector"],
                },
                output_schema={"type": "object", "properties": {"clicked": {"type": "boolean"}}},
                preconditions=["目标元素在页面上可见且可交互"],
                postconditions=["元素已被点击，触发相应事件"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='await page.click(input_data["selector"])',
                tool_calls=["playwright"],
            ),
        ),
        Skill(
            name="type_text",
            version="1.0.0",
            description="在输入框中输入文本",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            state=SkillState.RELEASED,
            tags=["web", "input", "text"],
            interface=SkillInterface(
                input_schema={
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "text": {"type": "string"},
                        "clear_first": {"type": "boolean", "default": True},
                    },
                    "required": ["selector", "text"],
                },
                output_schema={"type": "object", "properties": {"typed": {"type": "boolean"}}},
                preconditions=["目标输入框存在且可编辑"],
                postconditions=["文本已输入到指定输入框"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='await page.fill(input_data["selector"], input_data["text"])',
                tool_calls=["playwright"],
            ),
        ),
        Skill(
            name="locate_element",
            version="1.0.0",
            description="在页面上定位指定元素，返回元素信息",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            state=SkillState.RELEASED,
            tags=["web", "locate", "dom"],
            interface=SkillInterface(
                input_schema={
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "元素的自然语言描述"},
                        "selector_hint": {"type": "string"},
                    },
                    "required": ["description"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "found": {"type": "boolean"},
                        "element_type": {"type": "string"},
                    },
                },
                preconditions=["页面已加载完成"],
                postconditions=["返回元素的 CSS 选择器"],
            ),
            implementation=SkillImplementation(
                language="python",
                prompt_template="在页面上找到描述为 '{description}' 的元素，返回其 CSS 选择器。",
            ),
        ),
        Skill(
            name="fill_form",
            version="1.0.0",
            description="填写页面上的结构化表单",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            granularity_level=2,
            state=SkillState.RELEASED,
            tags=["web", "form", "input", "functional"],
            interface=SkillInterface(
                input_schema={
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "object",
                            "description": "字段名到值的映射",
                            "additionalProperties": {"type": "string"},
                        },
                        "submit": {"type": "boolean", "default": False},
                    },
                    "required": ["fields"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "filled_count": {"type": "integer"},
                        "submitted": {"type": "boolean"},
                    },
                },
                preconditions=["页面上存在可编辑的表单字段"],
                postconditions=["所有指定字段已填写完毕"],
                side_effects=["如果 submit=true，表单将被提交"],
            ),
            implementation=SkillImplementation(
                language="python",
                sub_skill_ids=[],
                prompt_template="对表单中的每个字段 {fields}，先定位字段，再输入对应值。",
            ),
        ),
        Skill(
            name="skill_lifecycle_manager",
            version="1.0.0",
            description="管理 Skill 的完整生命周期，包括创建、验证、发布和废弃",
            skill_type=SkillType.STRATEGIC,
            meta_category=MetaSkillCategory.LIFECYCLE,
            domain="skillos",
            granularity_level=4,
            state=SkillState.RELEASED,
            tags=["meta", "lifecycle", "management"],
            interface=SkillInterface(
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "validate", "release", "deprecate", "archive"],
                        },
                        "skill_id": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["action"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "new_state": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
                preconditions=["目标 Skill 存在于 SkillOS 中"],
                postconditions=["Skill 状态已按照生命周期规则转换"],
            ),
            implementation=SkillImplementation(
                language="python",
                prompt_template="执行 Skill 生命周期操作 {action}，遵循状态机规则。",
            ),
        ),
    ]

    inserted: List[Skill] = []

    for skill in samples:
        existing = get_skill(skill.name, skill.version, include_deleted=True)
        if existing:
            inserted.append(existing)
            continue

        inserted.append(
            add_skill(
                skill,
                author="seed",
                commit=False,
                event_action="seed",
            )
        )

    if commit:
        _git_commit("Seed sample skills")

    return inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _json_print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Git-backed SkillStorage common utility")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--reset", action="store_true")
    p_init.add_argument("--remote-url", default=None)
    p_init.add_argument("--branch", default="main")

    sub.add_parser("status")
    sub.add_parser("seed")
    sub.add_parser("rebuild-index")
    sub.add_parser("events")

    p_list = sub.add_parser("list")
    p_list.add_argument("--all-versions", action="store_true")
    p_list.add_argument("--include-deleted", action="store_true")
    p_list.add_argument("--state")
    p_list.add_argument("--type")
    p_list.add_argument("--domain")
    p_list.add_argument("--name-like")
    p_list.add_argument("--limit", type=int, default=100)
    p_list.add_argument("--offset", type=int, default=0)

    p_get = sub.add_parser("get")
    p_get.add_argument("name")
    p_get.add_argument("--version")
    p_get.add_argument("--include-deleted", action="store_true")

    p_versions = sub.add_parser("versions")
    p_versions.add_argument("name")
    p_versions.add_argument("--include-deleted", action="store_true")

    p_history = sub.add_parser("history")
    p_history.add_argument("name")
    p_history.add_argument("--version")
    p_history.add_argument("--max-count", type=int, default=20)

    p_diff = sub.add_parser("diff")
    p_diff.add_argument("name")
    p_diff.add_argument("v1")
    p_diff.add_argument("v2")

    p_new = sub.add_parser("new-version")
    p_new.add_argument("name")
    p_new.add_argument("--source-version")
    p_new.add_argument("--bump", choices=["major", "minor", "patch"], default="patch")

    p_delete = sub.add_parser("delete")
    p_delete.add_argument("name")
    p_delete.add_argument("--version")
    p_delete.add_argument("--hard", action="store_true")
    p_delete.add_argument("--reason")

    p_merge = sub.add_parser("merge")
    p_merge.add_argument("name")
    p_merge.add_argument("base_version")
    p_merge.add_argument("other_version")
    p_merge.add_argument("--new-version")
    p_merge.add_argument("--strategy", choices=["prefer_other", "prefer_base", "append_lists"], default="prefer_other")

    p_transition = sub.add_parser("transition")
    p_transition.add_argument("name")
    p_transition.add_argument("version")
    p_transition.add_argument("state")

    p_push = sub.add_parser("push")
    p_push.add_argument("--remote")
    p_push.add_argument("--branch")

    p_pull = sub.add_parser("pull")
    p_pull.add_argument("--remote")
    p_pull.add_argument("--branch")
    p_pull.add_argument("--no-rebase", action="store_true")

    args = parser.parse_args()

    if args.cmd == "init":
        init_repo(reset=args.reset, remote_url=args.remote_url, default_branch=args.branch)
        _json_print(repo_status())

    elif args.cmd == "status":
        _json_print(repo_status())

    elif args.cmd == "seed":
        init_repo()
        skills = create_sample_skills()
        print(f"seeded {len(skills)} skills")

    elif args.cmd == "rebuild-index":
        _json_print(rebuild_index())

    elif args.cmd == "events":
        _json_print(read_events())

    elif args.cmd == "list":
        _json_print(
            list_skills(
                state=args.state,
                skill_type=args.type,
                domain=args.domain,
                name_like=args.name_like,
                latest_only=not args.all_versions,
                include_deleted=args.include_deleted,
                limit=args.limit,
                offset=args.offset,
            )
        )

    elif args.cmd == "get":
        skill = get_skill(args.name, args.version, include_deleted=args.include_deleted)
        _json_print(_skill_to_dict(skill) if skill else None)

    elif args.cmd == "versions":
        _json_print(get_skill_versions(args.name, include_deleted=args.include_deleted))

    elif args.cmd == "history":
        print(git_file_history(args.name, args.version, max_count=args.max_count))

    elif args.cmd == "diff":
        print(diff_versions(args.name, args.v1, args.v2))

    elif args.cmd == "new-version":
        skill = create_new_version(args.name, args.source_version, bump=args.bump)
        _json_print(_skill_to_dict(skill))

    elif args.cmd == "delete":
        ok = delete_skill(args.name, args.version, hard=args.hard, reason=args.reason)
        print("deleted" if ok else "not found")

    elif args.cmd == "merge":
        skill = merge_skills(
            args.name,
            args.base_version,
            args.other_version,
            new_version=args.new_version,
            strategy=args.strategy,
        )
        _json_print(_skill_to_dict(skill))

    elif args.cmd == "transition":
        state = SkillState(args.state)
        skill = transition_skill_state(args.name, args.version, state)
        _json_print(_skill_to_dict(skill))

    elif args.cmd == "push":
        print(push_to_remote(args.remote, args.branch))

    elif args.cmd == "pull":
        print(pull_from_remote(remote_name=args.remote, branch=args.branch, rebase=not args.no_rebase))
