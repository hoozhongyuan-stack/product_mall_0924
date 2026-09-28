import assert from 'node:assert/strict'
import test from 'node:test'
import { buildStocktakeSubmission, parseCount } from '../src/views/inventory/stocktake.mjs'

test('实盘数量允许零和安全整数，拒绝小数与负数', () => {
  assert.equal(parseCount('0'), 0)
  assert.equal(parseCount(' 12 '), 12)
  for (const value of ['', '-1', '1.5', '1e2', '9007199254740992']) assert.equal(parseCount(value), null)
})

test('提交盘点要求全部 SKU 和差异原因', () => {
  const items = [
    { skuId: 'a', bookAtStartBaseUnits: 5 },
    { skuId: 'b', bookAtStartBaseUnits: 3 },
  ]
  const result = buildStocktakeSubmission(items, { a: { count: '5', reason: '' }, b: { count: '0', reason: '发现破损' } })
  assert.deepEqual(result, [
    { skuId: 'a', countedBaseUnits: 5, reason: '' },
    { skuId: 'b', countedBaseUnits: 0, reason: '发现破损' },
  ])
  assert.throws(() => buildStocktakeSubmission(items, { a: { count: '5', reason: '' } }), /填写全部/)
  assert.throws(() => buildStocktakeSubmission(items, { a: { count: '5', reason: '' }, b: { count: '2', reason: '' } }), /原因/)
})
