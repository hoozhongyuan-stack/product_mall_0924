export const isPointsOrder=order=>order?.orderKind==='POINTS'||order?.paymentMethod==='POINTS'
export function orderSettlementValue(order){return isPointsOrder(order)?`${Number.isSafeInteger(order.exchangePoints)?order.exchangePoints:'待核查'} 积分`:`¥${(order.payableFen/100).toFixed(2)}`}
export function paymentMethodLabel(order){return isPointsOrder(order)?'纯积分兑换':order.paymentMethod==='OFFLINE'?'线下支付':order.paymentMethod==='WECHAT'?'微信支付':'待核查'}
