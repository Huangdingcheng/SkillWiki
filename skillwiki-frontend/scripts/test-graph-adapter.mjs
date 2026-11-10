import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import { setImmediate } from 'node:timers/promises'
import ts from 'typescript'

// Execute the actual page callbacks. Only React state/ref primitives, navigation,
// and the external transport are replaced; responses settle in controlled order.
const source = await readFile(new URL('../src/pages/SkillGraph.tsx', import.meta.url), 'utf8')
const ast = ts.createSourceFile('SkillGraph.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const component = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'SkillGraph')
const declarations = component.body.statements.flatMap(node => ts.isVariableStatement(node) ? [...node.declarationList.declarations] : [])
const declaration = name => declarations.find(node => node.name.getText(ast) === name)
const compile = code => ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText
const helpers = ast.statements.filter(node => ts.isFunctionDeclaration(node) && node !== component).map(node => node.getText(ast)).join('\n')
const effects = component.body.statements.filter(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression) && node.expression.expression.getText(ast) === 'useEffect')

function fixture(view = 'skill_only', id = 'existing') {
  return { view, source_endpoint: '/test', nodes: [{ id, kind: 'skill', name: id, tags: [], metadata: {} }], edges: [], stats: {}, metadata: {}, validation_evidence: {} }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function harness(initial = {}) {
  const calls = [], navigations = [], pending = [], timers = new Map()
  const context = vm.createContext({
    graphData: fixture(), viewMode: 'skill_only', mode: 'full', depth: 2, centerSkillId: null,
    loading: false, subgraphLoading: false, error: null, selectedNode: null,
    edgeFilter: [], nodeSelection: null, selectedEdgeId: null, location: { search: '' },
    useCallback: fn => fn, useRef: value => ({ current: value }),
    getApiErrorMessage: (error, fallback) => error.message || fallback,
    URLSearchParams, encodeURIComponent,
    navigate: (...args) => navigations.push(args),
    graphApi: Object.fromEntries(['view', 'subgraph'].map(kind => [kind, (...args) => {
      const task = deferred(); calls.push({ kind, args }); pending.push(task); return task.promise
    }])),
    window: { setTimeout: fn => { const key = Symbol(); timers.set(key, fn); return key }, clearTimeout: key => timers.delete(key) },
    ...initial,
  })
  const mutations = []
  for (const name of ['GraphData', 'ViewMode', 'Mode', 'Loading', 'SubgraphLoading', 'Error', 'SelectedNodeId', 'SelectedEdgeId', 'CenterSkillId', 'EdgeFilter', 'SubgraphDepth']) {
    const key = name === 'SelectedNodeId' ? 'nodeSelection' : name[0].toLowerCase() + name.slice(1)
    context['set' + name] = value => { mutations.push(key); context[key] = typeof value === 'function' ? value(context[key]) : value }
  }
  vm.runInContext(compile(helpers), context)
  for (const node of declarations.filter(node => ts.isCallExpression(node.initializer) && node.initializer.expression.getText(ast) === 'useRef')) {
    vm.runInContext(compile('var ' + node.getText(ast)), context)
  }
  for (const name of ['resetSelection', 'loadGraphForView', 'loadSubgraph', 'handleViewChange', 'returnToFullGraph', 'openWiki', 'expandSelectedSkill']) {
    vm.runInContext(compile('var ' + declaration(name).getText(ast)), context)
  }
  const runEffect = statement => vm.runInContext(compile('(' + statement.expression.arguments[0].getText(ast) + ')()'), context)
  const initialEffects = effects.filter(node => !node.getText(ast).includes('graphShellRef') && !node.getText(ast).includes('containerRef'))
  const mount = () => initialEffects.map(runEffect).filter(fn => typeof fn === 'function')
  const flushTimers = () => { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()) }
  return { context, calls, pending, navigations, mutations, mount, flushTimers }
}
const tests = []
const test = (name, run) => tests.push({ name, run })

