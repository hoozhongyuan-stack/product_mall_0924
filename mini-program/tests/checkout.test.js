const test = require('node:test')
const assert = require('node:assert/strict')
const cart = require('../lib/cart')
const session = require('../lib/session')
const api = require('../lib/api')

const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
global.wx = {
  getStorageSync(key) { return storage.get(key) },
  setStorageSync(key, value) { storage.set(key, value) },
  removeStorageSync(key) { storage.delete(key) },
}

test('cart merges SKU, updates quantity and quote keeps only price hint', () => {
  storage.clear()
  cart.add({ skuId: 's1', quantity: 1, seenPriceFen: 1200, name: '商品' })
  cart.add({ skuId: 's1', quantity: 2, seenPriceFen: 1200 })
  assert.equal(cart.read().length, 1)
  assert.equal(cart.read()[0].quantity, 3)
  assert.deepEqual(cart.quoteItems(cart.selected()), [{ skuId: 's1', quantity: 3, seenPriceFen: 1200 }])
  cart.remove('s1')
  assert.deepEqual(cart.read(), [])
})

test('login recovery stores only SKU, quantity and non-personal price hint, once', () => {
  storage.clear()
  session.rememberCheckout([{ skuId: 's1', quantity: 2, name: '不保留',
    seenPriceFen: 1200, addressId: 'private' }])
  assert.deepEqual(session.recoverCheckout(), [{ skuId: 's1', quantity: 2, seenPriceFen: 1200 }])
  assert.deepEqual(session.recoverCheckout(), [])
})

test('API sends bearer token and clears it after 401', async () => {
  storage.clear()
  storage.set('mall.memberToken', 'token')
  let header = ''
  global.wx.request = (options) => {
    header = options.header.Authorization
    options.success({ statusCode: 401, data: { success: false,
      error: { code: 'SESSION_EXPIRED', message: '请重新登录。' } } })
  }
  await assert.rejects(api.post('/api/v1/app/checkout/quotes', { items: [] }), /请重新登录/)
  assert.equal(header, 'Bearer token')
  assert.equal(storage.has('mall.memberToken'), false)
})

test('WeChat login exchanges code and stores only app token', async () => {
  storage.clear()
  global.wx.login = ({ success }) => success({ code: 'one-use-code' })
  global.wx.request = (options) => {
    assert.equal(options.data.code, 'one-use-code')
    options.success({ statusCode: 200, data: { success: true, data: { accessToken: 'server-token' } } })
  }
  await session.login()
  assert.equal(storage.get('mall.memberToken'), 'server-token')
  assert.equal(storage.has('session_key'), false)
})

test('checkout quotes once on first show and restores selection after login', async () => {
  storage.clear()
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 1, seenPriceFen: 1200 }])
  let calls = 0
  global.wx.request = (options) => {
    calls += 1
    options.success({ statusCode: 201, data: { success: true, data: {
      lines: [{ skuId: 's1', unitPriceFen: 1200, lineAmountFen: 1200, specs: [], status: 'OK' }],
      goodsTotalFen: 1200, payableFen: 1200, ready: true, confirmRequired: false,
    } } })
  }
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve('../pages/checkout/checkout.js')]
  require('../pages/checkout/checkout.js')
  const page = { ...definition, data: structuredClone(definition.data),
    setData(patch) { this.data = { ...this.data, ...patch } } }
  await page.onLoad()
  page.onShow()
  assert.equal(calls, 1)
  session.rememberCheckout([{ skuId: 's1', quantity: 2, seenPriceFen: 1200 }])
  await page.onShow()
  assert.equal(calls, 2)
  assert.equal(page.items[0].quantity, 2)
})

function checkoutPage() {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve('../pages/checkout/checkout.js')]
  require('../pages/checkout/checkout.js')
  return { ...definition, data: structuredClone(definition.data),
    setData(patch) { this.data = { ...this.data, ...patch } } }
}

