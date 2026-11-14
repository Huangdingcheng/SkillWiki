"""Versioned repair-only evidence helpers. Historical packages stay read-only."""
from __future__ import annotations
import copy, csv, hashlib, importlib.util, json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / 'research/stage2-demo-api-execution-20261029/oracle/scorer.py'
spec = importlib.util.spec_from_file_location('frozen_wp01_oracle', OLD)
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
RULE_VERSION = 'api-response-issues-v2-20261030'

def now():
    return datetime.now(timezone.utc).isoformat()

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='') as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))

def new_attempt(root):
    path = Path(root)
    path.mkdir(parents=True, exist_ok=False)
    return path

def bind_input(payload, case_id=None):
    """Bind this admitted task contract, never infer general runtime inputs."""
    result = copy.deepcopy(payload)
    if not isinstance(result, dict) or not isinstance(result.get('task_data'), dict):
        raise ValueError('TASK_BINDING_REQUIRES_TASK_DATA')
    ids = [v for v in (case_id, result.get('case_id'), result['task_data'].get('case_id')) if v is not None]
    if not ids or any(not isinstance(v, str) or not v for v in ids) or len(set(ids)) != 1:
        raise ValueError('TASK_CASE_ID_MISSING_OR_CONFLICT')
    if 'task_contract' not in result:
        raise ValueError('TASK_CONTRACT_MISSING')
    result['case_id'] = ids[0]
    result['task_data']['case_id'] = ids[0]
    return result

def response_errors(case, doc):
    route, method = oracle.TASKS[case['task_id']]
    responses = doc['paths'][route][method]['responses']
    x = case['input']
    spec = responses.get(str(x['status']), responses.get('default', {}))
    media = x.get('content_type', 'application/json')
    schema = spec.get('content', {}).get(media, {}).get('schema')
    return [] if schema is None else oracle.schema_issues(x.get('body'), schema, doc, '/body')

def grade(case, raw, *, execution_status='success', enabled=True, doc=None):
    """Response report: empty issues or the exact real error set. Request rules unchanged."""
    source = doc if doc is not None else oracle.read_source()
    normalized = raw
    if execution_status == 'success' and enabled and case.get('kind') == 'response':
        try:
            obj = oracle.strict_load(raw)
            issues = obj.get('issues')
            allowed = response_errors(case, source)
            well_formed = isinstance(issues, list) and all(isinstance(e, dict) and set(e) == {'path', 'code'} and all(isinstance(v, str) for v in e.values()) for e in issues)
            if well_formed:
                tuples = [(e['path'], e['code']) for e in issues]
                if len(tuples) == len(set(tuples)) and (not issues or set(tuples) == {(e['path'],e['code']) for e in allowed}):
                    obj['issues'] = []
                    normalized = json.dumps(obj, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            pass
    result = oracle.grade(case, normalized, execution_status=execution_status, enabled=enabled, doc=source)
    return {**result, 'rule_version': RULE_VERSION}

def reconcile_events(rows):
    """One charge per request identity; reserves are replaced by settlement."""
    requests = {}
    for row in rows:
        rid = row['request_id']; event = row['event']
        if event in ('reserve', 'historical_import'):
            if rid in requests: raise ValueError('DUPLICATE_REQUEST_ID')
            charge = row.get('estimate_cny') if event == 'reserve' else row['charged_cny']
            amount = Decimal(str(charge))
            if not amount.is_finite() or amount < 0: raise ValueError('INVALID_CHARGE')
            requests[rid] = {'charge': amount, 'event': event, 'settled': event == 'historical_import', 'status': row.get('status'), 'reconciled': False}
        elif event in ('settle', 'reconcile'):
            if rid not in requests: raise ValueError('SETTLEMENT_WITHOUT_RESERVATION')
            if event == 'settle' and requests[rid]['settled']: raise ValueError('DUPLICATE_SETTLEMENT')
            if event == 'reconcile' and (not requests[rid]['settled'] or requests[rid]['status'] != 'anomaly_retained' or requests[rid]['reconciled']): raise ValueError('INVALID_OR_DUPLICATE_RECONCILIATION')
            amount = Decimal(str(row['charged_cny']))
            if not amount.is_finite() or amount < 0: raise ValueError('INVALID_CHARGE')
            requests[rid].update(charge=amount, event=event, settled=True, status=row.get('status', 'usage_settled'), reconciled=event=='reconcile')
        else: raise ValueError('UNKNOWN_EVENT')
    return {'requests': len(requests), 'charged_cny': str(sum((x['charge'] for x in requests.values()), Decimal(0))), 'unsettled': sum(not x['settled'] for x in requests.values())}

def http_observation(response):
    body = response.json()
    detail = body.get('detail', {}) if isinstance(body, dict) else {}
    decision = (body.get('decision') or (detail.get('decision') if isinstance(detail, dict) else None)) if isinstance(body, dict) else None
    return {'http_status': response.status_code, 'decision': decision, 'request_validation_error': response.status_code == 422, 'body': body}

def write_csv(path, fields, rows):
    rows = list(rows)
    if any(set(row) != set(fields) for row in rows): raise ValueError('CSV_SCHEMA_MISMATCH')
    with Path(path).open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)

def unique_receipts(paths):
    found = {}
    for path in paths:
        path = Path(path)
        if path.name != 'worker-receipt.json': continue
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        key = (value['job_id'], value['attempt_id'])
        if key in found:
            if found[key][1] != value: raise ValueError('CONFLICTING_ATTEMPT_RECEIPTS')
            continue
        found[key] = (str(path), value)
    return list(found.values())
