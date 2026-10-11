import { test } from 'node:test'
import assert from 'node:assert/strict'
import { statusTone, pageCount, pageWindow, toggleSelection, selectedOnPage, filterLoadedRows } from '../src/shared/list/list-state.mjs'

test('semantic statuses keep pending, processing, success and unknown distinct', () => {
  assert.equal(statusTone('PENDING'), 'pending')
  assert.equal(statusTone('APPROVED'), 'processing')
  assert.equal(statusTone('PAID'), 'success')
  assert.equal(statusTone('CONFIGURED'),'success')
  assert.equal(statusTone('UNCONFIGURED'),'pending')
  assert.equal(statusTone('APPROVED_PENDING_PAYMENT'),'pending')
  assert.equal(statusTone('REJECTED'), 'danger')
  assert.equal(statusTone('DRAFT'), 'neutral')
  assert.equal(statusTone('UNRECOGNIZED'), 'neutral')
})
test('full loaded collections paginate safely without mutating inputs', () => {
  const rows = Object.freeze(Array.from({length: 45}, (_, i) => Object.freeze({id: String(i)})))
  assert.equal(pageCount(45, 20), 3)
  assert.equal(pageCount(0, 50), 1)
  assert.equal(pageCount(-1, 0), 1)
  assert.equal(pageWindow(rows, 2, 20).items[0].id, '20')
  assert.equal(pageWindow(rows, 999, 20).page, 3)
  assert.equal(pageWindow(rows, -2, 50).items.length, 45)
  assert.equal(pageWindow(rows, NaN, 0).items.length, 20)
  assert.deepEqual(pageWindow([], 4, 100), {items: [], page: 1, pages: 1, total: 0})
})
test('selection only includes current allowed rows and never duplicates', () => {
  const ids = Object.freeze(['a'])
  assert.deepEqual(toggleSelection(ids, 'b', true, ['a', 'b']), ['a', 'b'])
  assert.deepEqual(toggleSelection(ids, 'a', true, ['a']), ['a'])
  assert.deepEqual(toggleSelection(ids, 'foreign', true, ['a']), ['a'])
  assert.deepEqual(toggleSelection(ids, 'a', false, ['a']), [])
  assert.deepEqual(selectedOnPage(['old', 'a', 'a'], ['a','b']), ['a'])
  assert.deepEqual(ids, ['a'])
})
test('loaded collection filters preserve actual records and tolerate empty values', () => {
  const rows = Object.freeze([{id:'1', name:'老挝啤酒', code:'SP01'}, {id:'2', name:'耳机', code:null}])
  assert.deepEqual(filterLoadedRows(rows, 'sp01', r => [r.name, r.code]), [rows[0]])
  assert.deepEqual(filterLoadedRows(rows, ' 啤酒 ', r => [r.name]), [rows[0]])
  assert.deepEqual(filterLoadedRows(rows, 'missing', r => [r.name]), [])
  assert.deepEqual(filterLoadedRows(rows, '', r => [r.name]), rows)
})
