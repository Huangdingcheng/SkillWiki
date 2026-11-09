"""Real Git roots below MAX_PATH with version/manifest children above it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from skillwiki.models.skill_model import Skill, SkillImplementation, SkillState
from skillwiki.storage.skill_repo.common import GitSkillStore


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("root_length", [210, 225])
def test_unprefixed_store_root_preserves_long_file_roundtrip_and_version_operations(tmp_path, existing, root_length):
    root = tmp_path / "repo"
    root = root / ("r" * (root_length - len(str(root)) - 1))
    assert len(str(root)) == root_length
    root.mkdir(parents=True)
    sentinel = root / "preserved.txt"
    sentinel.write_text("original data")
    if existing:
        subprocess.run(["git", "-c", "core.longpaths=true", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "--local", "core.longpaths", "false"], cwd=root, check=True)
    store = GitSkillStore(root, auto_commit=False)
    skill = Skill(name="create_pet_request_response_handler", description="Long path fixture.",
                  state=SkillState.DRAFT, implementation=SkillImplementation(code="output['ok'] = True"))
    assert len(str(root / "skills" / skill.name / "versions.json.tmp")) > 260
    saved = store.add_skill(skill)
    assert store.get_skill_by_id(saved.skill_id).name == skill.name
    assert store.list_skills()[0]["skill_id"] == saved.skill_id
    newer = store.create_new_version(skill.name, source_version="1.0.0", bump="patch",
                                     overrides={"description": "new version"})
    reopened = GitSkillStore(root, auto_commit=False)
    assert reopened.get_skill(skill.name).skill_id == newer.skill_id
    assert reopened.get_skill_versions(skill.name) == ["1.0.0", "1.0.1"]
    reopened.rebuild_index(commit=False)
    assert {r["version"] for r in reopened.list_skills(latest_only=False)} == {"1.0.0", "1.0.1"}
    assert reopened.delete_skill(skill.name, version="1.0.1", hard=False)
    assert reopened.get_skill(skill.name).skill_id == saved.skill_id
    assert reopened.get_skill(skill.name, "1.0.1") is None
    assert reopened.get_skill(skill.name, "1.0.1", include_deleted=True).skill_id == newer.skill_id
    assert reopened.delete_skill(skill.name, version="1.0.1", hard=True)
    assert reopened.get_skill(skill.name, "1.0.1", include_deleted=True) is None
    assert sentinel.read_text() == "original data"
    assert reopened.read_events(limit=100)
    if os.name == "nt":
        result = subprocess.run(["git", "config", "--local", "--get", "core.longpaths"], cwd=root, check=True, capture_output=True, text=True)
        assert result.stdout.strip() == "true"
