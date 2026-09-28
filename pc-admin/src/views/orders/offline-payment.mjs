import {isPointsOrder} from '../../shared/order-settlement.mjs'
export function amountFen(value) {
  if (typeof value !== 'string' || !/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(value)) return null
  const [yuan, cents = ''] = value.split('.')
  const amount = Number(yuan) * 100 + Number(cents.padEnd(2, '0'))
  return amount > 0 ? amount : null
}
export function reconciliationError(value, payableFen) {
  if (!value.merchantAccountId.trim() || value.merchantAccountId.length > 80) return '请输入有效的收款账户标识（最多 80 字）。'
  if (!value.externalTradeNo.trim() || value.externalTradeNo.length > 128) return '请输入实际到账流水号（最多 128 字）。'
  if (amountFen(value.amount) === null) return '请输入大于 0 的实际到账金额，最多两位小数。'
  if (!value.paidAt || !Number.isFinite(new Date(value.paidAt).getTime())) return '请输入实际到账时间。'
  if (new Date(value.paidAt).getTime() > Date.now()) return '到账时间不能晚于当前时间。'
  if (value.note.length > 500) return '核对备注不能超过 500 字。'
  if (amountFen(value.amount) !== payableFen && !value.note.trim()) return '实际金额与应付不符，请填写差异原因。'
  if (!value.verified) return '请先核对实际到账记录、账户、流水号和金额。'
  return ''
}
export function paymentStatus(order) {
  if(isPointsOrder(order))return ({PAID:'已扣积分',CLOSED:'已关闭',PENDING_PAYMENT:'兑换待处理'})[order.status]||order.status
  const status = { PENDING_PAYMENT: '待付款', PAID: '已付款', CLOSED: '已关闭' }[order.status] || order.status
  if (order.paymentReviewStatus === 'ANOMALY') return `${status} · 到账异常`
  if (order.status === 'PENDING_PAYMENT' && order.paymentReviewStatus === 'PENDING_REVIEW') return `${status} · 待核实`
  return status
}
export function sameEvidence(left, right) {
  const sort = value => JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))))
  return sort(left) === sort(right)
}
