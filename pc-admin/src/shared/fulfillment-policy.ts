export type RedeemScope = 'PLATFORM' | 'STORE' | 'BOTH'
export interface RedeemStore { storeId: string; name: string }
export interface RedemptionPolicy { redeemScope?: RedeemScope; redeemStoreIds?: string[]; redeemStores?: RedeemStore[] }
export function redemptionScopeLabel(policy: RedemptionPolicy) {
  const label = policy.redeemScope ? { PLATFORM: '仅平台', STORE: '指定前置仓', BOTH: '平台＋指定前置仓' }[policy.redeemScope] : '核销范围待核查'
  return `${label}${policy.redeemScope !== 'PLATFORM' && policy.redeemStores?.length ? `：${policy.redeemStores.map(store => store.name).join('、')}` : ''}`
}
export function fulfillmentOwnerLabel(order: {fulfillmentOwner?: string; fulfillmentStoreName?: string | null; storeName?: string; deliveryMode?: string}) {
  if (order.fulfillmentOwner === 'REDEEM' || order.deliveryMode === 'REDEEM') return '按订单项核销范围'
  if (order.fulfillmentOwner === 'STORE' || ['DELIVERY','PICKUP'].includes(order.deliveryMode || '')) return `${order.fulfillmentStoreName || order.storeName || '订单指定前置仓'}中心`
  return '平台后台'
}
