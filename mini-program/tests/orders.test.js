const test = require('node:test')
const assert = require('node:assert/strict')
const api = require('../lib/api')
const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
global.wx = { getStorageSync: (key) => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key), redirectTo: () => {}, navigateTo: () => {},
  showModal: ({ success }) => success({ confirm: true }), stopPullDownRefresh: () => {} }
function pageAt(path) {
  let definition; global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(path)]; require(path)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
const pendingOrder = { orderId: 'order-1', orderNo: 'NO-1', status: 'PENDING_PAYMENT',
  paymentMethod: 'OFFLINE', paymentReviewStatus: 'PENDING_REVIEW', paymentReports: [{ reportId: 'r1', note: '已转账', reportedAt: '2026-09-26T10:00:00Z' }],
  payableFen: 1200, goodsTotalFen: 1200, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0,
  expiresAt: new Date(Date.now() + 86400000).toISOString(), paymentInstructions: { instructions: '测试付款说明' }, items: [] }
function success(options, data) { options.success({ statusCode: 200, data: { success: true, data } }) }

test('API carries order idempotency header without dropping bearer', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  global.wx.request = (options) => { assert.equal(options.header['Idempotency-Key'], 'request-key'); assert.equal(options.header.Authorization, 'Bearer member'); success(options, {}) }
  await api.post('/api/v1/app/orders', {}, { 'Idempotency-Key': 'request-key' })
})
test('offline gate needs server availability and instructions; WeChat requires server availability', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member'); storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 1 }])
  let methods = ['WECHAT']; let configured = true
  global.wx.request = (options) => {
    if (options.url.endsWith('/addresses')) return success(options, [])
    if (options.url.endsWith('/offline-policy')) return success(options, { configured, instructions: '测试说明' })
    return success(options, { quoteId: 'q1', lines: [], goodsTotalFen: 1200, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0, payableFen: 1200, ready: true, availablePaymentMethods: methods, orderSubmissionAvailable: true })
  }
  const page = pageAt('../pages/checkout/checkout.js'); await page.onLoad(); assert.equal(page.data.canSubmit, true)
  methods = ['OFFLINE']; configured = false; await page.refresh(); assert.equal(page.data.canSubmit, false)
  configured = true; await page.refresh(); assert.equal(page.data.canSubmit, true)
})
test('checkout blocks duplicate clicks and retries unknown result with same body and key', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/checkout/checkout.js'); page.items = [{ skuId: 's1', quantity: 1 }]
  page.setData({ canSubmit: true, quote: { quoteId: 'q1' }, paymentMethod: 'OFFLINE' })
  let first; const requests = []; global.wx.request = (options) => { requests.push(options); first = options }
  const operation = page.submitOrder(); await page.submitOrder(); assert.equal(requests.length, 1)
  first.fail(); await operation; assert.equal(page.data.state, 'uncertain')
  const saved = storage.get('mall.pendingOrder.v1'); const restored = pageAt('../pages/checkout/checkout.js'); await restored.onLoad(); assert.equal(restored.data.state, 'uncertain')
  global.wx.request = (options) => { requests.push(options); success(options, pendingOrder) }
  await restored.submitOrder(); assert.equal(requests[1].header['Idempotency-Key'], saved.key); assert.deepEqual(requests[1].data, requests[0].data); assert.equal(storage.has('mall.pendingOrder.v1'), false)
})
test('closed checkout gate makes no order request', async () => {
  storage.clear(); const page = pageAt('../pages/checkout/checkout.js'); let calls = 0; global.wx.request = () => { calls += 1 }; await page.submitOrder(); assert.equal(calls, 0)
})
test('reported payment stays pending; result reads server state', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member'); global.wx.request = (options) => success(options, pendingOrder)
  const page = pageAt('../pages/orders/detail.js'); await page.onLoad({ id: 'order-1', result: '1' })
  assert.equal(page.data.statusLabel, '待付款 · 待核实'); assert.equal(page.data.isPaid, false); assert.equal(page.data.order.paymentReports[0].note, '已转账'); assert.equal(page.data.resultTitle, '订单已提交，等待核实收款')
})
test('report retries same key and cancel requires user confirmation', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member'); const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  page.setData({ state: 'ready', order: pendingOrder, canReport: true, note: '银行流水尾号1234' })
  const keys = []; global.wx.request = (options) => { keys.push(options.header['Idempotency-Key']); options.fail() }
  await page.reportPayment(); await page.reportPayment(); assert.equal(keys[0], keys[1]); assert.ok(keys[0]); assert.equal(page.data.isPaid, false)
  let submitted = false; global.wx.showModal = ({ success }) => success({ confirm: false }); global.wx.request = () => { submitted = true }; page.setData({ canCancel: true }); await page.cancelOrder(); assert.equal(submitted, false)
})
test('detail 401 clears private data and asks login', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member'); global.wx.request = (options) => options.success({ statusCode: 401, data: { success: false, error: { message: '请登录' } } })
  const page = pageAt('../pages/orders/detail.js'); page.setData({ order: pendingOrder }); await page.onLoad({ id: 'order-1' }); assert.equal(page.data.state, 'auth'); assert.equal(page.data.order, null); assert.equal(storage.has('mall.memberToken'), false)
})
test('order pagination replaces refresh and appends next page', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member'); global.wx.request = (options) => success(options, { items: [pendingOrder], page: options.url.includes('page=2') ? 2 : 1, pageSize: 20, total: 21 })
  const page = pageAt('../pages/orders/list.js'); await page.onShow(); assert.equal(page.data.rows.length, 1); await page.loadMore(); assert.equal(page.data.rows.length, 2); await page.refresh(); assert.equal(page.data.rows.length, 1)
})

