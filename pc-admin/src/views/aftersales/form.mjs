const text = (value, maximum) => typeof value === 'string' && value.trim().length > 0 && value.length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)
export function reviewError(decision, reason) {
  if (typeof decision !== 'boolean') return '请选择同意或拒绝。'
  if (!text(reason, 500) || reason.trim().length < 5) return '审核依据须为 5 至 500 字。'
  return ''
}
export function transferError(value, amountFen) {
  if (!text(value.externalRefundNo, 128)) return '请填写有效的实际退款流水号（最多 128 字）。'
  if (typeof value.amount !== 'string' || !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(value.amount)) return '退款金额须为有效金额，最多两位小数。'
  const [yuan, cents = ''] = value.amount.split('.')
  if (Number(yuan) * 100 + Number(cents.padEnd(2, '0')) !== amountFen) return '实际退款金额须与本笔审核金额一致，请先核查差异。'
  if (!value.refundedAt || !Number.isFinite(new Date(value.refundedAt).getTime()) || new Date(value.refundedAt).getTime() > Date.now()) return '请填写不晚于当前时间的实际转账时间。'
  if (!['BANK_TRANSFER','WECHAT_TRANSFER','ALIPAY_TRANSFER','CASH','OTHER'].includes(value.refundMethod)) return '请选择实际退款方式。'
  if (!text(value.proofReference, 128) || /:\/\//.test(value.proofReference)) return '请填写可追溯的转账凭证档案号（最多 128 字），不要填写网络链接。'
  if (typeof value.note !== 'string' || value.note.length > 500 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value.note)) return '核对说明不能超过 500 字。'
  if (value.verified !== true) return '请核对真实退款流水、金额、账户和转账凭证。'
  return ''
}
export function canConfirmTransfer(row, accountId, codes) {
  return Boolean(row && row.canConfirm && row.preparedById !== accountId && codes.includes('refund.offline.confirm') && !['SUCCEEDED', 'ANOMALY'].includes(row.outcome))
}
export const caseStatus = value => ({ PENDING_REVIEW: '待审核', WAITING_REFUND: '待退款', WAITING_RETURN: '待退货处理', COMPLETED: '已完成', REJECTED: '已拒绝', WITHDRAWN: '已撤销' }[value] || value || '—')
export const refundOutcome = value => ({ DRAFT: '已登记 · 待复核', PENDING_CONFIRMATION: '已登记 · 待复核', AUTHORIZED: '已授权 · 待结算', SUCCEEDED: '已确认退款', SETTLEMENT_FAILED: '结算失败 · 可恢复同一凭证', FAILED: '处理失败 · 请核查同一凭证', UNKNOWN: '结果未知 · 请刷新核查', ANOMALY: '退款异常 · 待人工核查' }[value] || value || '待处理')
export const refundMethodLabel = value => ({ BANK_TRANSFER: '银行转账', WECHAT_TRANSFER: '微信转账', ALIPAY_TRANSFER: '支付宝转账', CASH: '现金', OTHER: '其他' }[value] || value)
