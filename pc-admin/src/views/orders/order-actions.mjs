import { shipmentEligible, remaining } from './fulfillment.mjs'
import { isPointsOrder } from '../../shared/order-settlement.mjs'
export function orderActions(order, permissions) {
  if (!order) return []
  const has = code => permissions.includes(code)
  const actions = []
  if (!isPointsOrder(order) && order.payableFen > 0 && has('payment.offline.confirm')) {
    if (order.paymentMethod === 'OFFLINE' && order.status === 'PENDING_PAYMENT') actions.push('receipt')
    if (order.paymentMethod === 'OFFLINE' && order.status === 'CLOSED') actions.push('anomaly')
    if (order.paymentMethod === 'WECHAT' && order.status === 'PENDING_PAYMENT') actions.push('query')
  }
  if (order.status !== 'PAID') return actions
  if (has('fulfillment.ship') && !['AFTER_SALE', 'REFUNDED'].includes(order.fulfillmentStatus)) {
    const local = ({ WAITING_PREPARATION: 'prepare', WAITING_DELIVERY: 'deliver', DELIVERING: 'delivered', WAITING_PICKUP: 'pickup' })[order.fulfillmentStatus]
    if (local && ['PICKUP', 'DELIVERY'].includes(order.deliveryMode)) actions.push(local)
    if (shipmentEligible(order)) actions.push('ship')
  }
  if (order.shipment || order.hasShipment) {
    if (has('order.read')) actions.push('tracking')
    if (has('fulfillment.ship')) actions.push('correct')
  }
  if (has('fulfillment.redeem') && (typeof order.redeemEligible === 'boolean' ? order.redeemEligible : order.items?.some(line => line.fulfillmentKind === 'REDEEM' && ['READY', 'PARTIAL', 'WAITING_REDEMPTION'].includes(line.fulfillment?.status) && remaining(line) > 0))) actions.push('redeem')
  if (has('aftersale.read') && order.fulfillmentStatus === 'AFTER_SALE') actions.push('aftersale')
  return actions
}
export function localActionBody(action, revision, form) {
  const code = ({ prepare: 'PREPARE', deliver: 'DISPATCH', delivered: 'COMPLETE', pickup: 'COMPLETE' })[action]
  if (!code) throw Error('不支持的履约操作。')
  const note = action === 'deliver' ? `配送人员：${form.person.trim()}；联系电话：${form.phone.trim()}${form.note.trim() ? '；' + form.note.trim() : ''}` : form.note.trim()
  return { action: code, expectedRevision: revision, note, ...(action === 'pickup' ? { pickupCode: form.pickupCode.trim() } : {}) }
}
export const actionLabels = { receipt: '确认收款', anomaly: '登记异常到账', query: '查询支付结果', prepare: '确认备货完成', deliver: '开始配送', delivered: '确认送达', pickup: '确认自提', ship: '发货', correct: '更正运单', tracking: '查看物流', redeem: '核销', aftersale: '查看售后' }
export function actionPermission(action) {
  if (['receipt','anomaly','query'].includes(action)) return 'payment.offline.confirm'
  if (action === 'redeem') return 'fulfillment.redeem'
  if (action === 'aftersale') return 'aftersale.read'
  if (action === 'tracking') return 'order.read'
  return 'fulfillment.ship'
}
export function validatePending(value, actor, orderId, action) {
  if (!value || value.actor !== actor || value.orderId !== orderId || value.action !== action || typeof value.key !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.key) || !value.body || typeof value.body !== 'object' || Array.isArray(value.body)) return false
  if (!pendingBodyValid(value.body, action, value.phase)) return false
  const root = `/orders/${orderId}`
  if (['receipt','anomaly'].includes(action)) return value.phase === 'prepare' ? value.path === `${root}/offline-reconciliations` : value.phase === 'confirm' && /^\/payments\/offline-reconciliations\/[a-zA-Z0-9-]+\/confirm$/.test(value.path)
  if (value.phase !== 'write') return false
  if (action === 'redeem') return new RegExp('^' + root.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '/redemptions/[a-zA-Z0-9-]+/use$').test(value.path)
  const suffix = ({ship:'/shipment',correct:'/shipment/correct',query:'/wechat/query',prepare:'/local-fulfillment',deliver:'/local-fulfillment',delivered:'/local-fulfillment',pickup:'/local-fulfillment'})[action]
  return !!suffix && value.path === root + suffix
}
export function pendingActions(actor, orderId, storage) {
  try { return Object.keys(actionLabels).filter(action => { const value = storage.getItem(`mall-order-action:${actor}:${orderId}:${action}`); return value && validatePending(JSON.parse(value), actor, orderId, action) }) } catch { return [] }
}

function pendingBodyValid(body, action, phase) {
  const text = key => typeof body[key] === 'string'
  const revision = Number.isSafeInteger(body.expectedRevision) && body.expectedRevision >= 0
  const fields = keys => Object.keys(body).every(key => keys.includes(key))
  if (['receipt','anomaly'].includes(action)) return fields(['expectedRevision','merchantAccountId','externalTradeNo','amountFen','paidAt','note','verified']) && (phase === 'confirm' || revision) && text('merchantAccountId') && text('externalTradeNo') && text('paidAt') && text('note') && Number.isSafeInteger(body.amountFen) && body.amountFen > 0 && (phase === 'confirm' || body.verified === true)
  if (action === 'query') return Object.keys(body).length === 0
  if (!revision) return false
  if (action === 'ship' || action === 'correct') return fields(['expectedRevision','carrierCode','trackingNo',...(action === 'correct'?['reason']:[])]) && text('carrierCode') && text('trackingNo') && (action !== 'correct' || text('reason'))
  if (action === 'redeem') return fields(['expectedRevision','quantity']) && Number.isSafeInteger(body.quantity) && body.quantity > 0
  const code = ({prepare:'PREPARE',deliver:'DISPATCH',delivered:'COMPLETE',pickup:'COMPLETE'})[action]
  return !!code && fields(['expectedRevision','action','note',...(action==='pickup'?['pickupCode']:[])]) && body.action === code && text('note') && (action !== 'pickup' || text('pickupCode'))
}

export function receiptRecoveryMatches(order, pending) {
  const id = /^\/payments\/offline-reconciliations\/([a-zA-Z0-9-]+)\/confirm$/.exec(pending.path)?.[1]
  const record = order.confirmations?.find(item => item.reconciliationId === id && item.actorId === pending.actor)
  if (!record) return false
  return ['merchantAccountId', 'externalTradeNo', 'amountFen', 'note'].every(key => record[key] === pending.body[key]) &&
    Number.isFinite(Date.parse(record.paidAt)) && Date.parse(record.paidAt) === Date.parse(pending.body.paidAt)
}
