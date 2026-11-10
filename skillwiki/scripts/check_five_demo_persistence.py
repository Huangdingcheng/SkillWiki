"""Readback/idempotency and negative runtime checks; never request new inference."""
import argparse
import hashlib
import json
from pathlib import Path
import requests
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skillwiki'))
from skillwiki.utils.cumulative_budget import CumulativeBudgetGuard

OUT = ROOT / 'artifacts/20261024-five-capability-demo'
BASE = 'http://127.0.0.1:8008/api/v1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['before-restart', 'after-restart'])
    phase = parser.parse_args().phase
    guard = CumulativeBudgetGuard(ROOT / 'skillwiki-launcher/runtime/cumulative-budget.jsonl')
    before = guard.snapshot()
    observations = {'budget_before': before, 'skills': [], 'runs': []}
    for name in ('gepa-real', 'skillopt-real', 'arex-refresh-real'):
        saved = json.loads((OUT / (name + '.json')).read_text())['result']
        run = requests.get(BASE + '/evolution/component-runs/' + saved['run_id'], timeout=20).json()
        assert run['status'] == saved['status'] == 'success'
        candidate = run['candidate']
        skill = requests.get(BASE + '/skills/' + candidate['draft_skill_id'] + '/full', timeout=20).json()
        text = skill['implementation']['prompt_template']
        digest = 'sha256:' + hashlib.sha256(text.encode()).hexdigest()
        assert digest == candidate['candidate_hash'] and skill['state'] == 'S2'
        assert skill['version'] == candidate['version']
        observations['skills'].append({'id': skill['skill_id'], 'version': skill['version'], 'hash': digest, 'state': skill['state']})
        if phase == 'before-restart':
            # Exactly the already successful identity: persisted run must be reused.
            repeated = requests.post(BASE + '/evolution/component-runs', json=json.loads((OUT / (name + '.json')).read_text())['input'], timeout=30).json()
            assert repeated['run_id'] == run['run_id'] and repeated['candidate']['draft_skill_id'] == skill['skill_id']
        observations['runs'].append({'id': run['run_id'], 'component': run['component'], 'validation': candidate['validation_status']})
    saved = json.loads((OUT / 'combined-prompt-admission.json').read_text())['result']
    trace = requests.get(BASE + '/execution/trace/' + saved['plan_id'], timeout=20)
    assert trace.status_code == 200, trace.text
    trace = trace.json()
    assert trace['execution']['verifier_passed'] is True
    assert trace['plan']['metadata']['source'] == 'explicit_single_skill_deterministic'
    assert trace['execution']['steps'][0]['result']['nodes'][0]['speaker'] == 'Guard'
    observations['trace'] = trace
    if phase == 'before-restart':
        original = requests.get(BASE + '/skills/c92c09f1-bc3c-464d-84ff-7d55c3cd6d21', timeout=20).json()
        assert original['state'] == 'S4' and original['version'] == '1.0.0'
        # The real controlled-source commit changed; empty client context cannot bypass it.
        stale = requests.post(BASE + '/execution/skill', json={'skill_id': original['skill_id'], 'inputs': {'dialogue': 'A: x\nB: y'}}, timeout=30)
        assert stale.status_code == 409
        draft = requests.post(BASE + '/execution/skill', json={'skill_id': observations['skills'][0]['id'], 'inputs': {}}, timeout=20)
        assert draft.status_code == 409
        bad = requests.post(BASE + '/execution/plan', json={'goal': 'parse', 'skill_ids': ['missing-frozen-id']}, timeout=20)
        assert bad.status_code == 409
        observations['blocked_checks'] = {'stale_source_empty_context': stale.json(), 'unpublished_draft': draft.json(), 'missing_id': bad.json()}
    after = guard.snapshot()
    assert before == after, 'Readback or identical run unexpectedly consumed budget'
    observations['budget_after'] = after
    (OUT / (phase + '.json')).write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'phase': phase, 'drafts': len(observations['skills']), 'trace_id': saved['plan_id'], 'budget': after}))


if __name__ == '__main__':
    main()
