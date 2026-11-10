import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'
import vm from 'node:vm'

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
assert.doesNotMatch(demo, /setInterval|setPhase|Self-Evolution Loop Completed|Reuse signals updated/)
// A deferred backend read is valid. Execute the actual timer-owning effect and
// check that only the backend can populate its result, including failure and
// unmount/stale-response paths. A timer alone must never fabricate completion.
const demoAst = ts.createSourceFile('SelfEvolutionDemo.tsx', demo, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const timerEffects = []
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(demoAst) === 'useEffect'
      && node.arguments[0]?.getText(demoAst).includes('window.setTimeout')) {
    timerEffects.push(node.arguments[0])
  }
  ts.forEachChild(node, visit)
}
visit(demoAst)
assert.equal(timerEffects.length, 1)
const effectJs = ts.transpileModule('var effect = ' + timerEffects[0].getText(demoAst), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText
function deferredRead() {
  let resolve, reject, timer, cleanup, selected, error
  const runs = [], calls = [], generation = { current: 0 }
  const pending = new Promise((yes, no) => { resolve = yes; reject = no })
  const context = vm.createContext({
    skillId: 'actual-skill', generation,
    window: { setTimeout: (fn, delay) => { assert.equal(delay, 0); timer = fn; return 1 }, clearTimeout: () => { timer = undefined } },
    setRuns: value => runs.push(value), setError: value => { error = value },
    setLoading: () => {}, setSelectedRunId: value => { selected = value },
    evolutionApi: { componentRuns: id => { calls.push(id); return pending } },
    getApiErrorMessage: err => err.message,
  })
  vm.runInContext(effectJs, context)
  cleanup = context.effect()
  return { calls, runs, resolve, reject, start: () => timer(), cleanup,
    selected: () => selected, error: () => error }
}
async function flushRead() { for (let i = 0; i < 6; i += 1) await Promise.resolve() }
const saved = [{ run_id: 'real-backend-run', status: 'failed' }]
const loadingRead = deferredRead()
assert.equal(loadingRead.calls.length, 0)
assert.equal(loadingRead.runs.length, 0)
loadingRead.start()
assert.deepEqual(loadingRead.calls, ['actual-skill'])
assert.equal(loadingRead.runs.length, 1)
assert.equal(loadingRead.runs[0].length, 0)
assert.equal(loadingRead.selected(), undefined)
loadingRead.resolve({ runs: saved })
await flushRead()
assert.equal(loadingRead.runs[1], saved)
assert.equal(loadingRead.selected()(undefined), 'real-backend-run')
const failedRead = deferredRead()
failedRead.start()
failedRead.reject(new Error('backend unavailable'))
await flushRead()
assert.equal(failedRead.runs.length, 1)
assert.equal(failedRead.selected(), undefined)
assert.equal(failedRead.error(), 'backend unavailable')
const staleRead = deferredRead()
staleRead.start()
staleRead.cleanup()
staleRead.resolve({ runs: saved })
await flushRead()
assert.equal(staleRead.runs.length, 1)
assert.equal(staleRead.selected(), undefined)
const versions = await readFile(new URL('../src/pages/VersionControl.tsx', import.meta.url), 'utf8')
assert.equal(versions.split('handleSelect(newSkill.skill_id, { loadCurrentDiff: true })').length - 1, 2)
assert.equal(versions.includes('skills.some(skill => skill.skill_id === querySkillId)'), false)
console.log('M1 state checks passed: URL refresh/new-version round trips, no-ID compatibility, independent backend evidence, failed/partial/unconfirmed states, no fabricated completion timers.')