test('heterogeneous skill actions use metadata identity for both Wiki and subgraph', async () => {
  const h = harness({ selectedNode: { id: 'skill:old-id', kind: 'skill', metadata: { skill_id: 'canonical-id' } } })
  h.context.openWiki(); h.context.expandSelectedSkill()
  assert.equal(h.navigations[0][0], '/wiki?skill_id=canonical-id')
  assert.equal(h.calls[0].args[0], 'canonical-id')
  h.pending[0].resolve(fixture()); await Promise.resolve()
})
test('legacy prefixed skill IDs fall back to canonical IDs and non-skill nodes cannot navigate', () => {
  const h = harness({ selectedNode: { id: 'skill:legacy-id', kind: 'skill', metadata: {} } })
  h.context.openWiki()
  assert.equal(h.navigations[0][0], '/wiki?skill_id=legacy-id')
  h.context.selectedNode = { id: 'execution:run-id', kind: 'execution', metadata: { skill_id: 'legacy-id' } }
  h.context.openWiki(); h.context.expandSelectedSkill()
  assert.equal(h.navigations.length, 1); assert.equal(h.calls.length, 0)
})
test('the existing eight metadata rows retain binding and original outcome', () => {
  const { context } = harness()
  const metadata = { source: 'saved_receipt', created_at: 'today', updated_at: 'today', execution_id: 'run-1', status: 'completed', validator: 'saved', identity_binding: 'skill_id_and_version', qualification_proof: false, event_type: 'harness_execution', skill_id: 'canonical-id', skill_version: '1.0.0', receipt_ref: '/harness/loop-1', recorded_outcome: 'passed', artifact_binding: 'unavailable', outcome: 'unknown' }
  const visible = Object.fromEntries(context.metadataEntries(metadata).slice(0, 8))
  for (const key of ['event_type', 'skill_id', 'skill_version', 'receipt_ref', 'recorded_outcome', 'artifact_binding', 'outcome']) assert.equal(visible[key], metadata[key], 'Missing ' + key)
})
test('explicit synthetic and mock flags remain visible even when false', () => {
  const { context } = harness()
  const visible = Object.fromEntries(context.metadataEntries({ ...Object.fromEntries(Array.from({ length: 12 }, (_, i) => ['extra_' + i, i])), synthetic: true, mock: false }).slice(0, 8))
  assert.equal(visible.synthetic, true); assert.equal(visible.mock, false)
})
test('a failed view switch never relabels previous graph data', async () => {
  const h = harness()
  h.context.handleViewChange('provenance')
  assert.ok(!h.context.graphData || h.context.viewMode === h.context.graphData.view, 'Pending request relabelled old graph')
  h.pending[0].reject(new Error('offline')); await setImmediate()
  assert.ok(!h.context.graphData || h.context.viewMode === h.context.graphData.view, 'Failure relabelled old graph')
  assert.equal(h.context.loading, false); assert.equal(h.context.error, 'offline')
})
test('latest full-view request wins when responses arrive out of order', async () => {
  const h = harness()
  const first = h.context.loadGraphForView('provenance')
  const latest = h.context.loadGraphForView('version_impact')
  h.pending[1].resolve(fixture('version_impact', 'latest')); await latest
  h.pending[0].resolve(fixture('provenance', 'stale')); await first
  assert.equal(h.context.graphData.nodes[0].id, 'latest')
  assert.equal(h.context.viewMode, 'version_impact')
})
test('stale failure cannot clear loading or replace the active error state', async () => {
  const h = harness()
  const first = h.context.loadGraphForView('provenance')
  const latest = h.context.loadGraphForView('version_impact')
  h.pending[0].reject(new Error('stale failure')); await first
  assert.equal(h.context.loading, true); assert.equal(h.context.error, null)
  h.pending[1].resolve(fixture('version_impact')); await latest
})
test('failed subgraph preserves matching graph mode and selected identity', async () => {
  const h = harness({ graphData: fixture('provenance'), viewMode: 'provenance', nodeSelection: 'skill:existing' })
  const task = h.context.loadSubgraph('existing', 2)
  assert.equal(h.context.viewMode, h.context.graphData.view)
  h.pending[0].reject(new Error('subgraph offline')); await task
  assert.equal(h.context.viewMode, 'provenance'); assert.equal(h.context.nodeSelection, 'skill:existing')
  assert.equal(h.context.mode, 'full'); assert.equal(h.context.subgraphLoading, false)
})
test('late subgraph success cannot replace a newer full view', async () => {
  const h = harness()
  const first = h.context.loadSubgraph('old-skill', 2)
  const latest = h.context.loadGraphForView('provenance')
  h.pending[1].resolve(fixture('provenance', 'current')); await latest
  h.pending[0].resolve(fixture('skill_only', 'old-skill')); await first
  assert.equal(h.context.graphData.nodes[0].id, 'current'); assert.equal(h.context.mode, 'full')
  assert.equal(h.context.subgraphLoading, false)
})
test('late full-view success cannot replace a newer subgraph', async () => {
  const h = harness()
  const first = h.context.loadGraphForView('provenance')
  const latest = h.context.loadSubgraph('current', 3)
  h.pending[1].resolve(fixture('skill_only', 'current')); await latest
  h.pending[0].resolve(fixture('provenance', 'stale')); await first
  assert.equal(h.context.graphData.nodes[0].id, 'current'); assert.equal(h.context.mode, 'subgraph')
  assert.equal(h.context.viewMode, 'skill_only'); assert.equal(h.context.centerSkillId, 'current')
})
test('failed subgraph reload retains the previous center and accepted depth', async () => {
  const h = harness()
  const first = h.context.loadSubgraph('existing', 2)
  h.pending[0].resolve(fixture()); await first
  assert.equal(h.context.subgraphDepth, 2)
  h.context.depth = 3
  const task = h.context.loadSubgraph('other', 3)
  h.pending[1].reject(new Error('offline')); await task
  assert.equal(h.context.graphData.nodes[0].id, 'existing')
  assert.equal(h.context.mode, 'subgraph'); assert.equal(h.context.centerSkillId, 'existing')
  assert.equal(h.context.subgraphDepth, 2)
})
test('effect cleanup prevents outstanding transport results from updating unmounted state', async () => {
  const h = harness()
  const cleanups = h.mount()
  const task = h.context.loadGraphForView('provenance')
  cleanups.forEach(cleanup => cleanup())
  h.mutations.length = 0
  h.pending[0].resolve(fixture('provenance')); await task
  assert.equal(h.mutations.length, 0)
})
test('changing depth after a failed initial load does not retry the empty graph', async () => {
  const h = harness({ graphData: null, loading: true })
  h.mount(); h.flushTimers()
  assert.equal(h.calls.length, 1)
  h.pending[0].reject(new Error('offline')); await setImmediate()
  h.context.depth = 3
  h.mount(); h.flushTimers()
  assert.equal(h.calls.length, 1)
})

let failed = 0
for (const { name, run } of tests) {
  try { await run(); console.log('PASS ' + name) }
  catch (error) { failed += 1; console.error('FAIL ' + name + '\n' + error.stack) }
}
console.log((tests.length - failed) + ' passed, ' + failed + ' failed, ' + tests.length + ' total')
process.exitCode = failed ? 1 : 0
