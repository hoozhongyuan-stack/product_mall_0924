const test = require('node:test')
const assert = require('node:assert/strict')
const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
global.wx = { getStorageSync: (key) => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key), navigateTo: () => {}, navigateBack: () => {},
  showModal: ({ success }) => success({ confirm: true }), stopPullDownRefresh: () => {} }
function pageAt(path) {
  let definition; global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(path)]; require(path)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
function success(options, data) { options.success({ statusCode: 200, data: { success: true, data } }) }
function failure(options, statusCode, code, message) { options.success({ statusCode, data: { success: false, error: { code, message } } }) }
const saleCase = { caseId: 'case-1', orderId: 'order-1', lineId: 'line-1', status: 'PENDING_REVIEW',
  revision: 1, kind: 'REFUND_ONLY', quantity: 1, amountFen: 101, reason: '不再需要该商品', events: [],
  createdAt: '2026-09-27T10:00:00Z', canWithdraw: true }

test('case presentation distinguishes waiting confirmation from a completed refund', () => {
  const { presentCase } = require('../lib/aftersales')
  const waiting = presentCase({ ...saleCase, status: 'WAITING_REFUND', refund: { status: 'UNKNOWN' } })
  assert.equal(waiting.canWithdraw, false)
  assert.match(waiting.statusCopy, /核实|确认/)
  assert.doesNotMatch(waiting.statusCopy, /退款已完成/)
  const done = presentCase({ ...saleCase, status: 'COMPLETED', canWithdraw: false })
  assert.equal(done.statusLabel, '已完成'); assert.equal(done.amountLabel, '¥1.01')
  assert.equal(presentCase({ ...saleCase, status: 'REJECTED', canWithdraw: true }).canWithdraw, false)
})

test('draft key persists only same account and same order and retry retains immutable body', () => {
  const sale = require('../lib/aftersales'); storage.clear(); storage.set('mall.memberToken', 'member-A')
  const intent = sale.saveIntent('order-1', { lineId: 'line-1', kind: 'REFUND_ONLY', quantity: 1,
    reason: '不再需要该商品', redemptionScope: 'UNUSED' })
  assert.ok(intent.key); assert.deepEqual(sale.loadIntent('order-1').body, intent.body)
  assert.equal(sale.loadIntent('order-2'), null)
  storage.set('mall.memberToken', 'member-B'); assert.equal(sale.loadIntent('order-1'), null)
})
const options = { orderId: 'order-1', items: [{ lineId: 'line-1', title: '体验商品', quantity: 2,
  payableFen: 202, options: [{ kind: 'REFUND_ONLY', redemptionScope: 'UNUSED', maxQuantity: 2 }] }] }
