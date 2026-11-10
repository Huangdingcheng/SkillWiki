from pathlib import Path
from types import SimpleNamespace
import json
import subprocess

import pytest

from skillwiki.api.component_demo import ComponentStore, digest, instruction, materialize_candidate, persisted_checks, run_component
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillProvenance, SkillState


@pytest.fixture
def demo(tmp_path, monkeypatch):
    repo = tmp_path / "governance"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Demo", "-c", "user.email=demo@local", "commit", "--allow-empty", "-qm", "base"], check=True)
    monkeypatch.setenv("SKILLOS_GOVERNANCE_REPO", str(repo))
    return SimpleNamespace(wiki=MemoryWikiManager(), graph=None, llm=None,
                           execution_trace_path=tmp_path / "runtime-traces/traces.jsonl")


async def parent(app):
    skill = Skill(name="dialogue_demo", state=SkillState.RELEASED,
                  implementation=SkillImplementation(prompt_template="old instruction"),
                  provenance=SkillProvenance(source_type="manual"))
    return await app.wiki.create(skill)


@pytest.mark.asyncio
async def test_complete_candidate_becomes_distinct_draft_and_git_diff(demo):
    original = await parent(demo)
    text = "new instruction\n" + "x" * 9000 + "\n"
    candidate = await materialize_candidate(demo, component="gepa", parent=original,
        candidate_text=text, parent_hash=digest(instruction(original)), parent_version=original.version,
        run_id="a" * 64, validation={"passed": False})
    draft = await demo.wiki.get(candidate["draft_skill_id"])
    assert instruction(draft) == text
    assert draft.state == SkillState.DRAFT and draft.skill_id != original.skill_id
    assert (await demo.wiki.get(original.skill_id)).state == SkillState.RELEASED
    assert instruction(await demo.wiki.get(original.skill_id)) == "old instruction"
    assert candidate["diffs"] and candidate["head_commit"] != candidate["base_commit"]
    store = ComponentStore.for_app(demo)
    store.save({"run_id": "a" * 64, "component": "gepa", "skill_id": original.skill_id,
                "parent_hash": digest(instruction(original)), "created_at": "now", "candidate": candidate})
    reread = ComponentStore(store.root).get("a" * 64)
    assert reread["candidate"]["candidate_text"] == text
    again = await materialize_candidate(demo, component="gepa", parent=original,
        candidate_text=text, parent_hash=digest(instruction(original)), parent_version=original.version, run_id="a" * 64)
    assert again["draft_skill_id"] == draft.skill_id


@pytest.mark.asyncio
async def test_parent_mutation_blocks_before_creating_draft(demo):
    original = await parent(demo)
    old_hash = digest(instruction(original))
    original.implementation.prompt_template = "mutated same version"
    with pytest.raises(ValueError, match="changed"):
        await materialize_candidate(demo, component="skillopt", parent=original,
            candidate_text="candidate", parent_hash=old_hash, parent_version=original.version, run_id="a" * 64)
    assert len(await demo.wiki.list()) == 1


@pytest.mark.asyncio
async def test_missing_contract_cannot_be_bypassed_with_empty_context(demo):
    original = await parent(demo)
    assert not persisted_checks(demo, original)["blocked"]
    original.provenance.creation_context["demo_components"] = {"source": {}}
    assert persisted_checks(demo, original)["blocked"]
    result = await run_component(demo, "skillguard", original.skill_id)
    assert result["status"] == "blocked"
    assert ComponentStore.for_app(demo).get(result["run_id"])["result"]["status"] == "defer"


@pytest.mark.asyncio
async def test_unbudgeted_provider_failure_is_durable_and_not_retried(demo):
    original = await parent(demo)
    result = await run_component(demo, "gepa", original.skill_id)
    assert result["status"] == "failed" and "budget" in result["error"]
    assert await run_component(demo, "gepa", original.skill_id) == result
    assert len(ComponentStore.for_app(demo).list(original.skill_id)) == 1


def test_candidate_schema_preserves_hash_sensitive_whitespace():
    from skillwiki.api.schemas import UpstreamCandidateRequest
    text = "  preserve exact text\n"
    request = UpstreamCandidateRequest(component="gepa", skill_id="s", parent_version="1.0.0",
        parent_hash="sha256:" + "0" * 64, candidate_text=text, candidate_hash=digest(text),
        upstream_repository="repo", upstream_commit="commit")
    assert request.candidate_text == text
