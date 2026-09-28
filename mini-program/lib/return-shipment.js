const { requestKey } = require('./orders')
const STORAGE_KEY = 'mall.returnShipmentIntent.v1'
function validateBody(value) {
  if (!value || !Number.isSafeInteger(value.expectedRevision) || value.expectedRevision < 1) {
    throw new Error('售后状态已变化，请刷新后重新填写。')
  }
  const carrierName = typeof value.carrierName === 'string' ? value.carrierName.trim() : ''
  const trackingNo = typeof value.trackingNo === 'string' ? value.trackingNo.trim() : ''
  if (!carrierName || carrierName.length > 80 || /[\x00-\x1f\x7f]/.test(carrierName)) {
    throw new Error('请填写快递公司名称，最多80字。')
  }
  if (!trackingNo || trackingNo.length > 80 || /[\x00-\x1f\x7f]/.test(trackingNo)) throw new Error('请填写运单号，最多80字，不能含控制字符。')
  return { expectedRevision: value.expectedRevision, carrierName, trackingNo }
}
function owner() { return wx.getStorageSync('mall.memberToken') || '' }
// Store no bearer token; exact owner is checked at runtime and local digest scopes recovery only.
function fingerprint(token) {
  let hash = 2166136261
  for (let index = 0; index < token.length; index += 1) hash = Math.imul(hash ^ token.charCodeAt(index), 16777619)
  return `${token.length}-${hash >>> 0}`
}
function saveIntent(caseId, body) {
  const value = { key: requestKey(), body: validateBody(body), owner: fingerprint(owner()) }
  wx.setStorageSync(`${STORAGE_KEY}.${caseId}`, value)
  return { key: value.key, body: { ...value.body } }
}
function loadIntent(caseId) {
  const value = wx.getStorageSync(`${STORAGE_KEY}.${caseId}`)
  if (!value) return null
  if (!owner() || value.owner !== fingerprint(owner())) { clearIntent(caseId); return null }
  try {
    if (typeof value.key !== 'string' || !/^[a-f0-9-]{36}$/.test(value.key)) throw new Error('Invalid key')
    return { key: value.key, body: validateBody(value.body) }
  } catch (_) { clearIntent(caseId); return null }
}
function clearIntent(caseId, key) {
  const storageKey = `${STORAGE_KEY}.${caseId}`
  const current = wx.getStorageSync(storageKey)
  if (!key || (current && current.key === key)) wx.removeStorageSync(storageKey)
}
function matchesRecorded(intent, row) {
  return !!(intent && row.returnShipment && row.revision > intent.body.expectedRevision &&
    row.returnShipment.carrierName === intent.body.carrierName && row.returnShipment.trackingNo === intent.body.trackingNo)
}
module.exports = { validateBody, saveIntent, loadIntent, clearIntent, matchesRecorded }
