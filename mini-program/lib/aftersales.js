const {presentBenefits}=require('./benefits')
const { money } = require('./catalog')
const { requestKey, dateLabel } = require('./orders')
const INTENT_KEY = 'mall.aftersaleIntent.v1'
const labels = { PENDING_REVIEW: '待审核', WAITING_RETURN: '待退货', WAITING_REFUND: '待退款',
  COMPLETED: '已完成', REJECTED: '已拒绝', WITHDRAWN: '已撤销' }
const kindLabels = { REFUND_ONLY: '仅退款', RETURN_REFUND: '退货退款' }
const pointsKindLabels = { REFUND_ONLY: '仅退积分', RETURN_REFUND: '退货退积分' }
function statusLabel(status, orderKind) { return orderKind === 'POINTS' && status === 'WAITING_REFUND' ? '待退积分' : labels[status] || '状态待核实' }
function optionLabel(option, fulfillmentKind, orderKind) {
  const kindCopy = orderKind === 'POINTS' ? pointsKindLabels : kindLabels
  return `${kindCopy[option.kind] || '售后'}${fulfillmentKind === 'SHIP' ? '' : option.redemptionScope === 'USED' ? ' · 已核销部分' : option.redemptionScope === 'UNUSED' ? ' · 未使用部分' : ''}`
}
function presentCase(row) {
  if (!row || typeof row.caseId !== 'string' || !Number.isSafeInteger(row.amountFen) || row.amountFen < 0) {
    throw new Error('售后内容不完整，请重新加载。')
  }
  const copies = { PENDING_REVIEW: '申请已提交，等待平台审核。当前占用申请数量与金额，退款尚未发生。',
    WAITING_RETURN: '审核已通过。请先联系客服确认退货地址，寄回后登记物流；平台验收后继续处理退款。',
    WAITING_REFUND: '审核已通过，等待平台核对退款。录入凭证或转账信息不代表资金已退回，最终状态以确认结果为准。',
    COMPLETED: '平台已确认退款完成。请核对实际资金到账，如有疑问请联系平台。',
    REJECTED: '申请未通过，原因见处理记录。申请占用已释放；可申请范围以服务端最新核对为准。',
    WITHDRAWN: '申请已撤销，数量与金额占用已释放。' }
  const goods = row.goodsRefundAmountFen ?? row.effectiveRefundAmountFen ?? row.amountFen
  const shipping = row.shippingRefundAmountFen ?? 0
  const total = row.totalRefundAmountFen ?? goods
  if (![goods, shipping, total].every(value => Number.isSafeInteger(value) && value >= 0) || total !== goods + shipping) throw new Error('退款金额不完整，请重新加载。')
  const isPoints = row.orderKind === 'POINTS'
  if (isPoints && ![row.requestedRefundPoints, row.effectiveRefundPoints, row.returnedPoints].every(v => Number.isSafeInteger(v) && v >= 0)) throw new Error('售后兑换积分不完整，请重新加载。')
  const zeroCash = total === 0
  const zeroCopies = { WAITING_REFUND: '本次没有现金退回。审核已通过，等待平台确认数量与权益处理结果。', COMPLETED: '本次没有现金退回，平台已确认售后数量与权益处理完成。' }
  const acceptance = row.returnAcceptance
  const shipment = row.returnShipment
  const pointsCopies = { PENDING_REVIEW: '申请已提交，等待平台审核。申请数量暂占，兑换积分尚未退回。', WAITING_RETURN: '审核已通过，请按平台要求寄回商品；验收后确认获准退回的兑换积分。', WAITING_REFUND: '审核已通过，等待平台确认数量与兑换积分返还。', COMPLETED: '平台已确认本笔售后完成。退回兑换积分请核对积分明细及对应有效期。', WITHDRAWN: '申请已撤销，数量与兑换积分申请占用已释放。' }
  return { ...row, isPoints, pointsAmountLabel: `${row.requestedRefundPoints} 积分`, effectivePointsLabel: row.status === 'PENDING_REVIEW' ? '待审核' : ['REJECTED', 'WITHDRAWN'].includes(row.status) ? '未获准' : `${row.effectiveRefundPoints} 积分`, returnedPointsLabel: `${row.returnedPoints} 积分`, orderBenefits:presentBenefits(row.orderBenefits), zeroCash, shippingAmountLabel: money(shipping), totalAmountLabel: money(total), goodsAmountLabel: money(goods), effectiveAmountLabel: money(Number.isSafeInteger(row.effectiveRefundAmountFen) ? row.effectiveRefundAmountFen : row.amountFen),
    effectiveRefundQuantity: Number.isSafeInteger(row.effectiveRefundQuantity) ? row.effectiveRefundQuantity : row.quantity,
    canSubmitReturnShipment: row.status === 'WAITING_RETURN' && row.canSubmitReturnShipment === true,
    returnShipment: shipment ? { ...shipment, submittedLabel: dateLabel(shipment.submittedAt) } : null,
    returnAcceptance: acceptance ? { ...acceptance,
      modeLabel: acceptance.mode === 'WAIVED_RETURN' ? '已批准免寄回' : '退货已验收',
      amountLabel: isPoints ? `${row.effectiveRefundPoints} 积分` : money(acceptance.refundAmountFen), maxAmountLabel: money(acceptance.maxRefundAmountFen),
      acceptedLabel: dateLabel(acceptance.acceptedAt),
      statusCopy: acceptance.refundQuantity === 0 ? '本次未批准退款，平台处理说明见上方；没有退款资金动作。' :
        row.status === 'COMPLETED' ? (zeroCash ? '本次没有现金退回，批准的数量与权益处理已完成。' : '平台已确认本次批准退款完成，请核对实际到账。') :
        `${acceptance.mode === 'WAIVED_RETURN' ? '本次无需寄回商品。' : '验收处理已记录。'} 退款尚未完成，最终结果以退款进度为准。`,
      partial: acceptance.refundQuantity < row.quantity || acceptance.refundAmountFen < row.amountFen } : null,
    amountLabel: isPoints ? `${row.requestedRefundPoints} 积分` : money(row.amountFen), statusLabel: statusLabel(row.status, row.orderKind),
    kindLabel: (isPoints ? pointsKindLabels : kindLabels)[row.kind] || '售后类型待核实',
    scopeLabel: row.fulfillmentKind === 'SHIP' ? '实物商品' : row.redemptionScope === 'USED' ? '已核销部分' : '未核销部分',
    refundLabel: ({ PREPARED: '待处理', PROCESSING: '处理中，待核对资金', UNKNOWN: '结果未知，平台核实中', FAILED: '处理失败，等待平台处理', SUCCEEDED: '已确认完成' })[row.refundStatus] || '',
    canWithdraw: row.status === 'PENDING_REVIEW' && row.canWithdraw === true,
    createdLabel: dateLabel(row.createdAt), updatedLabel: dateLabel(row.updatedAt),
    statusCopy: (isPoints && pointsCopies[row.status]) || (zeroCash && zeroCopies[row.status]) || copies[row.status] || '处理状态暂不可确认，请刷新或联系平台。',
    events: (Array.isArray(row.events) ? row.events : []).map((event, index) => ({ ...event,
      key: `${index}-${event.occurredAt}`, statusLabel: statusLabel(event.status, row.orderKind), timeLabel: dateLabel(event.occurredAt) })) }
}
// Local retry ownership is only a privacy guard; the server independently verifies member ownership.
function fingerprint(token) {
  let hash = 2166136261
  for (let index = 0; index < String(token || '').length; index += 1) hash = Math.imul(hash ^ String(token).charCodeAt(index), 16777619)
  return `${String(token || '').length}-${hash >>> 0}`
}
function loadIntent(orderId) {
  const token = wx.getStorageSync('mall.memberToken')
  const value = wx.getStorageSync(`${INTENT_KEY}.${orderId}`)
  if (!value || value.orderId !== orderId) return null
  if (!token || value.owner !== fingerprint(token)) { clearIntent(orderId); return null }
  const body = value.body
  if (typeof value.key !== 'string' || !/^[a-f0-9-]{36}$/.test(value.key) || !body ||
      typeof body.lineId !== 'string' || !Number.isInteger(body.quantity) || body.quantity < 1 ||
      !Object.keys(kindLabels).includes(body.kind) || !['UNUSED', 'USED'].includes(body.redemptionScope) ||
      typeof body.reason !== 'string' || body.reason.length < 5 || body.reason.length > 500) { clearIntent(orderId); return null }
  return { key: value.key, body: { ...body } }
}
function saveIntent(orderId, body) {
  const value = { orderId, owner: fingerprint(wx.getStorageSync('mall.memberToken')), key: requestKey(), body: { ...body } }
  wx.setStorageSync(`${INTENT_KEY}.${orderId}`, value)
  return { key: value.key, body: { ...value.body } }
}
function clearIntent(orderId, key) {
  const storageKey = `${INTENT_KEY}.${orderId}`
  const value = wx.getStorageSync(storageKey)
  if (!key || (value && value.key === key)) wx.removeStorageSync(storageKey)
}
module.exports = { statusLabel, optionLabel, presentCase, loadIntent, saveIntent, clearIntent }
