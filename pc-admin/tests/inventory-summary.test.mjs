import { test } from 'node:test'
import assert from 'node:assert/strict'
import { summarizeBalances } from '../src/views/inventory/summary.mjs'
const row = (skuId, baseUnit, onHandBaseUnits, reservedBaseUnits = 0, warehouseId = 'w1') => ({ skuId, warehouseId, baseUnit, onHandBaseUnits, reservedBaseUnits, availableBaseUnits: onHandBaseUnits - reservedBaseUnits })
test('groups physical balances by base unit instead of adding bottles to items', () => {
  assert.deepEqual(summarizeBalances([row('a', '件', 480), row('b', '瓶', 419, 5)]), [
    { baseUnit: '件', onHand: 480, reserved: 0, available: 480 },
    { baseUnit: '瓶', onHand: 419, reserved: 5, available: 414 },
  ])
})
test('counts each warehouse anchor once while retaining balances in other warehouses', () => {
  const original = [row('pool', '瓶', 12), row('pool', '瓶', 12), row('other', '瓶', 8, 2), row('pool', '瓶', 6, 0, 'w2')]
  const snapshot = structuredClone(original)
  assert.deepEqual(summarizeBalances(original), [{ baseUnit: '瓶', onHand: 26, reserved: 2, available: 24 }])
  assert.deepEqual(original, snapshot)
  assert.deepEqual(summarizeBalances([]), [])
})
