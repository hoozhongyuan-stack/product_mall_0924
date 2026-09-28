export function shipmentError(carrierCode, trackingNo, carriers) {
  if (!Array.isArray(carriers) || !carriers.length) return '暂无可用快递公司，暂不能发货。'
  if (!carriers.some(carrier => carrier.code === carrierCode)) return '请选择可用快递公司。'
  if (!trackingNo.trim()) return '请输入运单号。'
  if (trackingNo.trim().length > 80) return '运单号不能超过 80 字。'
  return ''
}

export function redemptionError(quantity, available) {
  if (available < 1) return '当前凭证没有可核销数量。'
  if (!/^[1-9]\d*$/.test(String(quantity))) return '本次核销数量必须是正整数。'
  if (!Number.isSafeInteger(Number(quantity)) || Number(quantity) > available) return '本次核销数量超过可核销数量。'
  return ''
}

export function remaining(line) {
  if (typeof line.fulfillment?.remainingQuantity === 'number') return line.fulfillment.remainingQuantity
  return Math.max(0, line.quantity - (line.fulfillment?.redeemedQuantity || 0) - (line.fulfillment?.voidedQuantity || 0))
}

export function fulfillmentLabel(status) {
  return ({ AFTER_SALE: '售后处理中', REFUNDED: '已退款', PENDING: '待履约', PARTIAL: '部分核销', IN_PROGRESS: '履约中', COMPLETED: '已完成', WAITING_PAYMENT: '待付款', CLOSED: '已关闭', WAITING_SHIPMENT: '待发货', IN_TRANSIT: '运输中', WAITING_VALIDITY: '待确认有效期', WAITING_REDEMPTION: '待核销', EXPIRED: '已过期', READY: '可核销', UNPAID: '未付款', VALIDITY_MISSING: '有效期未确认', FULLY_REDEEMED: '已全部核销' })[status] || '待履约'
}

export function shipmentEligible(order) {
  if (['AFTER_SALE', 'REFUNDED'].includes(order.fulfillmentStatus)) return false
  if (typeof order.shipEligible === 'boolean') return order.shipEligible
  if (Array.isArray(order.items)) return order.status === 'PAID' && order.items.some(line => line.fulfillmentKind === 'SHIP' && line.fulfillment?.status === 'WAITING_SHIPMENT')
  return order.status === 'PAID' && ['PENDING', 'WAITING_SHIPMENT'].includes(order.fulfillment?.shipStatus)
}

export function shippingQuantity(line) {
  const purchased=line?.quantity,refunded=line?.fulfillment?.refundedQuantity
  if(!Number.isSafeInteger(purchased)||purchased<1||!Number.isSafeInteger(refunded)||refunded<0||refunded>purchased)return null
  return {purchased,refunded,remaining:purchased-refunded}
}
export function shipmentQuantityError(lines) {
  if(!Array.isArray(lines))return '发货数量待核查，请重新读取订单。'
  const rows=lines.filter(line=>line.fulfillmentKind==='SHIP').map(shippingQuantity)
  if(rows.some(row=>row===null))return '发货数量待核查，请重新读取订单；数量确认前不能发货。'
  if(!rows.some(row=>row.remaining>0))return '本单没有待发实物数量，不能登记发货。'
  return ''
}
