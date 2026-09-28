import test from 'node:test'
import assert from 'node:assert/strict'
import { exportStatusText, canDownloadExport, isDefinitiveExportError, exportFileName } from '../src/views/exports/task.mjs'

test('only ready and unexpired exports can be downloaded', () => {
  const now = Date.parse('2026-09-28T08:00:00Z')
  const task = { status: 'READY', expiresAt: '2026-09-29T08:00:00Z' }
  assert.equal(canDownloadExport(task, now), true)
  assert.equal(canDownloadExport({ ...task, status: 'RUNNING' }, now), false)
  assert.equal(canDownloadExport(task, Date.parse(task.expiresAt)), false)
  assert.equal(canDownloadExport({ ...task, expiresAt: null }, now), false)
})

test('known rejection clears pending request but uncertain result retains its request key', () => {
  assert.equal(isDefinitiveExportError(403), true)
  assert.equal(isDefinitiveExportError(429), true)
  assert.equal(isDefinitiveExportError(500), false)
  assert.equal(isDefinitiveExportError(0), false)
})

test('export labels and download names are fixed locally', () => {
  assert.equal(exportStatusText('READY'), '可下载')
  assert.equal(exportStatusText('UNKNOWN'), '状态待核实')
  assert.equal(exportFileName('AUDIT', 'a/b'), 'audit-export-a_b.csv')
  assert.equal(exportFileName('BUSINESS', '123'), 'business-export-123.csv')
})
