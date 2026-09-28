const { money } = require('./catalog')
const { dateLabel } = require('./orders')
const CLAIMS_KEY = 'mall.couponClaims.v1'
const STATUSES = { AVAILABLE: '可使用', RESERVED: '订单暂占', USED: '已使用', EXPIRED: '已过期' }
function integer(v) { return Number.isSafeInteger(v) && v >= 0 }
function base(row) {
  if (!row || typeof row.id !== 'string' || typeof row.title !== 'string' || !['FULL_REDUCTION','CASH'].includes(row.kind) ||
      !integer(row.discountFen) || row.discountFen === 0 || !integer(row.minGoodsFen) || !Array.isArray(row.productIds) ||
      !Array.isArray(row.productNames) || !row.productNames.every(p => p && typeof p.id === 'string' && row.productIds.includes(p.id) && typeof p.name === 'string') ||
      !row.productIds.every(id => typeof id === 'string') || typeof row.redeemEligible !== 'boolean' ||
      !Number.isFinite(Date.parse(row.validFrom)) || !Number.isFinite(Date.parse(row.validUntil))) throw new Error('优惠券资料不完整，请重新加载。')
  return { ...row, amountLabel: money(row.discountFen), kindLabel: row.kind === 'CASH' ? '现金券' : '满减券',
    thresholdLabel: row.minGoodsFen ? `适用商品金额满 ${money(row.minGoodsFen)} 可用` : '无门槛现金券',
    scopeLabel: row.productIds.length ? '指定商品适用' : '全场商品适用',
    redemptionLabel: row.redeemEligible ? '可用于快递商品与核销类商品' : '不适用于核销类商品',
    validLabel: `${dateLabel(row.validFrom)} 至 ${dateLabel(row.validUntil)}` }
}
function presentCampaign(row) {
  if (!integer(row.remainingQuantity) || !integer(row.selfClaimLimit) || !integer(row.selfClaimedCount) || typeof row.canClaim !== 'boolean') throw new Error('优惠券活动资料不完整，请重新加载。')
  return { ...base(row), claimLabel: row.canClaim ? '领取优惠券' : '已达领取上限' }
}
function presentCoupon(row) {
  if (!STATUSES[row.status] || typeof row.campaignId !== 'string') throw new Error('优惠券状态不完整，请重新加载。')
  return { ...base(row), statusLabel: STATUSES[row.status], statusCopy: row.status === 'RESERVED' ? '待付款订单正在暂占此券；取消或关闭订单后按有效期释放。' : row.status === 'AVAILABLE' ? '结算时由服务端核对商品、有效期与门槛；尚未到开始时间的券暂不可用。' : row.status === 'USED' ? '付款时已核销。全额退款后是否恢复以有效期及订单结算结果为准。' : '已超过有效截止时间，不能用于新订单。' }
}
function readPending(memberId) {
  const saved = wx.getStorageSync(CLAIMS_KEY), p = saved && saved[memberId]
  return p && p.memberId === memberId && typeof p.campaignId === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(p.key) ? p : null
}
function savePending(p) {
  try {
    const existing = wx.getStorageSync(CLAIMS_KEY)
    wx.setStorageSync(CLAIMS_KEY, { ...(existing && typeof existing === 'object' ? existing : {}), [p.memberId]: { ...p } })
    if (!readPending(p.memberId) || readPending(p.memberId).key !== p.key) throw new Error('save')
  } catch (_) { throw new Error('无法保存领取凭证，未发送领取请求。请检查设备存储后重试。') }
}
function clearPending(p) {
  const existing = wx.getStorageSync(CLAIMS_KEY)
  if (!existing || !existing[p.memberId] || existing[p.memberId].key !== p.key) return
  wx.setStorageSync(CLAIMS_KEY, Object.fromEntries(Object.entries(existing).filter(([id]) => id !== p.memberId)))
}
function validResult(result, pending) {
  return result && result.campaignId === pending.campaignId && result.quantity === 1 && Array.isArray(result.couponIds) && result.couponIds.length === 1 && typeof result.couponIds[0] === 'string'
}
module.exports = { presentCampaign, presentCoupon, readPending, savePending, clearPending, validResult, STATUSES }
