const test = require('node:test')
const assert = require('node:assert/strict')
const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
global.wx = { getStorageSync: (key) => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key), navigateTo: () => {}, navigateBack: () => {},
  showModal: ({ success }) => success({ confirm: true }), stopPullDownRefresh: () => {} }
function pageAt() {
  let definition; global.Page = (value) => { definition = value }
  const path = '../pages/aftersales/detail.js'; delete require.cache[require.resolve(path)]; require(path)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
const row = { caseId: 'case-1', orderId: 'order-1', lineId: 'line-1', status: 'WAITING_RETURN',
  revision: 2, fulfillmentKind: 'SHIP', kind: 'RETURN_REFUND', quantity: 3, amountFen: 1001,
  reason: '收到商品需要退货', events: [], createdAt: '2026-09-27T10:00:00Z', canWithdraw: false,
  canSubmitReturnShipment: true, returnShipment: null, returnAcceptance: null }
function success(request, value) { request.success({ statusCode: 200, data: { success: true, data: value } }) }
function failure(request, statusCode, message) { request.success({ statusCode, data: { success: false, error: { message } } }) }
function prime() {
  storage.clear(); storage.set('mall.memberToken', 'A')
  const page = pageAt(); page.caseId = 'case-1'; page.memberToken = 'A'
  page.setData({ state: 'ready', saleCase: require('../lib/aftersales').presentCase(row), carrierName: '顺丰速运', trackingNo: 'SF123456' })
  return page
}
test('presentation retains original request while showing partial acceptance and waived-return reason', () => {
  const sale = require('../lib/aftersales')
  const accepted = sale.presentCase({ ...row, status: 'WAITING_REFUND', canSubmitReturnShipment: false,
    effectiveRefundQuantity: 2, effectiveRefundAmountFen: 667, refundStatus: 'PROCESSING',
    returnAcceptance: { mode: 'RECEIVED', receivedQuantity: 2, salableQuantity: 1, damagedQuantity: 1,
      refundQuantity: 2, refundAmountFen: 667, maxRefundAmountFen: 667, reason: '验收通过两件，剩余一件未收到', acceptedAt: '2026-09-27T11:00:00Z' } })
  assert.equal(accepted.quantity, 3); assert.equal(accepted.amountLabel, '¥10.01')
  assert.equal(accepted.effectiveAmountLabel, '¥6.67'); assert.equal(accepted.returnAcceptance.amountLabel, '¥6.67')
  assert.equal(accepted.returnAcceptance.partial, true); assert.match(accepted.refundLabel, /待核对/)
  assert.equal(accepted.canSubmitReturnShipment, false)
  const waived = sale.presentCase({ ...row, status: 'WAITING_REFUND',
    returnAcceptance: { mode: 'WAIVED_RETURN', receivedQuantity: 0, salableQuantity: 0, damagedQuantity: 0,
      refundQuantity: 3, refundAmountFen: 1001, reason: '商品破损，平台批准无需寄回', acceptedAt: '2026-09-27T11:00:00Z' } })
  assert.equal(waived.returnAcceptance.modeLabel, '已批准免寄回'); assert.match(waived.returnAcceptance.reason, /无需寄回/)
})
test('server return permission is required and carrier/tracking validation rejects unsafe inputs', async () => {
  const page = prime(); let count = 0; global.wx.request = () => { count += 1 }
  page.setData({ carrierName: '', trackingNo: 'SF123456' }); await page.submitReturnShipment()
  assert.equal(count, 0); assert.match(page.data.actionError, /快递公司/)
  page.setData({ carrierName: '顺丰', trackingNo: 'SF\n123456' }); await page.submitReturnShipment()
  assert.equal(count, 0); assert.match(page.data.actionError, /运单号/)
  page.setData({ carrierName: '顺丰', trackingNo: 'SF123456', saleCase: { ...page.data.saleCase, canSubmitReturnShipment: false } })
  await page.submitReturnShipment(); assert.equal(count, 0)
})
test('unknown shipment outcome freezes body/key across reopen and refresh must precede retry', async () => {
  const page = prime(); const requests = []
  global.wx.request = (request) => { requests.push(request); request.fail() }
  await page.submitReturnShipment(); assert.equal(page.data.returnUncertain, true)
  page.onCarrierName({ detail: { value: '不能改变' } }); assert.equal(page.data.carrierName, '顺丰速运')
  await page.submitReturnShipment(); assert.equal(requests.length, 1)
  const reopened = pageAt(); global.wx.request = (request) => success(request, row)
  await reopened.onLoad({ id: 'case-1' }); assert.equal(reopened.data.returnUncertain, true)
  global.wx.request = (request) => { requests.push(request); success(request, { ...row, revision: 3,
    returnShipment: { carrierName: '顺丰速运', trackingNo: 'SF123456', revision: 1, submittedAt: '2026-09-27T11:00:00Z' } }) }
  await reopened.submitReturnShipment()
  assert.equal(requests[1].header['Idempotency-Key'], requests[0].header['Idempotency-Key'])
  assert.deepEqual(requests[1].data, { expectedRevision: 2, carrierName: '顺丰速运', trackingNo: 'SF123456' })
  assert.equal(reopened.data.returnUncertain, false)
})
test('unknown write resolved by authoritative read does not submit again', async () => {
  const page = prime(); let posts = 0
  global.wx.request = (request) => { posts += 1; request.fail() }; await page.submitReturnShipment()
  global.wx.request = (request) => success(request, { ...row, revision: 3,
    returnShipment: { carrierName: '顺丰速运', trackingNo: 'SF123456', revision: 1, submittedAt: '2026-09-27T11:00:00Z' } })
  await page.refresh(); assert.equal(page.data.returnUncertain, false); assert.match(page.data.actionMessage, /已登记/)
  assert.equal(posts, 1)
})
test('definitive revision conflict refreshes and stops correction after acceptance', async () => {
  const page = prime()
  global.wx.request = (request) => request.method === 'POST' ? failure(request, 409, '验收已完成，请刷新') :
    success(request, { ...row, status: 'WAITING_REFUND', revision: 4, canSubmitReturnShipment: false })
  await page.submitReturnShipment(); assert.equal(page.returnIntent, null)
  assert.equal(page.data.saleCase.canSubmitReturnShipment, false); assert.match(page.data.actionError, /验收已完成/)
})
test('member switching and unload prevent shipment responses from restoring private state', async () => {
  let page = prime(); let pending; global.wx.request = (request) => { pending = request }
  const old = page.submitReturnShipment(); storage.set('mall.memberToken', 'B')
  success(pending, { ...row, revision: 3 }); await old
  assert.equal(page.data.saleCase, null); assert.equal(page.data.carrierName, '')
  assert.equal(page.returnIntent, null)
  page = prime(); const write = page.submitReturnShipment(); page.onUnload()
  success(pending, { ...row, revision: 3 }); await write
  assert.equal(page.data.actionMessage, '')
})
test('idle member change drops retained form and pending key before reading new session', async () => {
  const page = prime(); global.wx.request = (request) => request.fail(); await page.submitReturnShipment()
  page.shown = true; storage.set('mall.memberToken', 'B')
  global.wx.request = (request) => failure(request, 404, '不可查看')
  await page.onShow(); assert.equal(page.data.carrierName, ''); assert.equal(page.data.trackingNo, '')
  assert.equal(page.returnIntent, null); assert.equal(page.data.returnUncertain, false)
})
test('malformed success preserves unknown request and a late old write cannot erase new member view', async () => {
  const page = prime(); let waiting; global.wx.request = (request) => { waiting = request }
  const write = page.submitReturnShipment(); success(waiting, { status: 'WAITING_RETURN' }); await write
  assert.ok(page.returnIntent); assert.equal(page.data.returnUncertain, true)
  const reload = page.refresh(); storage.set('mall.memberToken', 'B'); page.clearPrivate(); page.memberToken = 'B'
  page.generation += 1; page.setData({ state: 'ready', saleCase: { ...row, caseId: 'B-case' } })
  success(waiting, row)
  await reload
  assert.equal(page.data.saleCase.caseId, 'B-case')
})
test('known field error keeps editable form; refreshing same member preserves an unsaved correction', async () => {
  const page = prime(); page.onCarrierName({ detail: { value: '新的实际承运商' } })
  global.wx.request = (request) => request.method === 'POST' ? failure(request, 400, '请核对运单号') : success(request, row)
  await page.submitReturnShipment(); assert.equal(page.returnIntent, null)
  page.onTrackingNo({ detail: { value: 'RETRY1234' } }); await page.refresh()
  assert.equal(page.data.carrierName, '新的实际承运商'); assert.equal(page.data.trackingNo, 'RETRY1234')
})
test('zero-approved return acceptance reports no money refund rather than a pending refund', () => {
  const presented = require('../lib/aftersales').presentCase({ ...row, status: 'REJECTED',
    returnAcceptance: { mode: 'RECEIVED', receivedQuantity: 1, salableQuantity: 0, damagedQuantity: 1,
      refundQuantity: 0, refundAmountFen: 0, reason: '验收不符退款条件，说明处置依据', acceptedAt: '2026-09-27T11:00:00Z' } })
  assert.match(presented.returnAcceptance.statusCopy, /未批准退款/)
  assert.doesNotMatch(presented.returnAcceptance.statusCopy, /退款尚未完成/)
})