test('successful user report refreshes pending status and definitive rejection permits correction', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  page.setData({ state: 'ready', order: pendingOrder, canReport: true, note: '已付款' })
  global.wx.request = (options) => success(options, pendingOrder)
  await page.reportPayment()
  assert.equal(page.data.isPaid, false); assert.equal(page.data.statusLabel, '待付款 · 待核实'); assert.equal(page.data.note, '')
  page.onNote({ detail: { value: '新的报告' } })
  global.wx.request = (options) => options.success({ statusCode: 409, data: { success: false, error: { message: '订单已关闭' } } })
  await page.reportPayment(); assert.equal(page.reportIntent, null)
  page.onNote({ detail: { value: '修正说明' } }); assert.equal(page.data.note, '修正说明')
})
test('cancel uses server result and cannot cancel an already paid refreshed order', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  page.setData({ state: 'ready', order: pendingOrder, canCancel: true })
  global.wx.showModal = ({ success }) => success({ confirm: true })
  global.wx.request = (options) => success(options, { ...pendingOrder, status: 'CLOSED', paymentReviewStatus: 'CLOSED', closeReason: 'CANCELLED' })
  await page.cancelOrder(); assert.equal(page.data.isClosed, true); assert.equal(page.data.canReport, false); assert.equal(page.data.canCancel, false)
  global.wx.request = (options) => success(options, { ...pendingOrder, status: 'PAID', paymentReviewStatus: 'PAID' })
  await page.refresh(); assert.equal(page.data.isPaid, true); assert.equal(page.data.canReport, false)
  let calls = 0; global.wx.request = () => { calls += 1 }; await page.cancelOrder(); assert.equal(calls, 0)
})
test('invalid detail does not call server, unavailable target and network errors can retry', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); let calls = 0; global.wx.request = () => { calls += 1 }
  await page.onLoad({}); assert.equal(page.data.state, 'unavailable'); assert.equal(calls, 0)
  page.orderId = 'order-1'
  global.wx.request = (options) => options.success({ statusCode: 404, data: { success: false } })
  await page.refresh(); assert.equal(page.data.state, 'unavailable')
  global.wx.request = (options) => options.fail(); await page.refresh(); assert.equal(page.data.state, 'error')
  global.wx.request = (options) => success(options, pendingOrder); await page.refresh(); assert.equal(page.data.state, 'ready')
})
test('newer detail state wins over delayed old response', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  const waiting = []; global.wx.request = (options) => waiting.push(options)
  const first = page.refresh(); const second = page.refresh()
  success(waiting[1], { ...pendingOrder, status: 'PAID', paymentReviewStatus: 'PAID' }); await second
  success(waiting[0], pendingOrder); await first; assert.equal(page.data.isPaid, true)
})
test('list handles logout, filtering, network retry and failed pagination without losing rows', async () => {
  storage.clear(); const page = pageAt('../pages/orders/list.js'); await page.onShow(); assert.equal(page.data.state, 'auth')
  storage.set('mall.memberToken', 'member'); global.wx.request = (options) => options.fail()
  await page.refresh(); assert.equal(page.data.state, 'error')
  global.wx.request = (options) => success(options, { items: [pendingOrder], total: 21, page: 1, pageSize: 20 })
  await page.refresh(); global.wx.request = (options) => options.fail(); await page.loadMore()
  assert.equal(page.data.rows.length, 1); assert.equal(page.data.loadingMore, false); assert.ok(page.data.moreError)
  global.wx.request = (options) => { assert.ok(options.url.includes('status=CLOSED')); success(options, { items: [], total: 0, page: 1, pageSize: 20 }) }
  await page.filter({ currentTarget: { dataset: { status: 'CLOSED' } } }); assert.equal(page.data.state, 'empty')
})
test('definitive submission rejection clears pending key but authentication failure retains retry', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/checkout/checkout.js'); page.items = []; page.setData({ canSubmit: true, quote: { quoteId: 'q1' } })
  global.wx.request = (options) => options.success({ statusCode: 409, data: { success: false, error: { message: '库存不足' } } })
  await page.submitOrder(); assert.equal(page.data.state, 'error'); assert.equal(storage.has('mall.pendingOrder.v1'), false)
  page.setData({ canSubmit: true, quote: { quoteId: 'q2' } }); global.wx.request = (options) => options.success({ statusCode: 401, data: { success: false, error: { message: '请登录' } } })
  await page.submitOrder(); assert.equal(page.data.state, 'auth'); assert.ok(storage.get('mall.pendingOrder.v1'))
  storage.set('mall.memberToken', 'member'); page.shown = true; page.onShow(); assert.equal(page.data.state, 'uncertain')
})

