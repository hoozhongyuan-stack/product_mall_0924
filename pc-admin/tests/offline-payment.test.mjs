import test from 'node:test'
import assert from 'node:assert/strict'
import { amountFen, reconciliationError, paymentStatus, sameEvidence } from '../src/views/orders/offline-payment.mjs'

test('actual money is parsed exactly to cents and invalid amounts are refused', () => {
  assert.equal(amountFen('1000.01'), 100001)
  for (const value of ['0', '-1', '1.001', '1e3', 'Infinity', ' 1', '9999999999999']) assert.equal(amountFen(value), null)
})
test('reconciliation requires independently verified bank details and a reason for mismatch', () => {
  const value = { merchantAccountId: 'bank-1', externalTradeNo: 'TX-1', amount: '10.00', paidAt: '2026-09-26T10:00', note: '', verified: true }
  assert.equal(reconciliationError(value, 1000), '')
  assert.match(reconciliationError({ ...value, verified: false }, 1000), /核对/)
  assert.match(reconciliationError({ ...value, amount: '9.99' }, 1000), /原因/)
  assert.equal(reconciliationError({ ...value, amount: '9.99', note: '实际到账少一分' }, 1000), '')
  assert.match(reconciliationError({ ...value, paidAt: 'bad' }, 1000), /时间/)
  assert.match(reconciliationError({ ...value, externalTradeNo: '' }, 1000), /流水/)
})
test('reported payment and anomalies never display as paid', () => {
  assert.equal(paymentStatus({ status: 'PENDING_PAYMENT', paymentReviewStatus: 'PENDING_REVIEW' }), '待付款 · 待核实')
  assert.equal(paymentStatus({ status: 'PENDING_PAYMENT', paymentReviewStatus: 'ANOMALY' }), '待付款 · 到账异常')
  assert.equal(paymentStatus({ status: 'CLOSED', paymentReviewStatus: 'ANOMALY' }), '已关闭 · 到账异常')
  assert.equal(paymentStatus({ status: 'PAID' }), '已付款')
})
test('immutable confirmation must be regenerated after changing evidence', () => {
  assert.equal(sameEvidence({ amountFen: 1000, note: 'a' }, { note: 'a', amountFen: 1000 }), true)
  assert.equal(sameEvidence({ amountFen: 1000 }, { amountFen: 1001 }), false)
})
