import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/views/startup/editor.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { applyStartupUpload } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

test('late startup upload only replaces the originally selected slot', () => {
  const original = { gifAssetId: null, fallbackAssetId: 'fallback-1' }
  const afterUpload = applyStartupUpload(original, {
    role: 'gif', expectedAssetId: null, assetId: 'new-gif',
  })
  assert.deepEqual(afterUpload, { gifAssetId: 'new-gif', fallbackAssetId: 'fallback-1' })
  assert.deepEqual(original, { gifAssetId: null, fallbackAssetId: 'fallback-1' })

  const changedWhileUploading = { gifAssetId: 'another-gif', fallbackAssetId: 'fallback-1' }
  assert.equal(applyStartupUpload(changedWhileUploading, {
    role: 'gif', expectedAssetId: null, assetId: 'new-gif',
  }), changedWhileUploading)
})
