import test from 'node:test'
import assert from 'node:assert/strict'
import { reviewError, transferError, canConfirmTransfer, refundOutcome, caseStatus } from '../src/views/aftersales/form.mjs'

test('reviews require an explicit decision and an audit reason', () => {
  assert.equal(reviewError(true, '核对未履约数量后同意'), '')
  assert.match(reviewError(null, '核对后同意'), /选择/)
  assert.match(reviewError(false, '不行'), /5/)
})
test('actual transfer evidence must equal the approved amount and be independently checked', () => {
  const input = { externalRefundNo: 'BANK-REFUND-1', amount: '10.01', refundedAt: '2026-09-26T12:00', refundMethod: 'BANK_TRANSFER', proofReference: '合成凭证 R-1', note: '', verified: true }
  assert.equal(transferError(input, 1001), '')
  assert.match(transferError({ ...input, amount: '10.02' }, 1001), /一致/)
  assert.match(transferError({ ...input, amount: '1e1' }, 1001), /金额/)
  assert.match(transferError({ ...input, verified: false }, 1001), /核对/)
  assert.match(transferError({ ...input, refundedAt: 'invalid' }, 1001), /时间/)
  assert.match(transferError({ ...input, externalRefundNo: 'BANK\u0000' }, 1001), /流水/)
  assert.match(transferError({ ...input, proofReference: '' }, 1001), /凭证/)
  assert.match(transferError({ ...input, proofReference: 'https://example.test/bank' }, 1001), /链接/)
})
test('a preparer cannot confirm their own transfer even with permission', () => {
  const row = { preparedById: 'staff-a', outcome: 'DRAFT', canConfirm: true }
  assert.equal(canConfirmTransfer(row, 'staff-a', ['refund.offline.confirm']), false)
  assert.equal(canConfirmTransfer(row, 'staff-b', ['refund.offline.confirm']), true)
  assert.equal(canConfirmTransfer(row, 'staff-b', []), false)
  assert.equal(canConfirmTransfer({ ...row, outcome: 'SUCCEEDED' }, 'staff-b', ['refund.offline.confirm']), false)
  assert.equal(canConfirmTransfer({ ...row, canConfirm: false }, 'staff-b', ['refund.offline.confirm']), false)
})
test('unknown or failed confirmation keeps explicit recovery copy', () => {
  assert.match(refundOutcome('DRAFT'), /待复核/)
  assert.match(refundOutcome('SETTLEMENT_FAILED'), /恢复/)
  assert.match(refundOutcome('ANOMALY'), /异常/)
  assert.equal(refundOutcome('SUCCEEDED'), '已确认退款')
  assert.equal(caseStatus('WAITING_REFUND'), '待退款')
  assert.equal(caseStatus('WAITING_RETURN'), '待退货处理')
})
