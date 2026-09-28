const { dateLabel } = require('./orders')
const INTENT_KEY = 'mall.exchangeIntent.v1'
function integer(value, min = 0) { return Number.isSafeInteger(value) && value >= min }
function quantity(value) {
  if (!/^\d+$/.test(String(value)) || !integer(Number(value), 1) || Number(value) > 99) throw new Error('请填写 1—99 的整数兑换数量。')
  return Number(value)
}
function imageUrl(path, base) { return typeof path === 'string' && path.startsWith('/') && !path.startsWith('//') ? `${base}${path}` : '' }
function presentOffer(row, base = '') {
  if (!row || !['id','skuId','productId','productName','saleUnit'].every(key => typeof row[key] === 'string' && row[key]) ||
      !integer(row.pointsPrice, 1) || !integer(row.availableQuantity) || !['SHIP','REDEEM'].includes(row.fulfillmentKind) || !Array.isArray(row.specs) || !row.specs.every(s => s && typeof s.name === 'string' && typeof s.value === 'string')) throw new Error('积分兑换商品资料不完整，请重新加载。')
  return { ...row, pointsLabel: `${row.pointsPrice} 积分`, image: imageUrl(row.imageUrl, base),
    specLabel: row.specs.length ? row.specs.map(s => `${s.name}：${s.value}`).join(' · ') : '默认规格',
    fulfillmentLabel: row.fulfillmentKind === 'SHIP' ? '快递配送' : '到店核销',
    deliveryCopy: row.fulfillmentKind === 'SHIP' ? '积分售价已包含配送，快递包邮，无需现金付款。' : `到店出示订单核销凭证，固定截止日 ${row.redeemValidUntil || '待核实'}。`,
    rulesCopy: '纯积分兑换，不使用优惠券或现金抵扣比例，不计入会员消费累计，不产生消费奖励积分。' }
}
function presentQuote(row) {
  if (!row || typeof row.quoteId !== 'string' || row.orderKind !== 'POINTS' || typeof row.offerId !== 'string' ||
      !integer(row.quantity, 1) || row.quantity > 99 || !integer(row.pointsUnitPrice, 1) || !integer(row.totalPoints, 1) ||
      row.totalPoints !== row.quantity * row.pointsUnitPrice || !integer(row.availablePoints) || row.shippingFeeFen !== 0 ||
      !Number.isFinite(Date.parse(row.expiresAt)) || typeof row.ready !== 'boolean' || typeof row.exchangeOrderAvailable !== 'boolean' ||
      !row.line || !['SHIP','REDEEM'].includes(row.line.fulfillmentKind)) throw new Error('服务端积分报价不完整，请重新核对。')
  return { ...row, totalLabel: `${row.totalPoints} 积分`, availableLabel: `${row.availablePoints} 积分`,
    remainingLabel: `${Math.max(0, row.availablePoints - row.totalPoints)} 积分`, expiryLabel: dateLabel(row.expiresAt) }
}
function stored(memberId) {
  const values = wx.getStorageSync(INTENT_KEY), p = values && values[memberId]
  if (!p || p.memberId !== memberId || typeof p.offerId !== 'string' || !integer(p.totalPoints, 1) || !p.body || typeof p.body.quoteId !== 'string' ||
      !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(p.key)) return null
  return { ...p, body: { quoteId: p.body.quoteId } }
}
function save(p) {
  try {
    const values = wx.getStorageSync(INTENT_KEY)
    wx.setStorageSync(INTENT_KEY, { ...(values && typeof values === 'object' ? values : {}), [p.memberId]: { ...p, body: { quoteId: p.body.quoteId } } })
    const saved = stored(p.memberId)
    if (!saved || saved.key !== p.key || saved.body.quoteId !== p.body.quoteId || saved.totalPoints !== p.totalPoints) throw Error('readback')
  } catch (_) { throw new Error('无法保存兑换凭证，未提交兑换。请检查设备存储后重试。') }
}
function clear(p) {
  const values = wx.getStorageSync(INTENT_KEY)
  if (!values || !values[p.memberId] || values[p.memberId].key !== p.key) return
  wx.setStorageSync(INTENT_KEY, Object.fromEntries(Object.entries(values).filter(([id]) => id !== p.memberId)))
}
function validResult(row, p) {
  return !!row && typeof row.orderId === 'string' && row.orderKind === 'POINTS' && row.paymentMethod === 'POINTS' && row.exchangePoints === p.totalPoints && row.status === 'PAID'
}
module.exports = { quantity, presentOffer, presentQuote, stored, save, clear, validResult }
