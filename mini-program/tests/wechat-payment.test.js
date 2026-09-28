const test = require('node:test')
const assert = require('node:assert/strict')
const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
global.wx = { getStorageSync: (key) => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key), redirectTo: () => {}, navigateTo: () => {}, stopPullDownRefresh: () => {} }
function pageAt(path) {
  let definition; global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(path)]; require(path)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
const pending = { orderId: 'wechat-order', orderNo: 'NO-WX', status: 'PENDING_PAYMENT', paymentMethod: 'WECHAT', wechatPaymentAvailable: true,
  payableFen: 1200, goodsTotalFen: 1200, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0,
  expiresAt: new Date(Date.now() + 86400000).toISOString(), items: [] }
const parameters = { timeStamp: '1789000000', nonceStr: 'server-nonce', package: 'prepay_id=server-prepay', signType: 'RSA', paySign: 'server-signature' }
function success(options, data) { options.success({ statusCode: 200, data: { success: true, data } }) }
function reset() { storage.clear(); storage.set('mall.memberToken', 'member'); delete global.wx.requestPayment }
async function detail(order = pending) { global.wx.request = (options) => success(options, order); const page = pageAt('../pages/orders/detail.js'); await page.onLoad({ id: order.orderId }); return page }
function paymentRoutes(state = 'USERPAYING', order = pending, keys = []) {
  global.wx.request = (options) => {
    if (options.url.endsWith('/prepay')) { keys.push(options.header['Idempotency-Key']); success(options, { attemptId: 'a1', paymentParameters: parameters }) }
    else success(options, options.url.endsWith('/query') ? { paymentState: state } : order)
  }
}
test('checkout WeChat selection requires available server quote and survives refresh', async () => {
  reset(); storage.set('mall.checkoutSelection.v1', [{ skuId: 'sku', quantity: 1 }]); let available = true
  global.wx.request = (options) => {
    if (options.url.endsWith('/addresses')) return success(options, [])
    if (options.url.endsWith('/offline-policy')) return success(options, { configured: true, instructions: '测试说明' })
    success(options, { quoteId: 'q1', lines: [], goodsTotalFen: 1200, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0,
      payableFen: 1200, ready: true, availablePaymentMethods: available ? ['OFFLINE', 'WECHAT'] : [], orderSubmissionAvailable: available })
  }
  const page = pageAt('../pages/checkout/checkout.js'); await page.onLoad(); page.selectPayment({ detail: { value: 'WECHAT' } })
  assert.equal(page.data.paymentMethod, 'WECHAT'); assert.equal(page.data.canSubmit, true)
  await page.refresh(); assert.equal(page.data.paymentMethod, 'WECHAT')
  available = false; await page.refresh(); assert.equal(page.data.canSubmit, false)
})
test('WeChat submit uses selected method and restores unknown request same key', async () => {
  reset(); const page = pageAt('../pages/checkout/checkout.js'); page.items = []; page.setData({ canSubmit: true, quote: { quoteId: 'q1' }, paymentMethod: 'WECHAT' })
  let request; global.wx.request = (options) => { request = options; options.fail() }
  await page.submitOrder(); assert.equal(request.data.paymentMethod, 'WECHAT')
  const retry = pageAt('../pages/checkout/checkout.js'); await retry.onLoad(); assert.equal(retry.data.state, 'uncertain')
  global.wx.request = (options) => { assert.equal(options.header['Idempotency-Key'], request.header['Idempotency-Key']); success(options, pending) }
  await retry.submitOrder(); assert.equal(storage.has('mall.pendingOrder.v1'), false)
})
test('native payment success queries server and never claims local PAID', async () => {
  reset(); const page = await detail(); let invocations = 0
  global.wx.requestPayment = (options) => { invocations += 1; for (const key of Object.keys(parameters)) assert.equal(options[key], parameters[key]); options.success({}) }
  paymentRoutes(); await page.payWechat(); assert.equal(invocations, 1); assert.equal(page.data.isPaid, false); assert.equal(page.data.paymentUncertain, true)
  assert.match(page.data.actionMessage, /服务端/); await page.payWechat(); assert.equal(invocations, 1)
})
test('cancel queries before NOTPAY permits retry and reuses prepay UUID', async () => {
  reset(); const page = await detail(); let invocations = 0; const keys = []
  global.wx.requestPayment = (options) => { invocations += 1; options.fail({ errMsg: 'requestPayment:fail cancel' }) }
  paymentRoutes('NOTPAY', pending, keys); await page.payWechat(); assert.equal(page.data.isPaid, false); assert.equal(page.data.paymentUncertain, false)
  await page.payWechat(); assert.equal(invocations, 2); assert.equal(keys[0], keys[1]); assert.match(page.data.actionMessage, /未支付/)
})
test('unknown prepay retries same UUID after reload without premature native invocation', async () => {
  reset(); const page = await detail(); let first; let invocations = 0; global.wx.requestPayment = () => { invocations += 1 }
  global.wx.request = (options) => { first = options; options.fail() }; await page.payWechat(); assert.equal(invocations, 0)
  const restored = await detail(); global.wx.requestPayment = (options) => { invocations += 1; options.success({}) }
  const keys = []; paymentRoutes('USERPAYING', pending, keys); await restored.payWechat(); assert.equal(invocations, 1); assert.equal(keys[0], first.header['Idempotency-Key'])
})
test('unknown native result survives reload and failed query never reprompts', async () => {
  reset(); const page = await detail(); let invocations = 0
  global.wx.requestPayment = (options) => { invocations += 1; options.fail({ errMsg: 'requestPayment:fail network' }) }
  global.wx.request = (options) => options.url.endsWith('/prepay') ? success(options, { attemptId: 'a1', paymentParameters: parameters }) : options.fail()
  await page.payWechat(); assert.equal(page.data.paymentUncertain, true)
  const restored = await detail(); assert.equal(restored.data.paymentUncertain, true)
  global.wx.request = (options) => options.fail(); await restored.payWechat(); assert.equal(invocations, 1)
})
test('only authoritative order confirms paid even when native result failed', async () => {
  reset(); const page = await detail(); let invocations = 0
  global.wx.requestPayment = (options) => { invocations += 1; options.fail({ errMsg: 'requestPayment:fail unknown' }) }
  paymentRoutes('SUCCESS', { ...pending, status: 'PAID' }); await page.payWechat(); assert.equal(page.data.isPaid, true); assert.equal(page.data.paymentUncertain, false)
  await page.payWechat(); assert.equal(invocations, 1)
})
test('expired unavailable offline zero and successful-funds orders cannot start pay', async () => {
  reset(); let calls = 0
  for (const order of [{ ...pending, expiresAt: '2000-01-01T00:00:00Z' }, { ...pending, wechatPaymentAvailable: false }, { ...pending, paymentMethod: 'OFFLINE' }, { ...pending, payableFen: 0 }, { ...pending, wechatPaymentState: 'SUCCESS' }]) {
    const page = await detail(order); global.wx.request = () => { calls += 1 }; await page.payWechat(); assert.equal(page.data.canPayWechat, false)
  }
  assert.equal(calls, 0)
})
test('duplicate pay clicks and cancel blocked during prepay', async () => {
  reset(); const page = await detail(); let waiting; let calls = 0
  global.wx.request = (options) => { waiting = options; calls += 1 }; const first = page.payWechat(); await page.payWechat(); await page.cancelOrder(); assert.equal(calls, 1)
  waiting.fail(); await first; assert.equal(page.data.paying, false)
})
test('invalid signed parameters never enter native pay', async () => {
  reset(); const page = await detail(); let invocations = 0; global.wx.requestPayment = () => { invocations += 1 }
  global.wx.request = (options) => success(options, { attemptId: 'a1', paymentParameters: { ...parameters, signType: 'MD5' } })
  await page.payWechat(); assert.equal(invocations, 0); assert.match(page.data.actionError, /支付参数/)
})
test('payment 401 clears visible private order and persisted payment intent', async () => {
  reset(); const page = await detail(); page.setData({ note: 'private note' })
  global.wx.request = (options) => options.success({ statusCode: 401, data: { success: false, error: { message: '请登录' } } })
  await page.payWechat(); assert.equal(page.data.state, 'auth'); assert.equal(page.data.order, null); assert.equal(page.data.note, '')
  assert.equal(storage.has('mall.wechatPaymentIntent.v1'), false)
})
test('payment intents for two orders survive independently and omit signed private data', () => {
  reset(); const payment = require('../lib/wechat-payment')
  const one = { orderId: 'one', key: '11111111-1111-4111-8111-111111111111', phase: 'PAYMENT_UNKNOWN' }
  const two = { orderId: 'two', key: '22222222-2222-4222-8222-222222222222', phase: 'PREPAY' }
  payment.save(one); payment.save(two); assert.deepEqual(payment.load('one'), one); assert.deepEqual(payment.load('two'), two)
  payment.clear('two'); assert.deepEqual(payment.load('one'), one); assert.equal(payment.load('two'), null)
  assert.doesNotMatch(JSON.stringify(storage.get('mall.wechatPaymentIntent.v1')), /paySign|nonceStr|memberToken/)
})
test('new successful funds snapshot overrides older NOTPAY query without permitting repay', async () => {
  reset(); const page = await detail(); paymentRoutes('NOTPAY', { ...pending, wechatPaymentState: 'SUCCESS' })
  await page.queryWechat(); assert.equal(page.data.canPayWechat, false); assert.equal(page.data.paymentUncertain, true)
})
test('query 401 clears intent and private data; expired order can query without native pay', async () => {
  reset(); const page = await detail({ ...pending, expiresAt: '2000-01-01T00:00:00Z' }); let native = 0
  global.wx.requestPayment = () => { native += 1 }; paymentRoutes('UNKNOWN', page.data.order)
  await page.queryWechat(); assert.equal(page.data.paymentUncertain, true); assert.equal(native, 0)
  global.wx.request = (options) => options.success({ statusCode: 401, data: { success: false, error: { message: '请登录' } } })
  await page.queryWechat(); assert.equal(page.data.state, 'auth'); assert.equal(page.data.order, null); assert.equal(storage.has('mall.wechatPaymentIntent.v1'), false)
})
test('logout during prepay prevents native invocation and clears private view', async () => {
  reset(); const page = await detail(); let waiting; let native = 0
  global.wx.requestPayment = () => { native += 1 }; global.wx.request = (options) => { waiting = options }
  const request = page.payWechat(); storage.delete('mall.memberToken'); success(waiting, { attemptId: 'a1', paymentParameters: parameters }); await request
  assert.equal(native, 0); assert.equal(page.data.state, 'auth'); assert.equal(page.data.order, null)
})
test('deadline reached while page open forbids prepay without locally closing order', async () => {
  reset(); const page = await detail(); page.setData({ order: { ...pending, expiresAt: '2000-01-01T00:00:00Z' } })
  let calls = 0; global.wx.request = () => { calls += 1 }; await page.payWechat()
  assert.equal(calls, 0); assert.equal(page.data.canPayWechat, false); assert.equal(page.data.order.status, 'PENDING_PAYMENT')
})
test('closed WeChat order can query late payment while remaining closed', async () => {
  reset(); const closed = { ...pending, status: 'CLOSED', closeReason: 'CUSTOMER' }; const page = await detail(closed)
  assert.equal(page.data.showWechatPayment, true); assert.equal(page.data.canPayWechat, false)
  paymentRoutes('SUCCESS', closed); await page.queryWechat(); assert.equal(page.data.isClosed, true); assert.equal(page.data.isPaid, false)
  assert.match(page.data.actionMessage, /异常到账/)
})
test('switching member while prepay in flight clears view and never opens former member payment', async () => {
  reset(); const page = await detail(); let waiting; let native = 0
  global.wx.request = (options) => { waiting = options }; global.wx.requestPayment = () => { native += 1 }
  const operation = page.payWechat(); storage.set('mall.memberToken', 'different-member'); success(waiting, { attemptId: 'a1', paymentParameters: parameters }); await operation
  assert.equal(native, 0); assert.equal(page.data.order, null); assert.equal(page.data.state, 'auth')
})