function primeIndex() {
  storage.clear(); storage.set('mall.memberToken', 'member-A')
  const page = pageAt('../pages/aftersales/index.js'); page.orderId = 'order-1'; page.memberToken = 'member-A'
  page.setData({ state: 'ready', lines: options.items, selectedLine: options.items[0],
    selectedOption: options.items[0].options[0], quantity: '1', reason: '不再需要该商品', preview: { amountFen: 101 }, previewState: 'ready' })
  return page
}
test('application retries unknown result with same key and body even after reopening', async () => {
  const page = primeIndex(); const requests = []
  global.wx.request = (request) => { requests.push(request); request.fail() }
  await page.submit(); assert.equal(page.data.uncertain, true)
  page.onReason({ detail: { value: '尝试更改申请说明' } }); assert.equal(page.data.reason, '不再需要该商品')
  const reopened = pageAt('../pages/aftersales/index.js')
  global.wx.request = (request) => request.url.includes('aftersale-options') ? success(request, options) :
    success(request, { items: [], page: 1, total: 0 })
  await reopened.onLoad({ orderId: 'order-1' }); assert.equal(reopened.data.uncertain, true)
  global.wx.request = (request) => { requests.push(request); success(request, saleCase) }
  await reopened.submit()
  assert.equal(requests[1].header['Idempotency-Key'], requests[0].header['Idempotency-Key'])
  assert.deepEqual(requests[1].data, requests[0].data)
  assert.equal(Object.hasOwn(requests[1].data, 'amountFen'), false)
})
test('server preview controls displayed amount and delayed previous selection cannot win', async () => {
  const page = primeIndex(); const waiting = []; global.wx.request = (request) => waiting.push(request)
  const first = page.previewSelection(); page.setData({ quantity: '2' }); const second = page.previewSelection()
  success(waiting[1], { lineId: 'line-1', quantity: 2, amountFen: 202 }); await second
  success(waiting[0], { lineId: 'line-1', quantity: 1, amountFen: 999 }); await first
  assert.equal(page.data.preview.amountFen, 202)
})
test('definitive stale rejection clears retry then refreshes server eligibility', async () => {
  const page = primeIndex(); let calls = 0
  global.wx.request = (request) => {
    calls += 1
    if (request.method === 'POST') failure(request, 409, 'AFTERSALE_CONFLICT', '已发货，请重新核对')
    else if (request.url.includes('aftersale-options')) success(request, { ...options, items: [] })
    else success(request, { items: [], total: 0, page: 1 })
  }
  await page.submit(); assert.equal(page.intent, null); assert.equal(page.data.uncertain, false)
  assert.ok(calls >= 3); assert.match(page.data.actionError, /重新核对|已发货/)
})
test('member switch discards private async detail result and an old 401 does not log out new member', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member-A')
  const page = pageAt('../pages/aftersales/detail.js'); page.caseId = 'case-1'
  let waiting; global.wx.request = (request) => { waiting = request }
  const load = page.refresh(); storage.set('mall.memberToken', 'member-B')
  success(waiting, saleCase); await load
  assert.equal(page.data.saleCase, null); assert.equal(page.data.state, 'auth')
  const api = require('../lib/api'); storage.set('mall.memberToken', 'member-A')
  const old = api.get('/old'); storage.set('mall.memberToken', 'member-B')
  failure(waiting, 401, 'UNAUTHENTICATED', '旧会话过期'); await assert.rejects(old)
  assert.equal(storage.get('mall.memberToken'), 'member-B')
})
test('withdraw requires confirmation and sends current revision, review conflict refreshes', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member-A')
  const page = pageAt('../pages/aftersales/detail.js'); page.caseId = 'case-1'; page.memberToken = 'member-A'
  page.setData({ state: 'ready', saleCase: require('../lib/aftersales').presentCase(saleCase) })
  let calls = 0; global.wx.showModal = ({ success }) => success({ confirm: false }); global.wx.request = () => { calls += 1 }
  await page.withdraw(); assert.equal(calls, 0)
  global.wx.showModal = ({ success }) => success({ confirm: true })
  global.wx.request = (request) => {
    if (request.method === 'POST') { assert.deepEqual(request.data, { expectedRevision: 1 }); failure(request, 409, 'AFTERSALE_CONFLICT', '申请已审核') }
    else success(request, { ...saleCase, status: 'WAITING_REFUND', revision: 2, canWithdraw: false })
  }
  await page.withdraw(); assert.equal(page.data.saleCase.canWithdraw, false); assert.match(page.data.actionError, /已审核/)
})
test('changing line invalidates previous preview; unloading prevents private write responses', async () => {
  const page = primeIndex(); let waiting; global.wx.request = (request) => { waiting = request }
  const pending = page.previewSelection(); page.selectLine(0)
  success(waiting, { lineId: 'line-1', quantity: 1, amountFen: 101 }); await pending
  assert.equal(page.data.preview, null)
  page.setData({ preview: { amountFen: 101 }, previewState: 'ready', reason: '不再需要该商品' })
  let navigated = false; global.wx.navigateTo = () => { navigated = true }
  const submit = page.submit(); page.onUnload(); success(waiting, saleCase); await submit
  assert.equal(navigated, false); assert.equal(page.data.actionMessage, '')
})
test('scope labels distinguish shipment and safe unknown refund progress', () => {
  const sale = require('../lib/aftersales')
  assert.equal(sale.optionLabel({ kind: 'REFUND_ONLY', redemptionScope: 'UNUSED' }, 'SHIP'), '仅退款')
  const row = sale.presentCase({ ...saleCase, fulfillmentKind: 'SHIP', refundStatus: 'UNKNOWN' })
  assert.equal(row.scopeLabel, '实物商品'); assert.match(row.refundLabel, /结果未知/)
})
test('pagination retains records on retry error and login states clear private forms', async () => {
  const page = primeIndex(); page.setData({ rows: [saleCase], page: 1, total: 21 })
  global.wx.request = (request) => request.fail()
  await page.loadMore(); assert.equal(page.data.rows.length, 1); assert.equal(page.data.loadingMore, false); assert.ok(page.data.moreError)
  storage.delete('mall.memberToken'); await page.refresh()
  assert.equal(page.data.state, 'auth'); assert.equal(page.data.reason, ''); assert.deepEqual(page.data.rows, [])
})
test('order redemption display shows available capacity after active after-sale holds', () => {
  const orders = require('../lib/orders')
  const result = orders.present({ orderId: 'o', status: 'PAID', paymentMethod: 'OFFLINE', items: [
    { orderLineId: 'l', fulfillmentKind: 'REDEEM', quantity: 2, unitPriceFen: 100, payableFen: 200,
      fulfillment: { kind: 'REDEEM', status: 'AFTER_SALE', remainingQuantity: 2, heldQuantity: 1, availableQuantity: 1 } }] })
  assert.equal(result.items[0].redemption.availableQuantity, 1)
  assert.equal(result.items[0].redemption.heldQuantity, 1)
})
test('idle retained page member switch clears old draft and retry before rebinding session', async () => {
  const page = primeIndex(); const sale = require('../lib/aftersales')
  page.intent = sale.saveIntent('order-1', { lineId: 'line-1', kind: 'REFUND_ONLY', redemptionScope: 'UNUSED', quantity: 1, reason: '旧会员私有申请说明' })
  page.setData({ reason: '旧会员私有申请说明', uncertain: true }); page.shown = true
  storage.set('mall.memberToken', 'member-B')
  let posts = 0
  global.wx.request = (request) => {
    if (request.method === 'POST') posts += 1
    failure(request, 404, 'NOT_FOUND', '订单不可查看')
  }
  await page.onShow()
  assert.equal(page.intent, null); assert.equal(page.data.reason, ''); assert.equal(page.data.uncertain, false)
  await page.submit(); assert.equal(posts, 0)
})

test('zero cash aftersale preview permits quantity-based application without claiming funds',async()=>{
 const page=primeIndex();global.wx.request=request=>success(request,{lineId:'line-1',quantity:1,amountFen:0})
 await page.previewSelection();assert.equal(page.data.previewState,'ready');assert.equal(page.data.preview.amountFen,0)
 assert.match(page.data.preview.cashCopy,/没有现金/)
})
test('member separates goods and shipping and zero completed copy from cash completion',()=>{
 const sale=require('../lib/aftersales')
 const zero=sale.presentCase({...saleCase,amountFen:0,effectiveRefundAmountFen:0,goodsRefundAmountFen:0,shippingRefundAmountFen:0,totalRefundAmountFen:0,status:'COMPLETED'})
 assert.equal(zero.zeroCash,true);assert.match(zero.statusCopy,/没有现金/);assert.doesNotMatch(zero.statusCopy,/到账/)
 const cash=sale.presentCase({...saleCase,goodsRefundAmountFen:101,shippingRefundAmountFen:1000,totalRefundAmountFen:1101})
 assert.equal(cash.shippingAmountLabel,'¥10');assert.equal(cash.totalAmountLabel,'¥11.01')
})
