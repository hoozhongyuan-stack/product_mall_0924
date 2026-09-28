import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'
const source = readFileSync(new URL('../src/shared/publication-sync.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { publicationIntent, currentPublication, applyCurrentPublication } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
test('original publish body and confirmation revision remain fixed across rollback and draft edits', () => {
 const draft = { revision: 4, publicationRevision: 2 }
 const intent = publicationIntent('/pages/home', 'home', draft, 'original-key')
 draft.revision = 5; draft.publicationRevision = 7
 assert.deepEqual(intent.body, { expectedRevision: 4, expectedPublicationRevision: 2 })
 assert.equal(intent.key, 'original-key')
 assert.throws(() => { intent.body.expectedPublicationRevision = 7 }, TypeError)
 assert.throws(() => publicationIntent('/startup', 'startup', { revision: 0 }, 'key'))
 assert.deepEqual(publicationIntent('/startup', 'startup', { revision: 1 }, 'key').body, { expectedRevision: 1, expectedPublicationRevision: 0 })
})
test('successful original receipt after a later rollback reads authoritative pointer and version', async () => {
 const paths = []
 const result = await currentPublication(async path => {
  paths.push(path)
  return paths.length === 1 ? { currentVersionId: 'current-v1', publicationRevision: 3 } : { versionId: 'current-v1', revision: 1 }
 }, '/pages/home')
 assert.deepEqual(paths, ['/pages/home/versions?page=1&pageSize=1', '/pages/home/versions/current-v1'])
 assert.deepEqual(result, { publishedRevision: 1, publishedVersionId: 'current-v1', publicationRevision: 3 })
 const config = { components: [] }
 const draft = { revision: 5, config, name: '未保存名称', publishedRevision: 2 }
 const updated = applyCurrentPublication(draft, result)
 assert.equal(updated.revision, 5); assert.equal(updated.config, config); assert.equal(updated.name, '未保存名称')
 assert.equal(draft.publishedRevision, 2); assert.equal(updated.publicationRevision, 3)
})
test('failed pointer/detail refresh never supplies a partial publication state', async () => {
 await assert.rejects(currentPublication(async () => { throw new Error('offline') }, '/startup'), /offline/)
 await assert.rejects(currentPublication(async path => {
  if (path.includes('?')) return { currentVersionId: 'old', publicationRevision: 9 }
  throw new Error('detail offline')
 }, '/startup'), /detail offline/)
 await assert.rejects(currentPublication(async () => ({ currentVersionId: 'v', publicationRevision: -1 }), '/startup'))
 await assert.rejects(currentPublication(async path => path.includes('?') ? { currentVersionId: 'v', publicationRevision: 1 } : { versionId: 'different', revision: 1 }, '/startup'))
 await assert.rejects(currentPublication(async path => path.includes('?') ? { currentVersionId: 'v', publicationRevision: 1 } : { versionId: 'v', revision: 0 }, '/startup'))
})
test('unpublished pointer is synchronized without requesting a nonexistent detail', async () => {
 let calls = 0
 const current = await currentPublication(async () => { calls++; return { currentVersionId: null, publicationRevision: 0 } }, '/startup')
 assert.equal(calls, 1)
 assert.deepEqual(current, { publishedRevision: null, publishedVersionId: null, publicationRevision: 0 })
})
test('publish completion accepts a valid original receipt and rejects malformed or different operations', async () => {
 const { matchesPublishResult } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
 const body = { expectedRevision: 4, expectedPublicationRevision: 2 }
 const valid = { versionId: '12345678-1234-4234-8234-123456789abc', revision: 4, publicationRevision: 3, draftRevision: 4 }
 assert.equal(matchesPublishResult(valid, body), true)
 for (const invalid of [null, {}, { ...valid, versionId: 'bad' }, { ...valid, revision: 5 }, { ...valid, publicationRevision: 4 }, { ...valid, draftRevision: 5 }]) assert.equal(matchesPublishResult(invalid, body), false)
})
test('durable ordinary publish recovery is scoped and preserves exact frozen body without password', async () => {
 const { parsePublishIntent, persistPublishIntent } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
 const intent = publicationIntent('/startup', 'startup', { revision: 4, publicationRevision: 2 }, '12345678-1234-4234-8234-123456789abc')
 let raw = null
 const storage = { setItem: (_, value) => { raw = value }, getItem: () => raw }
 assert.equal(persistPublishIntent(storage, 'key', intent), true)
 assert.deepEqual(parsePublishIntent(raw, '/startup', 'startup'), intent)
 assert.equal(parsePublishIntent(raw, '/pages/home', 'home'), null)
 assert.equal(parsePublishIntent('{', '/startup', 'startup'), null)
 assert.equal(parsePublishIntent(JSON.stringify({ ...intent, password: 'secret' }), '/startup', 'startup'), null)
 assert.equal(parsePublishIntent(JSON.stringify({ ...intent, body: { ...intent.body, expectedRevision: 0 } }), '/startup', 'startup'), null)
 assert.equal(parsePublishIntent(JSON.stringify({ ...intent, key: 'invalid' }), '/startup', 'startup'), null)
 assert.equal(persistPublishIntent({ setItem: () => { throw new Error('quota') }, getItem: () => null }, 'key', intent), false)
 assert.equal(persistPublishIntent({ setItem: () => {}, getItem: () => '{' }, 'key', intent), false)
})
