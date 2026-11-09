import json
from pathlib import Path

import pytest

from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.layers.skill_runtime.harness import VerificationLoop
from skillwiki.layers.skill_runtime.harness.verifier_loop import _build_test_cases
from skillwiki.layers.skill_runtime.verifier import evaluate_verifier_specs
from skillwiki.models.skill_model import Skill, SkillImplementation, SkillTestCase, SkillEvaluation, SkillState


def draft(cases=None, code="output['answer'] = input_data.get('answer')", specs=None):
    return Skill(name='case_bound_oracles', implementation=SkillImplementation(code=code),
                 test_cases=cases or [], evaluation=SkillEvaluation(verifier_specs=specs or []))


def pair():
    return [SkillTestCase(name='first', input_data={'answer': 'A'}, expected_output={'answer': 'A'}),
            SkillTestCase(name='second', input_data={'answer': 'B'}, expected_output={'answer': 'B'})]


def test_d07_each_case_uses_own_expected_and_shared_rules():
    cases = _build_test_cases(draft(pair(), specs=[{'type': 'json_exists', 'path': 'output.answer'}]), timeout_s=5)
    for case, expected, wrong in zip(cases, ['A', 'B'], ['B', 'A']):
        assert evaluate_verifier_specs(case.verifier_specs, {'output': {'answer': expected}}).passed
        assert not evaluate_verifier_specs(case.verifier_specs, {'output': {'answer': wrong}}).passed
        assert any(s['type'] == 'json_exists' for s in case.verifier_specs)


def test_d07_explicit_empty_expected_is_an_oracle():
    case = _build_test_cases(draft([SkillTestCase(name='empty', expected_output={})]), timeout_s=5)[0]
    assert evaluate_verifier_specs(case.verifier_specs, {'output': {}}).passed
    assert not evaluate_verifier_specs(case.verifier_specs, {'output': {'unexpected': True}}).passed


def test_d08_no_implicit_smoke_oracle():
    assert _build_test_cases(draft(), timeout_s=5)[0].verifier_specs == []


@pytest.mark.asyncio
async def test_d08_missing_oracle_executes_but_never_qualifies(tmp_path):
    wiki = MemoryWikiManager()
    skill = await wiki.create(draft(code="output['answer'] = 'A'"))
    result = await VerificationLoop(wiki=wiki, evidence_root=tmp_path).run(skill.skill_id)
    assert result.status == 'not_evaluated'
    assert not result.promotion_allowed and len(result.attempts) == 1
    assert result.attempts[0].output['record']['status'] == 'success'
    assert (await wiki.get(skill.skill_id)).state == SkillState.DRAFT
    assert not result.repairs


@pytest.mark.asyncio
async def test_d08_default_repair_does_not_fill_expected_answer(tmp_path):
    wiki = MemoryWikiManager()
    skill = await wiki.create(draft(pair(), code="output['unrelated'] = 'value'"))
    result = await VerificationLoop(wiki=wiki, evidence_root=tmp_path).run(skill.skill_id)
    assert not result.promotion_allowed
    assert len(await wiki.get_version_history(skill.name)) == 1
    assert result.status == 'needs_human_review'


@pytest.mark.asyncio
async def test_d07_distinct_case_receipts_are_not_overwritten(tmp_path):
    wiki = MemoryWikiManager()
    skill = await wiki.create(draft(pair()))
    result = await VerificationLoop(wiki=wiki, evidence_root=tmp_path).run(skill.skill_id, max_attempts=1, promote_on_pass=False)
    paths = [Path(item.evidence_path) for item in result.attempts]
    assert len(set(paths)) == 2
    for path, item in zip(paths, result.attempts):
        assert json.loads((path / 'input.json').read_text()) == item.input_data
        assert json.loads((path / 'result.json').read_text())['run_id'] == item.run_id


@pytest.mark.asyncio
async def test_d08_explicit_fixture_repair_never_promotes(tmp_path):
    wiki = MemoryWikiManager()
    skill = await wiki.create(draft([SkillTestCase(name='fixture', expected_output={'answer': 'A'})], code="output['other'] = True"))
    result = await VerificationLoop(wiki=wiki, evidence_root=tmp_path, allow_fixture_repair=True).run(skill.skill_id)
    assert result.status == 'fixture_only'
    assert result.attempts[-1].verifier_passed
    assert not result.promotion_allowed
    assert result.final_state == SkillState.DRAFT.value
