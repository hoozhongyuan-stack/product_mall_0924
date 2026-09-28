const {presentBenefits}=require('./benefits')
const { money } = require('./catalog')
const PENDING_KEY = 'mall.pendingOrder.v1'

// A random request identifier is for retry deduplication, never authentication.
function requestKey() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16)
    return (char === 'x' ? value : (value & 3) | 8).toString(16)
  })
}
function dateLabel(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间暂不可用'
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
function statusLabel(order) {
  if (order.status === 'PAID') return ({ WAITING_SHIPMENT: '待发货', WAITING_REDEMPTION: '待核销',
    IN_PROGRESS: '履约中', COMPLETED: '已完成', AFTER_SALE: '售后处理中', REFUNDED: '已退款' })[order.fulfillmentStatus] || (order.orderKind === 'POINTS' ? '已兑换' : '已付款')
  if (order.status === 'CLOSED') return '已关闭'
  if (order.status === 'PENDING_PAYMENT') return order.paymentReviewStatus === 'PENDING_REVIEW' ? '待付款 · 待核实' : '待付款'
  return '状态待核实'
}
function fulfillmentCopy(order) {
  return ({ WAITING_SHIPMENT: '收款已确认，实物商品等待发货。',
    WAITING_REDEMPTION: '收款已确认，出示订单项的核销凭证后可到店使用。',
    IN_PROGRESS: '订单项分别履约；实物查看运单，核销服务查看剩余次数。',
    COMPLETED: order.afterSaleSummary && order.afterSaleSummary.refundedQuantity > 0 ?
      '部分商品已退款，其余订单项已完成履约。' : '全部订单项已完成履约。',
    AFTER_SALE: '订单有售后正在处理，受影响的商品履约以服务端更新为准。',
    REFUNDED: '订单退款已确认，请以退款凭证和实际资金流水核对到账。' })[order.fulfillmentStatus] || '收款已确认，后续履约信息以订单更新为准。'
}
const lineStatusLabels = {
  SHIP: { AFTER_SALE: '售后处理中', REFUNDED: '已退款', WAITING_SHIPMENT: '待发货', IN_TRANSIT: '运输中', COMPLETED: '已收货' },
  REDEEM: { AFTER_SALE: '售后处理中', REFUNDED: '已退款', WAITING_PAYMENT: '待确认收款', WAITING_VALIDITY: '有效期待核实',
    WAITING_REDEMPTION: '待核销', PARTIAL: '部分核销', COMPLETED: '核销完成', EXPIRED: '已过有效期' },
}
function presentLine(item, order) {
  const paid = order.status === 'PAID'
  const kind = item.fulfillmentKind
  const progress = item.fulfillment && item.fulfillment.kind === kind ? item.fulfillment : null
  const redemption = paid && kind === 'REDEEM' && progress ? {
    voucherCode: typeof progress.voucherCode === 'string' ? progress.voucherCode : '',
    voucherQrAvailable: typeof progress.voucherQrDataUrl === 'string' &&
      /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(progress.voucherQrDataUrl),
    validUntilLabel: typeof progress.validUntil === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(progress.validUntil) ?
      progress.validUntil : '有效期待核实',
    redeemedQuantity: progress.redeemedQuantity,
    voidedQuantity: progress.voidedQuantity,
    remainingQuantity: progress.remainingQuantity,
    heldQuantity: Number.isSafeInteger(progress.heldQuantity) ? progress.heldQuantity : 0,
    availableQuantity: Number.isSafeInteger(progress.availableQuantity) ? progress.availableQuantity : progress.remainingQuantity,
    events: (Array.isArray(progress.events) ? progress.events : []).map((event) => ({ ...event,
      actionLabel: event.kind === 'REVERSE' ? '撤销核销' : event.reversedAt ? '已撤销的核销' : '核销',
      redeemedAtLabel: dateLabel(event.redeemedAt), reversedAtLabel: dateLabel(event.reversedAt) })),
  } : null
  const safeFulfillment = item.fulfillment && typeof item.fulfillment === 'object' ?
    { ...item.fulfillment, voucherQrDataUrl: undefined } : item.fulfillment
  return { ...item, fulfillment: safeFulfillment, price: order.orderKind === 'POINTS' ? `${item.pointsUnitPrice} 积分` : money(item.unitPriceFen), amount: order.orderKind === 'POINTS' ? `${item.pointsTotal} 积分` : money(item.payableFen), redemption,
    specLabel: Array.isArray(item.specs) && item.specs.length ? item.specs.map((spec) => `${spec.name}：${spec.value}`).join(' · ') : '默认规格',
    fulfillmentLabel: kind === 'SHIP' ? '快递发货' : '到店核销',
    fulfillmentStatus: order.status === 'CLOSED' ? '已关闭' : !paid ? '收款确认后可履约' :
      (progress && lineStatusLabels[kind] && lineStatusLabels[kind][progress.status]) || '进度待核实',
  }
}
function orderProgress(order) {
  const paid = order.status === 'PAID'
  const fulfillment = order.fulfillmentStatus
  if (order.status !== 'PENDING_PAYMENT' && (!paid ||
      !['WAITING_SHIPMENT', 'WAITING_REDEMPTION', 'IN_PROGRESS', 'COMPLETED'].includes(fulfillment))) return []
  const completed = fulfillment === 'COMPLETED'
  return [
    { label: '已下单', reached: true },
    { label: order.orderKind === 'POINTS' ? '已兑换' : '已付款', reached: paid },
    { label: completed ? '已履约' : fulfillment === 'IN_PROGRESS' ? '履约中' : '待履约', reached: paid },
    { label: '完成', reached: completed },
  ]
}
function present(order) {
  if (!order || typeof order.orderId !== 'string' || !Array.isArray(order.items)) throw new Error('订单内容不完整，请重新加载。')
  const isPaid = order.status === 'PAID'
  const closed = order.status === 'CLOSED'
  const pending = order.status === 'PENDING_PAYMENT'
  const offline = order.paymentMethod === 'OFFLINE'
  const wechat = order.paymentMethod === 'WECHAT'
  const deadline = new Date(order.expiresAt).getTime()
  const isExpired = pending && Number.isFinite(deadline) && deadline <= Date.now()
  const shipment = isPaid && order.shipment && typeof order.shipment === 'object' ? order.shipment : null
  const canConfirmReceipt = !!shipment && !!shipment.shippedAt && !shipment.confirmedAt &&
    order.items.some((item) => item.fulfillmentKind === 'SHIP' && item.fulfillment && item.fulfillment.status === 'IN_TRANSIT')
  const safeOrder = { ...order, items: order.items.map((item) => item.fulfillment && item.fulfillment.voucherQrDataUrl ?
    { ...item, fulfillment: { ...item.fulfillment, voucherQrDataUrl: undefined } } : item) }
  const isPoints = order.orderKind === 'POINTS'
  if (isPoints && (!Number.isSafeInteger(order.exchangePoints) || order.exchangePoints < 1 || order.paymentMethod !== 'POINTS' || order.items.some(item => !Number.isSafeInteger(item.pointsUnitPrice) || item.pointsUnitPrice < 1 || item.pointsTotal !== item.pointsUnitPrice * item.quantity))) throw new Error('积分兑换订单资料不完整，请重新加载。')
  return { isPoints, progressSteps: orderProgress(order), canRequestExchangeCancel: isPoints && isPaid && ['WAITING_SHIPMENT', 'WAITING_REDEMPTION'].includes(order.fulfillmentStatus), order: safeOrder, orderBenefits:presentBenefits(order.orderBenefits), statusLabel: statusLabel(order), isPaid, isClosed: closed, isExpired,
    shipment: shipment && { ...shipment, shippedAtLabel: dateLabel(shipment.shippedAt),
      autoConfirmAtLabel: dateLabel(shipment.autoConfirmAt), confirmedAtLabel: dateLabel(shipment.confirmedAt) },
    canConfirmReceipt,
    trackingMessage: shipment && shipment.trackingStatus === 'UNAVAILABLE' ?
      '物流轨迹暂不可用，发货状态不受影响。请保存快递公司和运单号，稍后重试。' : '',
    showPaymentInstructions: pending && offline && !isExpired && !!order.paymentInstructions,
    closeReasonLabel: order.closeReason === 'TIMEOUT' ? '超时关闭' : order.closeReason === 'CUSTOMER' ? '用户取消' : '订单关闭',
    total: isPoints ? `${order.exchangePoints} 积分` : money(order.payableFen), goodsTotal: money(order.goodsTotalFen), shippingFee: money(order.shippingFeeFen),
    couponDiscount: money(order.couponDiscountFen), pointsDiscount: money(order.pointsDiscountFen),
    expiresLabel: dateLabel(order.expiresAt), createdLabel: dateLabel(order.createdAt), paidLabel: dateLabel(order.paidAt),
    paymentLabel: isPoints ? '纯积分兑换' : order.payableFen === 0 ? '零现金结算' : offline ? '线下支付' : order.paymentMethod === 'WECHAT' ? '微信支付' : '支付方式待核实',
    canReport: !isPoints && pending && offline && !isExpired, canCancel: !isPoints && pending,
    canPayWechat: pending && wechat && !isExpired && order.payableFen > 0 && order.wechatPaymentAvailable === true &&
      !['SUCCESS', 'CLOSED', 'USERPAYING'].includes(order.wechatPaymentState),
    showWechatPayment: !isPaid && wechat && order.payableFen > 0,
    resultTitle: isPoints && isPaid ? '积分兑换成功' : isPaid && order.payableFen === 0 ? '零现金订单已结算' : isPaid && order.fulfillmentStatus === 'REFUNDED' ? '订单已退款' : isPaid ? '服务端已确认收款' : closed ? '订单已关闭' : offline ? '订单已提交，等待核实收款' : '订单已提交，等待支付',
    statusCopy: isPoints ? '已按成交快照扣除兑换积分，商品按订单进度履约。未发货或未核销前可申请取消兑换，审核通过后退回积分；履约后沿商品售后规则处理。' : isPaid ? fulfillmentCopy(order) : closed ? '请勿继续向此订单付款。关闭后到账需由平台处理，不会恢复原订单。' :
      isExpired ? '付款时限已到，请勿继续付款。请刷新核对服务端关单状态；若已经付款，请联系平台核实异常到账。' :
      offline ? '用户报告付款仍为待付款。平台核对实际到账并确认后，才会开始履约。' :
        order.wechatPaymentState === 'SUCCESS' ? '微信已返回到账，平台正在核实结算。请勿重复支付；订单状态以服务端确认为准。' :
        order.wechatPaymentAvailable === true ? '支付结果由服务端核实。微信页面提示成功后，仍需刷新确认订单状态。' : '微信收款入口暂不可用。已发起支付可查询结果，请勿自行转账。',
    items: order.items.map((item) => presentLine(item, order)),
    reports: (Array.isArray(order.paymentReports) ? order.paymentReports : []).map((report) => ({ ...report, timeLabel: dateLabel(report.reportedAt) })),
  }
}
function pendingOrder() {
  const value = wx.getStorageSync(PENDING_KEY)
  if (!value || !value.body || typeof value.body.quoteId !== 'string' || !['OFFLINE', 'WECHAT'].includes(value.body.paymentMethod) ||
      typeof value.key !== 'string' || !/^[a-f0-9-]{36}$/.test(value.key)) return null
  return value
}
function savePendingOrder(value) { wx.setStorageSync(PENDING_KEY, value) }
function clearPendingOrder() { wx.removeStorageSync(PENDING_KEY) }
module.exports = { requestKey, dateLabel, statusLabel, present, pendingOrder, savePendingOrder, clearPendingOrder }
