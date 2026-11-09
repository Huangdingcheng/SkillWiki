import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Execute the actual client module with only its transport boundary replaced.
const source = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
let { outputText } = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
} })
const calls = []
globalThis.__lifecycleHttpTest = {
  create: () => ({
    post: async (...args) => { calls.push(args); return { data: { skill_id: 'actual-id', state: 'S4' } } },
    interceptors: { request: { use() {} }, response: { use() {} } },
  }),
}
outputText = outputText.replace(/import axios from ['"]axios['"];?/, 'const axios = globalThis.__lifecycleHttpTest;')
const { lifecycleApi } = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'))
try {
  const result = await lifecycleApi.release('actual-id')
  assert.equal(result.skill_id, 'actual-id')
  assert.equal(result.state, 'S4')
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/lifecycle/actual-id/release')
  assert.deepEqual(calls[0][1], {}, 'Native release requires a JSON object body, even with default author')
  console.log('PASS existing release action posts a JSON body and returns the backend identity')
} finally {
  delete globalThis.__lifecycleHttpTest
}
