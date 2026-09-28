import test from 'node:test'
import assert from 'node:assert/strict'
import { canRecover, eventLabel, reasonLabel, statusLabel, taskQuery } from '../src/views/notifications/task-view.mjs'

test('task labels distinguish unknown delivery from synthetic processing', () => {
  assert.equal(statusLabel('UNKNOWN'), '待核验')
  assert.equal(statusLabel('SIMULATED'), '仅模拟完成')
  assert.equal(eventLabel('ORDER_SHIPPED'), '订单首次发货')
  assert.equal(reasonLabel('LEASE_EXPIRED'), '发送租约过期，结果待核验')
})

test('only explicitly recoverable reservations with a dedicated permission show recovery', () => {
  const task = { status: 'RESERVED', recoverable: true, attemptCount: 0 }
  assert.equal(canRecover(task, true), true)
  assert.equal(canRecover(task, false), false)
  assert.equal(canRecover({ ...task, status: 'UNKNOWN' }, true), false)
  assert.equal(canRecover({ ...task, recoverable: false }, true), false)
  assert.equal(canRecover({ ...task, attemptCount: 1 }, true), true)
})

test('task query keeps bounded filters and opaque cursor', () => {
  assert.equal(taskQuery({ eventType: 'ORDER_PAID', status: 'FAILED', createdFrom: '2026-09-01', createdTo: '2026-09-28' }, 'opaque+cursor'),
    '?eventType=ORDER_PAID&status=FAILED&createdFrom=2026-09-01&createdTo=2026-09-28&limit=20&cursor=opaque%2Bcursor')
  assert.equal(taskQuery({ eventType: '', status: '', createdFrom: '', createdTo: '' }, ''), '?limit=20')
})
