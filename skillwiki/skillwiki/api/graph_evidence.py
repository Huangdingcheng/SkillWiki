"""Project saved events for navigation; graph verdicts never grant release rights."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import quote

from .execution_history import history_directory
from ..utils.filesystem_paths import filesystem_path
from ..layers.skill_runtime.harness.workspace import HarnessEvidenceStore
from ..layers.skill_governance.artifact_identity import canonical_skill_artifact_hash
from ..models.graph_model import (ExecutionGraphNode, ValidationGraphNode,
                                 HeteroGraphEdge, HeteroEdgeType)


def _load_projection_history(directory, diagnostics):
    """Read independently so one corrupt receipt cannot hide valid graph events."""
    directory = filesystem_path(directory)
    rows = []
    if not directory.exists():
        return rows
    for path in sorted(directory.glob('*.json')):
        try:
            row = json.loads(path.read_bytes())
            ident = row.get('execution_id') if isinstance(row, dict) else None
            if not isinstance(ident, str) or not ident:
                raise ValueError('invalid execution receipt identity')
            # Keep the immutable execution-history filename contract.
            expected = hashlib.sha256(ident.encode('utf-8')).hexdigest() + '.json'
            if path.name != expected or not isinstance(row.get('created_at'), str):
                raise ValueError('invalid execution receipt identity or timestamp')
            rows.append(row)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            diagnostics['issues'].append({'receipt': path.name, 'reason': type(exc).__name__})
    return sorted(rows, key=lambda row: (row['created_at'], row['execution_id']))


def _step_verdict(row, steps, step):
    """Only a unique explicit step and Skill match can supply a step verdict."""
    scope = 'single_step' if len(steps) == 1 else 'task_only'
    summary = row.get('verifier_summary')
    if not isinstance(summary, dict) or 'results' not in summary:
        return (row.get('verifier_passed') if len(steps) == 1 else None), scope
    results = summary['results']
    step_id, skill_id = step.get('step_id'), step.get('skill_id')
    if not isinstance(results, list) or not isinstance(step_id, str) or not step_id:
        return None, scope
    matches = [result for result in results if isinstance(result, dict)
               and result.get('step_id') == step_id and result.get('skill_id') == skill_id]
    occurrences = sum(isinstance(item, dict) and item.get('step_id') == step_id
                      and item.get('skill_id') == skill_id for item in steps)
    if occurrences == 1 and len(matches) == 1 and isinstance(matches[0].get('passed'), bool):
        return matches[0]['passed'], 'step'
    return None, scope


def receipt_projection(app, skills):
    by_id = {skill.skill_id: skill for skill in skills}
    nodes, edges = [], []
    diagnostics = {'projected_events': 0, 'unmatched_events': 0, 'issues': []}

    def event(*, ident, skill_id, version, status, outcome, kind, reference,
              validator, extra=None, artifact_hash=None):
        skill = by_id.get(skill_id)
        if skill is None or version != skill.version:
            diagnostics['unmatched_events'] += 1
            return
        recorded_outcome = outcome
        current_hash = canonical_skill_artifact_hash(skill)
        binding = 'match' if artifact_hash == current_hash else 'mismatch' if artifact_hash else 'unavailable'
        if kind != 'candidate_import':
            outcome = outcome if binding == 'match' else 'stale' if binding == 'mismatch' else 'unknown'
        execution_id, validation_id = f'execution:{ident}', f'validation:{ident}'
        metadata = {
            'event_type': kind, 'skill_id': skill_id, 'skill_version': version,
            'receipt_ref': reference, 'identity_binding': 'skill_id_and_version',
            'qualification_proof': False, **(extra or {}),
            'recorded_outcome': recorded_outcome, 'artifact_binding': binding,
            'artifact_hash_verified': binding == 'match',
        }
        nodes.append(ExecutionGraphNode(
            node_id=execution_id, name=f'{kind}:{skill.name}', execution_id=ident,
            status=status, skill_ref=f'skill:{skill_id}', metadata=metadata,
            description='Saved event; execution completion and task correctness are separate.',
            created_by='runtime_receipt'))
        nodes.append(ValidationGraphNode(
            node_id=validation_id, name=f'{validator}:{skill.name}',
            validation_id=validation_id, outcome=outcome, validator=validator,
            metadata=metadata, created_by='runtime_receipt'))
        for source, target, relation in [
            (f'skill:{skill_id}', execution_id, HeteroEdgeType.EXECUTED_AS),
            (execution_id, validation_id, HeteroEdgeType.VALIDATED_BY),
        ]:
            edges.append(HeteroGraphEdge(
                edge_id=f'receipt:{relation.value}:{source}:{target}',
                source_id=source, target_id=target, edge_type=relation,
                metadata=metadata, created_by='runtime_receipt'))
        diagnostics['projected_events'] += 1

    for skill in skills:
        context = skill.provenance.creation_context if skill.provenance else {}
        imported = context.get('candidate_import_receipt')
        if not isinstance(imported, dict):
            continue
        # Descendants can retain provenance context, but cannot inherit the event.
        if imported.get('skill_id') != skill.skill_id or imported.get('skill_version') != skill.version:
            continue
        event(ident=f'ingest:{skill.skill_id}', skill_id=skill.skill_id, version=skill.version,
              status='completed', outcome='candidate', kind='candidate_import',
              reference=f'/api/v1/skills/{quote(skill.skill_id, safe="")}',
              validator='candidate_audit', extra={
                  'audit': imported.get('audit'), 'audit_status': imported.get('audit_status', 'unknown'),
                  'task_correctness_evaluated': False,
              })

    store = HarnessEvidenceStore()
    if store.root.exists():
        for path in sorted(store.root.glob('*/result.json')):
            try:
                loop = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(loop, dict) or loop.get('loop_id') != path.parent.name:
                    raise ValueError('loop identity mismatch')
                for run in loop.get('attempts', []):
                    if not isinstance(run, dict):
                        raise ValueError('invalid run record')
                    record = (run.get('output') or {}).get('record') or {}
                    sid = run.get('skill_id')
                    if sid not in by_id:
                        continue
                    if not run.get('run_id') or record.get('skill_id') != sid:
                        diagnostics['issues'].append({'receipt': path.parent.name, 'reason': 'run identity unavailable or inconsistent'})
                        continue
                    passed = run.get('verifier_passed')
                    outcome = ('not_evaluated' if run.get('status') == 'not_evaluated' else
                               'passed' if passed is True else 'failed' if passed is False else 'unknown')
                    event(ident=f'harness:{run["run_id"]}', skill_id=sid,
                          version=record.get('skill_version'), status=record.get('status', 'unknown'),
                          artifact_hash=(run.get('output') or {}).get('artifact_hash'),
                          outcome=outcome, kind='harness_execution', validator='harness_verifier',
                          reference=f'/api/v1/harness/{quote(loop["loop_id"], safe="")}',
                          extra={'loop_id': loop['loop_id'], 'run_id': run['run_id'],
                                 'loop_status': loop.get('status'), 'failure_reason': run.get('failure_reason', ''),
                                 'artifact_hash_verified': False})
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                diagnostics['issues'].append({'receipt': path.parent.name, 'reason': type(exc).__name__})

    directory = history_directory(app)
    if directory is not None:
        try:
            rows = _load_projection_history(directory, diagnostics)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows = []
            diagnostics['issues'].append({'receipt': 'execution-history', 'reason': type(exc).__name__})
        for row in rows:
            unit = row.get('experience_unit')
            metadata = unit.get('metadata') if isinstance(unit, dict) else None
            versions = metadata.get('retrieved_skill_versions') if isinstance(metadata, dict) else None
            steps = row.get('steps', [])
            if not isinstance(versions, dict) or not isinstance(steps, list):
                diagnostics['issues'].append({'receipt': row['execution_id'], 'reason': 'invalid execution receipt shape'})
                continue
            for index, step in enumerate(steps):
                try:
                    if not isinstance(step, dict):
                        raise ValueError('invalid step')
                    skill_id = step.get('skill_id')
                    status = step.get('status', 'unknown')
                    if not isinstance(skill_id, str) or not skill_id or not isinstance(status, str):
                        raise ValueError('invalid step identity or status')
                    evidence = step.get('execution_evidence') or {}
                    identity = evidence.get('artifact_identity') or {} if isinstance(evidence, dict) else {}
                    if not isinstance(identity, dict):
                        identity = {}
                    bound_hash = identity.get('artifact_hash') if (identity.get('skill_id') == skill_id
                                 and identity.get('skill_version') == versions.get(skill_id)) else None
                    # A task verdict cannot replace a missing or ambiguous step verdict.
                    passed, scope = _step_verdict(row, steps, step)
                    outcome = 'passed' if passed is True else 'failed' if passed is False else 'not_evaluated'
                    event(ident=f'runtime:{row["execution_id"]}:{index}',
                          skill_id=skill_id, version=versions.get(skill_id),
                          artifact_hash=bound_hash, status=status, outcome=outcome,
                          kind='task_execution', validator='runtime_verifier',
                          reference=f'/api/v1/execution/history/{quote(row["execution_id"], safe="")}',
                          extra={'execution_id': row['execution_id'], 'step_index': index,
                                 'validation_scope': scope, 'task_verifier_passed': row.get('verifier_passed'),
                                 'artifact_hash_verified': False})
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    diagnostics['issues'].append({'receipt': row['execution_id'], 'step_index': index,
                                                  'reason': type(exc).__name__})
    return nodes, edges, diagnostics
