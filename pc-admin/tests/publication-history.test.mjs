import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'
const source = readFileSync(new URL('../src/shared/publication-history.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { rollbackBody, parseRollbackIntent, historyScope, matchesRollbackResult, keepRollbackIntent, persistRollbackIntent } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const version = '12345678-1234-4234-8234-123456789abc'
test('rollback body binds target and both revisions and requires a reason', () => {
 assert.deepEqual(rollbackBody(version, 4, 2, ' 修复错误 '), { expectedRevision: 4, expectedPublicationRevision: 2, versionId: version, reason: '修复错误' })
 assert.throws(() => rollbackBody(version, 4, 2, ' '))
 assert.throws(() => rollbackBody(version, 4, -1, '原因'))
})
test('pending recovery retains exact body and key only within actor and target', () => {
 const scope = historyScope('a', '/pages/home')
 const intent = { scope, key: version, body: rollbackBody(version, 4, 2, '原因') }
 assert.deepEqual(parseRollbackIntent(JSON.stringify(intent), scope), intent)
 assert.equal(parseRollbackIntent(JSON.stringify(intent), historyScope('b', '/pages/home')), null)
 assert.equal(parseRollbackIntent(JSON.stringify(intent), historyScope('a', '/startup')), null)
 assert.equal(parseRollbackIntent('{', scope), null)
 assert.equal(parseRollbackIntent(JSON.stringify({ ...intent, password: 'secret' }), scope), null)
})
test('recovery accepts only the original historical version and next publication revision', () => {
 const body = rollbackBody(version, 4, 2, '原因')
 assert.equal(matchesRollbackResult({ versionId: version, revision: 1, publicationRevision: 3, draftRevision: 4 }, body), true)
 assert.equal(matchesRollbackResult({ versionId: 'other', revision: 1, publicationRevision: 3, draftRevision: 4 }, body), false)
 assert.equal(matchesRollbackResult({ versionId: version, revision: 1, publicationRevision: 5, draftRevision: 4 }, body), false)
})

test('unknown outcomes and recovery authorization failures retain original request', () => {
 for (const status of [undefined, 408, 429, 500, 503]) assert.equal(keepRollbackIntent(status, false), true)
 assert.equal(keepRollbackIntent(403, true), true)
 assert.equal(keepRollbackIntent(409, true), true)
 assert.equal(keepRollbackIntent(422, false), false)
})
test('new rollback requires a durable verified recovery record before network submission', () => {
 const intent = { scope: historyScope('a', '/startup'), key: version, body: rollbackBody(version, 4, 2, '原因') }
 let raw = null
 assert.equal(persistRollbackIntent({ setItem: (_, value) => { raw = value }, getItem: () => raw }, 'key', intent), true)
 assert.equal(persistRollbackIntent({ setItem: () => { throw new Error('quota') }, getItem: () => null }, 'key', intent), false)
 assert.equal(persistRollbackIntent({ setItem: () => {}, getItem: () => '{' }, 'key', intent), false)
})
