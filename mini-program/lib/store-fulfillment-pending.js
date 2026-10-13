// Privacy binding only. The server independently checks identity and exact request digests.
function fingerprint(value) {
  const text = String(value || ''); let hash = 2166136261
  for (let index = 0; index < text.length; index++) hash = Math.imul(hash ^ text.charCodeAt(index), 16777619)
  return `${text.length}-${hash >>> 0}`
}
function storageKey(owner, storeId, orderId) { return `mall.storeFulfillmentPending.v1.${fingerprint(owner)}.${storeId}.${orderId}` }
function save(owner, storeId, orderId, pending) {
  const { voucherCode, pickupCode, ...body } = pending.body
  const credentialField = voucherCode ? 'voucherCode' : pickupCode ? 'pickupCode' : ''
  const value = { key: pending.key, endpoint: pending.endpoint || 'fulfillment', body,
    owner: fingerprint(owner), storeId, orderId, credentialField,
    credentialFingerprint: credentialField ? fingerprint(pending.body[credentialField]) : '' }
  const name = storageKey(owner, storeId, orderId)
  wx.setStorageSync(name, value)
  if (JSON.stringify(wx.getStorageSync(name)) !== JSON.stringify(value)) throw new Error('Operation recovery storage failed')
  return value
}
function read(owner, storeId, orderId) {
  if (!owner) return null
  const value = wx.getStorageSync(storageKey(owner, storeId, orderId))
  if (!value || value.owner !== fingerprint(owner) || value.storeId !== storeId || value.orderId !== orderId ||
      !/^[a-f0-9-]{36}$/i.test(value.key || '') || !['redemptions', 'fulfillment'].includes(value.endpoint) ||
      !value.body || !Number.isSafeInteger(value.body.expectedRevision) || value.body.expectedRevision < 1 ||
      !['', 'voucherCode', 'pickupCode'].includes(value.credentialField) || value.body.voucherCode || value.body.pickupCode) return null
  return { ...value, body: { ...value.body } }
}
function clear(owner, storeId, orderId, key) {
  const name = storageKey(owner, storeId, orderId), value = wx.getStorageSync(name)
  if (value && value.key === key) wx.removeStorageSync(name)
}
function confirmed(order, key) {
  const events = [...(order.events || []), ...(order.localDeliveryEvents || []), ...(order.storeDelivery?.events || []),
    ...(order.items || []).flatMap(item => [...(item.fulfillment?.events || []), ...(item.fulfillment?.records || [])])]
  return events.some(event => event.requestKey === key)
}
module.exports = { storageKey, save, read, clear, confirmed, fingerprint }