test('checkout shows each server settlement amount and keeps public submit closed', async () => {
  storage.clear()
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 1 }])
  let serverAllowsSubmission = false
  global.wx.request = ({ success }) => success({ statusCode: 201, data: { success: true, data: {
    quoteId: 'q1', expiresAt: '2026-09-26T10:00:00+08:00',
    lines: [{ skuId: 's1', unitPriceFen: 10000, lineAmountFen: 10000,
      specs: [], status: 'OK', fulfillmentKind: 'SHIP' }],
    goodsTotalFen: 10000, shippingFeeFen: 1000, couponDiscountFen: 2000,
    pointsDiscountFen: 100, payableFen: 8900, ready: true,
    confirmRequired: false, orderSubmissionAvailable: serverAllowsSubmission,
    availablePoints: 1000, pointsToUse: 100, selectedCouponId: 'coupon-1',
    availableCoupons: [{ id: 'coupon-1', title: '满减券' }],
  } } })
  const page = checkoutPage()
  await page.onLoad()
  assert.equal(page.data.goodsTotal, '¥100')
  assert.equal(page.data.shippingFee, '¥10')
  assert.equal(page.data.couponDiscount, '−¥20')
  assert.equal(page.data.pointsDiscount, '−¥1')
  assert.equal(page.data.total, '¥89')
  assert.equal(page.data.settlementReady, true)
  assert.equal(page.data.canSubmit, false)
  assert.equal(page.data.couponLabel, '满减券')
  serverAllowsSubmission = true
  await page.refresh()
  assert.equal(page.data.canSubmit, false)
})

test('checkout never presents stale goods total as final payable when settlement is absent', async () => {
  storage.clear()
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 1 }])
  global.wx.request = ({ success }) => success({ statusCode: 201, data: { success: true, data: {
    lines: [{ skuId: 's1', unitPriceFen: 1200, lineAmountFen: 1200,
      specs: [], status: 'OK', fulfillmentKind: 'SHIP' }],
    goodsTotalFen: 1200, shippingFeeFen: null, shippingFeePending: true,
    payableFen: 1200, ready: true, confirmRequired: false,
    orderSubmissionAvailable: false,
  } } })
  const page = checkoutPage()
  await page.onLoad()
  assert.equal(page.data.goodsTotal, '¥12')
  assert.equal(page.data.shippingFee, '待核算')
  assert.equal(page.data.total, '待核算')
  assert.equal(page.data.settlementReady, false)
  assert.equal(page.data.canSubmit, false)
})

test('coupon and points selection request a fresh server quote without calculating locally', async () => {
  storage.clear()
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 1 }])
  storage.set('mall.memberToken', 'member-token')
  const bodies = []
  global.wx.request = ({ url, data, success }) => {
    if (url.endsWith('/api/v1/app/addresses')) {
      success({ statusCode: 200, data: { success: true, data: [] } })
      return
    }
    bodies.push(data)
    success({ statusCode: 201, data: { success: true, data: {
      lines: [{ skuId: 's1', unitPriceFen: 1200, lineAmountFen: 1200, specs: [], status: 'OK' }],
      goodsTotalFen: 1200, shippingFeeFen: 0, couponDiscountFen: 0,
      pointsDiscountFen: 0, payableFen: 1200, ready: true,
      confirmRequired: false, orderSubmissionAvailable: false,
      availablePoints: 500, availableCoupons: [{ id: 'c1', title: '优惠券一' }],
      selectedCouponId: data.couponId || null, pointsToUse: data.pointsToUse || 0,
    } } })
  }
  const page = checkoutPage()
  await page.onLoad()
  await page.selectCoupon({ detail: { value: '1' } })
  page.onPointsInput({ detail: { value: '100' } })
  await page.applyPoints()
  assert.equal(bodies.length, 3)
  assert.equal(bodies[1].couponId, 'c1')
  assert.equal(bodies[2].couponId, 'c1')
  assert.equal(bodies[2].pointsToUse, 100)
  assert.equal(page.data.total, '¥12')
})