test('unknown report survives failed refresh and later retries same note and key', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  page.setData({ state: 'ready', order: pendingOrder, canReport: true, note: '银行到账流水参考 ABC' })
  const requests = []; global.wx.request = (options) => { requests.push(options); options.fail() }
  await page.reportPayment(); const key = requests[0].header['Idempotency-Key']
  await page.refresh(); assert.equal(page.data.state, 'error'); assert.equal(page.data.note, '银行到账流水参考 ABC')
  global.wx.request = (options) => success(options, pendingOrder)
  await page.refresh(); assert.equal(page.data.canReport, true); assert.equal(page.data.reportUncertain, true)
  global.wx.request = (options) => { assert.equal(options.header['Idempotency-Key'], key); assert.deepEqual(options.data, { note: '银行到账流水参考 ABC' }); success(options, pendingOrder) }
  await page.reportPayment(); assert.equal(page.reportIntent, null); assert.equal(page.data.isPaid, false)
})

test('logout invalidates in-flight order list response and never restores private rows', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/list.js'); let waiting
  global.wx.request = (options) => { waiting = options }
  const request = page.refresh(); storage.delete('mall.memberToken'); await page.refresh()
  success(waiting, { items: [pendingOrder], total: 1, page: 1, pageSize: 20 }); await request
  assert.equal(page.data.state, 'auth'); assert.deepEqual(page.data.rows, [])
})

test('payment report draft warns before leaving and clears after confirmed report', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  let protectedDraft = false
  global.wx.enableAlertBeforeUnload = () => { protectedDraft = true }
  global.wx.disableAlertBeforeUnload = () => { protectedDraft = false }
  const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  page.setData({ canReport: true, order: pendingOrder })
  page.onNote({ detail: { value: '付款流水说明' } }); assert.equal(protectedDraft, true)
  global.wx.request = (options) => success(options, pendingOrder)
  await page.reportPayment(); assert.equal(protectedDraft, false)
  delete global.wx.enableAlertBeforeUnload; delete global.wx.disableAlertBeforeUnload
})

