"""Released identities remain discoverable alongside newer draft versions."""
from __future__ import annotations

import pytest

from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.layers.skill_repository.repository import SkillWikiManager
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillProvenance, SkillState


def _skill(version: str, state: SkillState) -> Skill:
    # Legacy released fixture seeds catalog state, not a new publication proof.
    return Skill(name="versioned_online_skill", description="Existing online catalog entry.",
                 version=version, state=state, domain="api", tags=["online"],
                 implementation=SkillImplementation(code="output['ok'] = True"))


@pytest.mark.parametrize("backend", ["memory", "git"])
@pytest.mark.asyncio
async def test_released_ids_survive_newer_draft_and_recovery_versions(tmp_path, backend):
    wiki = MemoryWikiManager() if backend == "memory" else SkillWikiManager(storage_dir=tmp_path / "store", auto_commit=False)
    online = await wiki.create(_skill("1.0.1", SkillState.RELEASED))
    newer = await wiki.create_new_version(online.skill_id, "patch")
    assert newer.state == SkillState.DRAFT
    assert {s.skill_id for s in await wiki.list(state=SkillState.RELEASED)} == {online.skill_id}
    assert {s.skill_id for s in await wiki.list(state=SkillState.DRAFT)} == {newer.skill_id}
    # Recovery drafts have special default-latest handling but must remain
    # discoverable when the caller explicitly asks for their state.
    recovered = _skill("1.0.3", SkillState.DRAFT)
    recovered.provenance = SkillProvenance(source_type="adapt", creation_context={"recovery_draft": True})
    await wiki.create(recovered)
    assert {s.skill_id for s in await wiki.list(state=SkillState.RELEASED)} == {online.skill_id}
    assert {s.skill_id for s in await wiki.list(state=SkillState.DRAFT)} == {newer.skill_id, recovered.skill_id}
    if backend == "git":
        wiki = SkillWikiManager(storage_dir=tmp_path / "store", auto_commit=False)
        assert [s.skill_id for s in await wiki.list()] == [newer.skill_id]
        assert {s.skill_id for s in await wiki.list(state=SkillState.RELEASED)} == {online.skill_id}


@pytest.mark.parametrize("backend", ["memory", "git"])
@pytest.mark.asyncio
async def test_state_filter_keeps_all_released_identities_and_other_filters(tmp_path, backend):
    wiki = MemoryWikiManager() if backend == "memory" else SkillWikiManager(storage_dir=tmp_path / "store", auto_commit=False)
    first = await wiki.create(_skill("1.0.1", SkillState.RELEASED))
    second = await wiki.create(_skill("1.0.2", SkillState.RELEASED))
    draft = await wiki.create(_skill("1.0.3", SkillState.DRAFT))
    released = await wiki.list(state=SkillState.RELEASED, tags=["online"], domain="api", name_like="versioned")
    assert {s.skill_id for s in released} == {first.skill_id, second.skill_id}
    assert await wiki.list(state=SkillState.RELEASED, domain="different") == []
    assert len(await wiki.list(state=SkillState.RELEASED, limit=1, offset=1)) == 1
    if backend == "git":
        assert [s.skill_id for s in await wiki.list()] == [draft.skill_id]
        assert [row["skill_id"] for row in wiki.store.list_skills()] == [draft.skill_id]
    await wiki.delete(first.skill_id)
    assert {s.skill_id for s in await wiki.list(state=SkillState.RELEASED)} == {second.skill_id}
    assert await wiki.get(draft.skill_id) is not None
