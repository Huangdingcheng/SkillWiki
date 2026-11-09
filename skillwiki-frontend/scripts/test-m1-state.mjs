import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

async function loadTs(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { skillIdFromSearch, searchWithSkillId, skillContextPath } = await loadTs('../src/utils/skillContext.ts')
const { executionEvidence } = await loadTs('../src/utils/executionEvidence.ts')
const { recoveryDraftId } = await loadTs('../src/utils/recoveryDraft.ts')

assert.equal(skillIdFromSearch(''), null)
assert.equal(skillIdFromSearch('?skill_id=%20%20'), null)
assert.equal(skillContextPath('/versions'), '/versions')
const id = 'candidate with spaces/&?中文'
const initial = searchWithSkillId('?proposal_id=review-1', id)
assert.equal(skillIdFromSearch(initial), id)
assert.equal(initial.get('proposal_id'), 'review-1')
assert.equal(skillIdFromSearch(new URLSearchParams(skillContextPath('/versions', id).split('?')[1])), id)
const nextVersion = searchWithSkillId(initial, 'new-version-id')
assert.equal(skillIdFromSearch(nextVersion.toString()), 'new-version-id')
assert.equal(nextVersion.getAll('skill_id').length, 1)
assert.equal(skillIdFromSearch(searchWithSkillId(nextVersion, null)), null)
assert.equal(searchWithSkillId(nextVersion, null).get('proposal_id'), 'review-1')

const recovery = { skill_id: 'original', restored_skill_id: 'new-recovery', restored_state: 'S2', live_skill_updated: false, draft_created: true }
assert.equal(recoveryDraftId(recovery), 'new-recovery')
for (const change of [{ restored_skill_id: undefined }, { restored_skill_id: 'original' }, { restored_state: 'S4' }, { live_skill_updated: true }, { live_skill_updated: undefined }, { draft_created: false }, { draft_created: undefined }]) {
  assert.throws(() => recoveryDraftId({ ...recovery, ...change }), /separate S2 recovery draft/)
}

const base = { status: 'success', steps: [], experience_recorded: false }
const unverified = executionEvidence(base)
assert.equal(unverified.alertType, 'warning')
assert.equal(unverified.verificationLabel, 'Verification not confirmed')
assert.deepEqual(unverified.steps.map(step => step.status), ['finish', 'wait', 'wait'])
const verified = executionEvidence({ ...base, experience_recorded: true, verifier_passed: true })
assert.equal(verified.alertType, 'success')
assert.deepEqual(verified.steps.map(step => step.status), ['finish', 'finish', 'finish'])
assert.equal(executionEvidence({ ...base, verifier_passed: false }).alertType, 'error')
for (const status of ['failed', 'error']) {
  const failed = executionEvidence({ ...base, status, verifier_passed: true })
  assert.equal(failed.alertType, 'error')
  assert.equal(failed.steps[0].status, 'error')
}
for (const status of ['partial', 'blocked', 'pending', 'unknown']) {
  const incomplete = executionEvidence({ ...base, status, verifier_passed: true })
  assert.equal(incomplete.alertType, 'warning')
  assert.equal(incomplete.steps[0].status, 'wait')
}
assert.equal(executionEvidence({ ...base, experience_recorded: 'true' }).experienceRecorded, false)
assert.equal(executionEvidence({ ...base, verifier_passed: 'true' }).verificationLabel, 'Verification not confirmed')

const demo = await readFile(new URL('../src/pages/SelfEvolutionDemo.tsx', import.meta.url), 'utf8')
assert.doesNotMatch(demo, /setTimeout|setInterval|setPhase|setPhase|Self-Evolution Loop Completed|Reuse signals updated/)
const versions = await readFile(new URL('../src/pages/VersionControl.tsx', import.meta.url), 'utf8')
assert.equal(versions.split('handleSelect(newSkill.skill_id, { loadCurrentDiff: true })').length - 1, 2)
assert.equal(versions.includes('skills.some(skill => skill.skill_id === querySkillId)'), false)
console.log('M1 state checks passed: URL refresh/new-version round trips, no-ID compatibility, independent backend evidence, failed/partial/unconfirmed states, no fabricated completion timers.')
