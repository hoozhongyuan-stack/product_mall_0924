function scopeLabel(progress) {
  const stores = (Array.isArray(progress.redeemStores) ? progress.redeemStores : []).map(store => store.name).filter(Boolean)
  const names = stores.length ? stores.join('、') : '指定前置仓'
  return ({ PLATFORM: '平台核销', STORE: names, BOTH: `平台及${names}` })[progress.redeemScope] || '核销范围待核实'
}
function ownerLabel(order) {
  return ({ PLATFORM: '平台', STORE: order.fulfillmentStoreName || order.storeName || '指定前置仓', REDEEM: '按各项授权范围核销' })[order.fulfillmentOwner] ||
    (['DELIVERY', 'PICKUP'].includes(order.deliveryMode) ? order.storeName || '指定前置仓' : '平台')
}
function actorLabel(event) {
  return event.actorKind === 'STORE' ? `${event.storeName || '前置仓'}核销` : event.actorKind === 'PLATFORM' ? '平台核销' : '核销方待核实'
}
function storeRedemptionItems(order, storeId) {
  if (order.status !== 'PAID') return []
  return (order.items || []).filter(item => {
    const progress = item.fulfillment || {}
    return item.fulfillmentKind === 'REDEEM' && ['STORE', 'BOTH'].includes(progress.redeemScope) &&
      Array.isArray(progress.redeemStoreIds) && progress.redeemStoreIds.includes(storeId)
  }).map(item => {
    const progress = item.fulfillment
    const canRedeem = progress.canRedeemStore === true && ['WAITING_REDEMPTION', 'PARTIAL'].includes(progress.status) &&
      Number.isSafeInteger(progress.availableQuantity) && progress.availableQuantity > 0 &&
      Number.isSafeInteger(progress.voucherRevision) && progress.voucherRevision > 0
    return { ...item, fulfillment: { ...progress, voucherCode: undefined, voucherQrDataUrl: undefined }, canRedeem,
      scopeLabel: scopeLabel(progress), validUntilLabel: progress.validUntil || '有效期待核实' }
  })
}
module.exports = { scopeLabel, ownerLabel, actorLabel, storeRedemptionItems }
