"""Use the newly authorized 14th request to validate real model planning.

Execution is linked to an existing exact-input verified run, not silently rerun.
The source has since changed; this is explicitly a historical frozen-snapshot
check and does not bypass current source admission or publish the refresh draft.
"""
import asyncio
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skillwiki'))
from skillwiki.config.llm_config import LLMConfig
from skillwiki.utils.llm_client import LLMClient
from skillwiki.utils.cumulative_budget import CumulativeBudgetGuard
from skillwiki.layers.skill_repository import SkillWikiManager
from skillwiki.layers.skill_runtime.planner import SkillPlanner
from skillwiki.api.routes.execution import _skill_content_hash
from skillwiki.layers.skill_management.dialogue_demo import verify_graph


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', type=int, choices=[14, 15], default=14)
    attempt = parser.parse_args().attempt
    out = ROOT / 'artifacts/20261024-five-capability-demo'
    path = out / f'model-plan-{attempt}.json'
    if path.exists():
        raise RuntimeError('Model planning already attempted; inspect its evidence, no retry')
    ledger = ROOT / 'skillwiki-launcher/runtime/cumulative-budget.jsonl'
    guard = CumulativeBudgetGuard(ledger)
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    count = sum(row['event'] == 'reserve' and row['kind'] == 'model' for row in rows)
    assert count == attempt - 1, count
    old = json.loads((out / 'combined-prompt-admission.json').read_text())
    prior = old['result']
    wiki = SkillWikiManager(storage_dir=ROOT / 'skillwiki-launcher/runtime/skill-storage-git')
    skill = await wiki.get('c92c09f1-bc3c-464d-84ff-7d55c3cd6d21')
    assert _skill_content_hash(skill) == prior['retrieval_trace']['provenance']['skills'][skill.skill_id]['content_hash']
    row = {'status': 'dispatching', 'budget_before': guard.snapshot(),
        'scope': 'historical_frozen_snapshot_model_plan_with_exact_input_execution_reuse',
        'current_source_status': 'changed; current runtime remains blocked',
        'skill_id': skill.skill_id, 'version': skill.version, 'content_hash': _skill_content_hash(skill)}
    path.write_text(json.dumps(row, indent=2), encoding='utf-8')
    config = LLMConfig(api_key=os.environ['DEEPSEEK_API_KEY'], api_url='https://api.deepseek.com',
        model='deepseek-flash', temperature=0, max_tokens=4096, timeout=120, retry_count=0,
        cumulative_budget_ledger_path=str(ledger), cumulative_budget_total_limit_cny='10')
    class RecordingClient(LLMClient):
        def chat(self, *args, **kwargs):
            response = super().chat(*args, **kwargs)
            (out / f'model-plan-{attempt}-raw-response.json').write_text(json.dumps({
                'text': response.content, 'model': response.model, 'usage': response.usage,
                'finish_reason': response.finish_reason,
                'receipt': self.last_budget_receipt}, ensure_ascii=False, indent=2), encoding='utf-8')
            return response
    client = RecordingClient(config)
    try:
        plan = await SkillPlanner(client).plan(task_description=old['input']['goal'],
            available_skills=[skill], current_state=old['input']['context'], force_llm=True,
            fallback_on_llm_error=False, fallback_on_invalid_response=False)
        row['plan'] = asdict(plan)
        row['receipt'] = client.last_budget_receipt
        assert len(plan.steps) == 1 and plan.metadata.get('source') == 'llm'
        step = plan.steps[0]
        assert step.skill_id == skill.skill_id
        assert step.input_mapping == prior['steps'][0]['input_mapping']
        passed, reason = verify_graph(step.input_mapping['dialogue'], prior['steps'][0]['result'])
        assert passed, reason
        row.update(status='passed', execution_reuse={'execution_id': prior['plan_id'],
            'mode': 'read_previous_verified_execution_not_a_new_execution',
            'input_sha256': hashlib.sha256(json.dumps(step.input_mapping, sort_keys=True).encode()).hexdigest(),
            'verifier_rechecked': passed, 'reason': reason})
    except Exception as exc:
        row.update(status='failed', error=f'{type(exc).__name__}: {str(exc)[:500]}',
            receipt=getattr(client, 'last_budget_receipt', None))
    row['budget_after'] = guard.snapshot()
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k:row.get(k) for k in ('status', 'error', 'budget_after', 'execution_reuse')}))


if __name__ == '__main__':
    asyncio.run(main())
