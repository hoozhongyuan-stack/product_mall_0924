import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'
const source = readFileSync(new URL('../src/shared/media-library.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { selectionError, assetQuery, validateAssetFile, selectionStillCurrent } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const asset = { assetId: 'a', kind: 'IMAGE', width: 600, height: 600, availability: 'READY' }
test('selector rejects missing, incompatible, nonsquare and duplicate assets', () => {
  assert.equal(selectionError(asset, { kind: 'IMAGE', square: true }), '')
  assert.match(selectionError({ ...asset, availability: 'EXPIRED' }, { kind: 'IMAGE' }), /不可用/)
  assert.match(selectionError({ ...asset, availability: 'MISSING' }, { kind: 'IMAGE' }), /不可用/)
  assert.match(selectionError(asset, { kind: 'VIDEO' }), /类型/)
  assert.match(selectionError({ ...asset, height: 400 }, { kind: 'IMAGE', square: true }), /方形/)
  assert.match(selectionError(asset, { kind: 'IMAGE', excludedIds: ['a'] }), /已选择/)
})
test('query encodes filters and limits upload-only users to unbound assets', () => {
  const query = new URLSearchParams(assetQuery({ kind: 'GIF', q: 'a & b', binding: 'BOUND', page: 2, square: true }, false))
  assert.equal(query.get('binding'), 'UNBOUND')
  assert.equal(query.get('q'), 'a & b')
  assert.equal(query.get('square'), '1')
  assert.equal(query.get('page'), '2')
})
test('upload checks real size/type and selection rejects an obsolete target', () => {
  assert.equal(validateAssetFile({ type: 'image/png', size: 1 }, 'IMAGE'), '')
  assert.match(validateAssetFile({ type: 'image/png', size: 0 }, 'IMAGE'), /文件/)
  assert.match(validateAssetFile({ type: 'image/gif', size: 10 }, 'IMAGE'), /类型/)
  assert.equal(selectionStillCurrent('draft-1', 'draft-2'), false)
  assert.equal(selectionStillCurrent('draft-1', 'draft-1'), true)
})
test('global readers can reuse bound materials and blank filters stay absent', () => {
  const query = new URLSearchParams(assetQuery({ binding: 'BOUND', q: '   ', page: 1 }, true))
  assert.equal(query.get('binding'), 'BOUND')
  assert.equal(query.has('q'), false)
  assert.equal(query.has('kind'), false)
  assert.equal(query.has('square'), false)
  assert.equal(new URLSearchParams(assetQuery({ page: 1 }, true)).has('binding'), false)
})
test('video and GIF uploads enforce their own MIME and byte boundaries', () => {
  assert.equal(validateAssetFile({ type: 'video/mp4', size: 50 * 1024 * 1024 }, 'VIDEO'), '')
  assert.match(validateAssetFile({ type: 'video/mp4', size: 50 * 1024 * 1024 + 1 }, 'VIDEO'), /50 MiB/)
  assert.equal(validateAssetFile({ type: 'image/gif', size: 10 * 1024 * 1024 }, 'GIF'), '')
  assert.match(validateAssetFile({ type: 'image/gif', size: 10 * 1024 * 1024 + 1 }, 'GIF'), /10 MiB/)
  assert.match(selectionError({ ...asset, width: undefined }, { kind: 'IMAGE', square: true }), /方形/)
})
