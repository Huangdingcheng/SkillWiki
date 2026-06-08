from __future__ import annotations

import pytest

from skillos.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillos.layers.skill_runtime.harness import HarnessKind, VerificationLoop
from skillos.models.skill_model import (
    Skill,
    SkillEvaluation,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillTestCase,
)


def _draft_skill(*, broken: bool = True) -> Skill:
    code = "output['summary'] = input_data.get('text', '')" if broken else "output['email'] = input_data.get('email')"
    return Skill(
        name="draft_extract_email_loop",
        description="Extract an email field from text.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["text", "email"],
            },
            output_schema={"type": "object", "properties": {"email": {"type": "string"}}},
            postconditions=["output.email exists"],
        ),
        implementation=SkillImplementation(code=code),
        test_cases=[
            SkillTestCase(
                test_id="email-case",
                name="email case",
                input_data={"text": "Contact Ada at ada@example.com", "email": "ada@example.com"},
                expected_output={"email": "ada@example.com"},
            )
        ],
        evaluation=SkillEvaluation(
            verifier_specs=[{"type": "json_exists", "path": "output.email"}],
            test_case_refs=["email-case"],
        ),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )


@pytest.mark.asyncio
async def test_verification_loop_repairs_retries_and_promotes_to_s3(tmp_path):
    wiki = MemoryWikiManager()
    graph = MemoryGraphManager()
    skill = await wiki.create(_draft_skill(broken=True))
    await graph.sync_skill(skill)

    result = await VerificationLoop(
        wiki=wiki,
        graph=graph,
        evidence_root=tmp_path,
    ).run(
        skill.skill_id,
        harness_kind=HarnessKind.LOCAL_SKILLWIKI,
        max_attempts=3,
    )

    assert result.status == "verified"
    assert result.promotion_allowed is True
    assert len(result.attempts) == 2
    assert result.attempts[0].verifier_passed is False
    assert result.attempts[1].verifier_passed is True
    assert result.repairs[0]["source"] == "deterministic"
    assert result.final_state == SkillState.VERIFIED.value

    original = await wiki.get(skill.skill_id)
    assert original is not None
    assert original.state == SkillState.DRAFT

    versions = await wiki.get_version_history(skill.name)
    verified = versions[-1]
    assert verified.state == SkillState.VERIFIED
    assert verified.evaluation.harness_validation["last_loop_id"] == result.loop_id
    assert verified.evaluation.harness_validation["promotion_gate"] == "passed"
    assert (tmp_path / result.loop_id / "result.json").exists()

    subgraph = await graph.get_subgraph([verified.skill_id], depth=1)
    assert any(edge.target_id == skill.skill_id for edge in subgraph.edges)


@pytest.mark.asyncio
async def test_verification_loop_can_repeat_repair_without_version_collision(tmp_path):
    wiki = MemoryWikiManager()
    skill = await wiki.create(_draft_skill(broken=True))

    loop = VerificationLoop(wiki=wiki, evidence_root=tmp_path)
    first = await loop.run(
        skill.skill_id,
        harness_kind=HarnessKind.LOCAL_SKILLWIKI,
        max_attempts=3,
    )
    second = await loop.run(
        skill.skill_id,
        harness_kind=HarnessKind.LOCAL_SKILLWIKI,
        max_attempts=3,
    )

    assert first.status == "verified"
    assert second.status == "verified"
    assert first.final_version == "1.0.1"
    assert second.final_version == "1.0.2"
    versions = await wiki.get_version_history(skill.name)
    assert [item.version for item in versions] == ["1.0.0", "1.0.1", "1.0.2"]
    original = await wiki.get(skill.skill_id)
    assert original is not None
    assert original.state == SkillState.DRAFT


@pytest.mark.asyncio
async def test_verification_loop_repairs_nested_json_contracts(tmp_path):
    wiki = MemoryWikiManager()
    skill = await wiki.create(
        Skill(
            name="draft_script_contract_loop",
            description="Return a script dry-run contract.",
            implementation=SkillImplementation(code="output['result'] = ''"),
            evaluation=SkillEvaluation(
                verifier_specs=[
                    {"type": "json_nonempty", "path": "output.result.entrypoint"},
                    {"type": "json_array", "path": "output.result.arguments"},
                    {"type": "json_array_nonempty", "path": "output.evidence"},
                    {"type": "json_equals", "path": "output.verifier.passed", "value": True},
                ]
            ),
        )
    )

    result = await VerificationLoop(wiki=wiki, evidence_root=tmp_path).run(
        skill.skill_id,
        harness_kind=HarnessKind.LOCAL_SKILLWIKI,
        max_attempts=3,
    )

    assert result.status == "verified"
    assert result.promotion_allowed is True
    assert len(result.attempts) == 2
    assert result.attempts[1].verifier_passed is True
    repaired_output = result.attempts[1].output["output"]
    assert repaired_output["result"]["entrypoint"]
    assert isinstance(repaired_output["result"]["arguments"], list)
    assert repaired_output["evidence"]
    assert repaired_output["verifier"]["passed"] is True


@pytest.mark.asyncio
async def test_verification_loop_does_not_promote_failed_skill_when_repair_disabled(tmp_path):
    wiki = MemoryWikiManager()
    skill = await wiki.create(_draft_skill(broken=True))

    result = await VerificationLoop(wiki=wiki, evidence_root=tmp_path).run(
        skill.skill_id,
        harness_kind=HarnessKind.LOCAL_SKILLWIKI,
        max_attempts=1,
        allow_repair=False,
    )

    assert result.status == "needs_human_review"
    assert result.promotion_allowed is False
    stored = await wiki.get(skill.skill_id)
    assert stored is not None
    assert stored.state == SkillState.DRAFT
