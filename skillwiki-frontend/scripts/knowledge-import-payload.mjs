// Execute actual production builders without rendering or network requests.
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const path = new URL('../src/pages/KnowledgeImport.tsx', import.meta.url)
const source = readFileSync(process.argv[2] || path, 'utf8')
const file = ts.createSourceFile(path.pathname, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const helpers = file.statements.filter(node => ts.isFunctionDeclaration(node) && node.name?.text !== 'KnowledgeImport')
let payloadDeclaration
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(file) === 'buildPayload') payloadDeclaration = node
  ts.forEachChild(node, visit)
}
visit(file)
if (!payloadDeclaration) throw new Error('Production buildPayload declaration was not found')
const executionSource = helpers.map(node => node.getText(file)).join('\n') + [
  '\nfunction run(unit, sourceType) {',
  'const selectedUnit = unit;',
  'const activeTab = sourceType;',
  'const content = unit.raw_content;',
  'const currentDraft = buildCandidateDraft(unit, sourceType);',
  'const ' + payloadDeclaration.getText(file) + ';',
  'return {draft: currentDraft, payload: buildPayload()};',
  '}',
  'run(input.unit, input.sourceType);',
].join('\n')
const js = ts.transpileModule(executionSource, {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None, jsx: ts.JsxEmit.ReactJSX}}).outputText
const input = JSON.parse(readFileSync(0, 'utf8'))
try {
  console.log(JSON.stringify({ok: true, ...vm.runInNewContext(js, {input}, {timeout: 5000})}))
} catch (error) {
  console.log(JSON.stringify({ok: false, error: String(error)}))
}