test('elapsed pending order hides payment guidance and new report without changing server status', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const expired = { ...pendingOrder, expiresAt: '2000-01-01T00:00:00Z' }
  global.wx.request = (options) => success(options, expired)
  const page = pageAt('../pages/orders/detail.js'); await page.onLoad({ id: 'order-1' })
  assert.equal(page.data.order.status, 'PENDING_PAYMENT'); assert.equal(page.data.isExpired, true)
  assert.equal(page.data.canReport, false); assert.equal(page.data.showPaymentInstructions, false)
  assert.match(page.data.statusCopy, /请勿继续付款/)
  let calls = 0; global.wx.request = () => { calls += 1 }; page.setData({ note: '新的报告' }); await page.reportPayment(); assert.equal(calls, 0)
})
test('timeout and customer close reasons have distinct accurate labels', () => {
  const { present } = require('../lib/orders')
  assert.equal(present({ ...pendingOrder, status: 'CLOSED', closeReason: 'TIMEOUT' }).closeReasonLabel, '超时关闭')
  assert.equal(present({ ...pendingOrder, status: 'CLOSED', closeReason: 'CUSTOMER' }).closeReasonLabel, '用户取消')
})
test('unknown prior report can replay same key after deadline while new reports stay disabled', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  page.setData({ state: 'ready', order: pendingOrder, canReport: true, note: '已经付款的报告' })
  let first; global.wx.request = (options) => { first = options; options.fail() }; await page.reportPayment()
  const key = first.header['Idempotency-Key']
  const expired = { ...pendingOrder, expiresAt: '2000-01-01T00:00:00Z' }
  global.wx.request = (options) => success(options, expired); await page.refresh()
  assert.equal(page.data.canReport, false); assert.equal(page.data.reportUncertain, true)
  global.wx.request = (options) => { assert.equal(options.header['Idempotency-Key'], key); success(options, expired) }
  await page.reportPayment(); assert.equal(page.reportIntent, null); assert.equal(page.data.canReport, false)
})


test('deadline reached while detail open blocks a newly clicked report and hides instructions', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = 'order-1'
  page.setData({ state: 'ready', order: { ...pendingOrder, expiresAt: '2000-01-01T00:00:00Z' }, canReport: true, note: '报告草稿' })
  let calls = 0; global.wx.request = () => { calls += 1 }
  await page.reportPayment(); assert.equal(calls, 0); assert.equal(page.data.canReport, false)
  assert.equal(page.data.showPaymentInstructions, false); assert.match(page.data.actionError, /付款时限已到/)
})

test('after-sale and refunded orders present accurate states without payment or fulfillment actions', () => {
  const { present } = require('../lib/orders')
  const paid = { ...pendingOrder, status: 'PAID', fulfillmentStatus: 'AFTER_SALE',
    items: [{ fulfillmentKind: 'REDEEM', quantity: 2, unitPriceFen: 600, payableFen: 1200,
      fulfillment: { kind: 'REDEEM', status: 'AFTER_SALE', remainingQuantity: 2, heldQuantity: 2, availableQuantity: 0 } }] }
  const held = present(paid)
  assert.equal(held.statusLabel, '售后处理中')
  assert.match(held.statusCopy, /售后/)
  assert.equal(held.items[0].fulfillmentStatus, '售后处理中')
  assert.equal(held.canReport, false)
  assert.equal(held.canPayWechat, false)
  const refunded = present({ ...paid, fulfillmentStatus: 'REFUNDED', items: [
    { ...paid.items[0], fulfillment: { ...paid.items[0].fulfillment, status: 'REFUNDED' } }] })
  assert.equal(refunded.statusLabel, '已退款')
  assert.equal(refunded.resultTitle, '订单已退款')
  assert.match(refunded.statusCopy, /退款已确认/)
  assert.equal(refunded.items[0].fulfillmentStatus, '已退款')
  assert.equal(refunded.canConfirmReceipt, false)
})
