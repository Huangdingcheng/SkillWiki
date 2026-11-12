import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const source = await readFile(new URL('../src/utils/maintenanceProposal.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { acceptedRepairPatch, maintenanceParentContent } = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'))
const parent = { skill_id: 'parent', version: '1.0.0', name: 'demo', description: 'contract',
  interface: { input_schema: { type: 'object' }, output_schema: { type: 'object' } },
  implementation: { language: 'python', code: 'old', prompt_format: null, request_body_validation: null },
  evaluation: { verifier_specs: [], test_case_refs: [], benchmark_task_ids: [], harness_validation: { old: true } } }
const patch = { interface: parent.interface, implementation: { language: 'python', code: 'new' } }
const proposal = { proposal_id: 'repair', status: 'accepted', skill_id: 'parent', metadata: {
  parent_version: '1.0.0', parent_content: maintenanceParentContent(parent), patched_skill: patch } }
assert.deepEqual(acceptedRepairPatch(proposal, parent), patch)
assert.equal(parent.implementation.code, 'old')
assert.throws(() => acceptedRepairPatch({ ...proposal, status: 'pending' }, parent), /Accept/)
assert.throws(() => acceptedRepairPatch(proposal, { ...parent, skill_id: 'other' }), /different/)
assert.throws(() => acceptedRepairPatch(proposal, { ...parent, version: '1.0.1' }), /different/)
assert.throws(() => acceptedRepairPatch(proposal, { ...parent, implementation: { ...parent.implementation, code: 'changed' } }), /changed/)
assert.throws(() => acceptedRepairPatch(proposal, { ...parent, evaluation: { ...parent.evaluation, verifier_specs: ['new-contract'] } }), /changed/)
assert.deepEqual(acceptedRepairPatch(proposal, { ...parent, evaluation: { ...parent.evaluation, harness_validation: { refreshed: true } } }), patch)
assert.throws(() => acceptedRepairPatch({ ...proposal, metadata: { ...proposal.metadata, patched_skill: null } }, parent), /no saved/)
assert.throws(() => acceptedRepairPatch({ ...proposal, metadata: { ...proposal.metadata, patched_skill: { ...patch, state: 'S4' } } }, parent), /unsupported/)
console.log('PASS 10 proposal checks: accepted parent, stale content, changed contract, mutable proof, missing patch, prohibited fields')
