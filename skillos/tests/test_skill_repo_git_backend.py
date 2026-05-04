from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from skillos.api.main import _seed_demo_skills, _sync_graph_from_wiki
from skillos.api.memory_store import MemoryGraphManager
from skillos.api.routes import graph as graph_routes
from skillos.api.routes import lifecycle, repository, skills
from skillos.api.schemas import ExecutionStepResult, SkillUpdateRequest
from skillos.layers.skill_management.librarian import SkillLibrarianAgent
from skillos.layers.skill_repository.repository import SkillWikiManager
from skillos.models.skill_model import (
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillMetrics,
    SkillState,
    SkillType,
)
from skillos.storage.skill_repo.common import GitSkillStore


def make_skill(
    name: str = "submit_form",
    *,
    version: str = "1.0.0",
    state: SkillState = SkillState.DRAFT,
    tags: list[str] | None = None,
    description: str | None = None,
) -> Skill:
    return Skill(
        name=name,
        version=version,
        description=description if description is not None else f"{name} skill",
        skill_type=SkillType.FUNCTIONAL,
        state=state,
        tags=tags or ["web", "form"],
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        implementation=SkillImplementation(code="output['ok'] = True"),
    )


def commit_count(path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def test_git_store_create_writes_version_manifest_index_and_event(tmp_path):
    store = GitSkillStore(tmp_path / "SkillStorage", auto_commit=True)
    store.init_repo(initial_commit=True)
    skill = store.add_skill(make_skill(), author="test")

    skill_file = tmp_path / "SkillStorage" / "skills" / skill.name / "1.0.0.json"
    manifest_file = tmp_path / "SkillStorage" / "skills" / skill.name / "versions.json"
    index_file = tmp_path / "SkillStorage" / "metadata" / "skills_index.json"

    assert skill_file.exists()
    assert manifest_file.exists()
    assert index_file.exists()
    assert store.get_skill(skill.name, "1.0.0").skill_id == skill.skill_id
    assert store.list_skills(tags=["web"])[0]["skill_id"] == skill.skill_id
    assert store.read_events(limit=5)[-1]["action"] == "create"


def test_git_store_update_new_version_soft_delete_diff_history_and_status(tmp_path):
    store = GitSkillStore(tmp_path / "SkillStorage", auto_commit=True)
    store.init_repo(initial_commit=True)
    skill = store.add_skill(make_skill(description="first"), author="test")

    skill.description = "same version update"
    store.update_skill_version(skill, author="test")

    new_skill = store.create_new_version(
        skill.name,
        source_version="1.0.0",
        bump="minor",
        overrides={"description": "second version"},
        author="test",
    )

    assert new_skill.skill_id != skill.skill_id
    assert new_skill.version == "1.1.0"
    assert new_skill.state == SkillState.DRAFT
    assert new_skill.metrics == SkillMetrics()
    assert store.get_skill_versions(skill.name) == ["1.0.0", "1.1.0"]
    assert "second version" in store.diff_versions(skill.name, "1.0.0", "1.1.0")
    assert store.git_file_history(skill.name)

    assert store.delete_skill(skill.name, version="1.1.0", hard=False, author="test")
    assert store.get_skill(skill.name).version == "1.0.0"
    assert store.get_skill(skill.name, "1.1.0") is None
    assert store.get_skill(skill.name, "1.1.0", include_deleted=True).version == "1.1.0"

    status = store.repo_status()
    assert status["backend"] == "git"
    assert status["is_git_repo"] is True


@pytest.mark.asyncio
async def test_wiki_manager_adapter_keeps_public_repo_contracts(tmp_path):
    wiki = SkillWikiManager(storage_dir=tmp_path / "SkillStorage")
    skill = await wiki.create(make_skill(tags=["web", "checkout"]))

    listed = await wiki.list(tags=["checkout"])
    assert [item.skill_id for item in listed] == [skill.skill_id]
    assert (await wiki.get_by_name(skill.name)).skill_id == skill.skill_id

    updated = await wiki.update(
        skill.skill_id,
        interface=skill.interface.model_dump(),
        implementation=skill.implementation.model_dump(),
        tags=["updated"],
    )
    assert updated.interface.input_schema["properties"]["target"]["type"] == "string"
    assert updated.implementation.code == "output['ok'] = True"
    assert updated.tags == ["updated"]

    before = commit_count(wiki.store.base_dir)
    await wiki.record_execution(skill.skill_id, success=True, latency_ms=12.5)
    after = commit_count(wiki.store.base_dir)
    assert after == before
    assert (await wiki.get(skill.skill_id)).metrics.success_count == 1
    assert (await wiki.repo_status())["dirty"] is True


@pytest.mark.asyncio
async def test_lifecycle_routes_use_git_history_and_diff(tmp_path):
    wiki = SkillWikiManager(storage_dir=tmp_path / "SkillStorage")
    old_skill = await wiki.create(make_skill(description="old"))
    new_skill = await wiki.create_new_version(
        old_skill.skill_id,
        bump="patch",
        description="new",
    )
    app = SimpleNamespace(wiki=wiki, version_ctrl=None)

    history = await lifecycle.get_skill_diff(new_skill.skill_id, app=app)
    assert history["source"] == "git"
    assert [item["to_version"] for item in history["history"]] == ["1.0.0", "1.0.1"]

    diff = await lifecycle.get_skill_diff(new_skill.skill_id, compare_to=old_skill.skill_id, app=app)
    assert diff["source"] == "git"
    assert diff["raw_diff"]
    assert diff["diff"][0]["new_lines"]


@pytest.mark.asyncio
async def test_api_update_delete_and_repository_routes_use_public_wiki_methods(tmp_path):
    wiki = SkillWikiManager(storage_dir=tmp_path / "SkillStorage")
    skill = await wiki.create(make_skill())
    app = SimpleNamespace(wiki=wiki)

    summary = await skills.update_skill(
        skill.skill_id,
        SkillUpdateRequest(description="updated via api", tags=["api"]),
        app=app,
    )
    assert summary.description == "updated via api"
    assert summary.tags == ["api"]

    status = await repository.get_repository_status(app=app)
    assert status["backend"] == "git"

    events = await repository.get_repository_events(limit=10, app=app)
    assert any(event["action"] == "update" for event in events)

    deleted = await skills.delete_skill(skill.skill_id, app=app)
    assert deleted.ok is True
    assert await wiki.get(skill.skill_id) is None


@pytest.mark.asyncio
async def test_librarian_updates_wiki_through_public_update_method(tmp_path):
    wiki = SkillWikiManager(storage_dir=tmp_path / "SkillStorage")
    skill = await wiki.create(make_skill())
    changed = skill.model_copy(deep=True)
    changed.description = "librarian update"

    result = await SkillLibrarianAgent(wiki_manager=wiki).update(changed)

    assert result.wiki_updated is True
    assert result.errors == []
    assert (await wiki.get(skill.skill_id)).description == "librarian update"


@pytest.mark.asyncio
async def test_librarian_uses_public_graph_methods(tmp_path):
    wiki = SkillWikiManager(storage_dir=tmp_path / "SkillStorage")
    graph = FakeGraphManager()
    skill = await wiki.create(make_skill())

    librarian = SkillLibrarianAgent(wiki_manager=wiki, graph_manager=graph)
    result = await librarian.update(skill)
    relation_added = await librarian.add_relation(
        skill.skill_id,
        "target-skill",
        "depends_on",
        weight=0.7,
    )

    assert result.graph_updated is True
    assert graph.synced == [skill.skill_id]
    assert relation_added is True
    assert graph.edges[0].source_id == skill.skill_id
    assert graph.edges[0].target_id == "target-skill"
    assert graph.edges[0].edge_type.value == "depends_on"


def test_execution_step_result_keeps_c_e_runtime_fields():
    assert "step_index" in ExecutionStepResult.model_fields
    assert "outputs" in ExecutionStepResult.model_fields
    assert "result" in ExecutionStepResult.model_fields


class FakeGraphManager:
    def __init__(self) -> None:
        self.synced: list[str] = []
        self.edges: list[object] = []

    async def sync_skill(self, skill: Skill) -> None:
        self.synced.append(skill.skill_id)

    async def create_edge(self, edge: object) -> None:
        self.edges.append(edge)


@pytest.mark.asyncio
async def test_demo_seed_syncs_graph_and_uses_readable_text(tmp_path):
    wiki = SkillWikiManager(storage_dir=tmp_path / "SkillStorage")
    graph = MemoryGraphManager()

    await _seed_demo_skills(wiki)
    await _sync_graph_from_wiki(wiki, graph)

    seeded_skills = await wiki.list(limit=100)
    stats = await graph.get_stats()
    route_stats = await graph_routes.get_graph_stats(app=SimpleNamespace(graph=graph))

    assert seeded_skills
    assert stats["nodes"] == len(seeded_skills)
    assert route_stats["nodes"] == len(seeded_skills)

    click_skill = await wiki.get_by_name("click_element")
    assert click_skill is not None
    assert click_skill.description == "Click a target element on a web page."

    mojibake_markers = ("鍦", "灏", "鐨", "歿")
    sampled_parts: list[str] = []
    for skill in seeded_skills:
        sampled_parts.extend([
            skill.description or "",
            skill.implementation.prompt_template if skill.implementation else "",
            " ".join(skill.interface.preconditions),
            " ".join(skill.interface.postconditions),
        ])
    sampled_text = "\n".join(part or "" for part in sampled_parts)
    assert not any(marker in sampled_text for marker in mojibake_markers)
