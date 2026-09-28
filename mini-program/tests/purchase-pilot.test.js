const test = require('node:test')
const assert = require('node:assert/strict')
const cart = require('../lib/cart')
const storage = new Map()
const productId = '11111111-1111-1111-1111-111111111111'
const event = (dataset) => ({ currentTarget: { dataset } })
const product = {
  productId, name: '经典干红', mainImageUrl: '/api/v1/app/assets/a/file', purchasable: true,
  skus: [
    { skuId: 's1', specs: [{ name: '包装', value: '单瓶' }], listPriceFen: 1200, availableQuantity: 5, cartEligible: true },
    { skuId: 's2', specs: [{ name: '包装', value: '礼盒' }], listPriceFen: 2200, availableQuantity: 2, cartEligible: true },
    { skuId: 'sold', specs: [], listPriceFen: 800, availableQuantity: 0, cartEligible: false },
  ],
}
function mount(file) {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
function setup() {
  storage.clear()
  const navigations = []
  global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
  global.wx = {
    getStorageSync: (key) => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: (key) => storage.delete(key), setNavigationBarTitle() {}, showToast() {},
    navigateTo: ({ url }) => navigations.push(url),
    request: ({ success }) => success({ statusCode: 200, data: { success: true, data: product } }),
  }
  return navigations
}
async function detail() {
  const page = mount('../pages/product/detail.js')
  await page.onLoad({ productId })
  return page
}
test('both purchase entries open a drawer without writing a cart or checkout', async () => {
  const navigations = setup()
  const page = await detail()
  page.addToCart()
  assert.equal(page.data.drawerOpen, true)
  assert.deepEqual(cart.read(), [])
  page.closeSkuDrawer()
  page.buyNow()
  assert.equal(page.data.drawerOpen, true)
  assert.equal(storage.has('mall.checkoutSelection.v1'), false)
  assert.deepEqual(navigations, [])
})
test('drawer rejects unavailable SKUs, caps stock and restores selection on cancel', async () => {
  setup()
  const page = await detail()
  assert.equal(page.data.selectedSkuId, 's1')
  page.openSkuDrawer()
  page.chooseSku(event({ id: 'sold' }))
  assert.equal(page.data.selectedSkuId, 's1')
  page.changeQuantity(event({ step: 1 }))
  page.changeQuantity(event({ step: 1 }))
  page.chooseSku(event({ id: 's2' }))
  assert.equal(page.data.quantity, 2)
  page.changeQuantity(event({ step: 1 }))
  assert.equal(page.data.quantity, 2)
  page.closeSkuDrawer()
  assert.equal(page.data.selectedSkuId, 's1')
  assert.equal(page.data.quantity, 1)
  page.openSkuDrawer()
  assert.equal(page.data.selectedPrice, '¥12')
  page.changeQuantity(event({ step: -1 }))
  assert.equal(page.data.quantity, 1)
})
test('confirmed drawer action uses existing cart and keeps confirmed quantity and count', async () => {
  setup()
  const page = await detail()
  page.addToCart()
  page.changeQuantity(event({ step: 1 }))
  page.confirmAddToCart()
  assert.equal(page.data.drawerOpen, false)
  assert.equal(page.data.cartCount, 2)
  assert.equal(cart.read()[0].quantity, 2)
  assert.equal(cart.read()[0].seenPriceFen, 1200)
  page.openSkuDrawer()
  page.changeQuantity(event({ step: 1 }))
  page.closeSkuDrawer()
  assert.equal(page.data.quantity, 2)
})
test('direct purchase confirms only inside drawer and share contains the product route', async () => {
  const navigations = setup()
  const page = await detail()
  page.buyNow()
  page.chooseSku(event({ id: 's2' }))
  page.confirmBuyNow()
  assert.equal(page.data.drawerOpen, false)
  assert.equal(storage.get('mall.checkoutSelection.v1')[0].skuId, 's2')
  assert.deepEqual(navigations, ['/pages/checkout/checkout'])
  assert.deepEqual(page.onShareAppMessage(), { title: '经典干红', path: `/pages/product/detail?productId=${productId}` })
})
test('drawer failed persistence keeps recoverable selection and unavailable data closes drawer', async () => {
  setup()
  const page = await detail()
  page.addToCart()
  wx.setStorageSync = () => { throw new Error('空间不足') }
  page.confirmAddToCart()
  assert.equal(page.data.drawerOpen, true)
  assert.equal(page.data.drawerError, '空间不足')
  wx.request = ({ success }) => success({ statusCode: 404, data: { success: false } })
  await page.loadDetail()
  assert.equal(page.data.drawerOpen, false)
  assert.equal(page.data.product, null)
})
test('cart exposes old price, actual fulfillment and safe reselect route, keeps invalid row until explicit removal', async () => {
  const navigations = setup()
  cart.add({ skuId: 's1', quantity: 1, seenPriceFen: 1100 })
  wx.request = ({ success }) => success({ statusCode: 201, data: { success: true, data: {
    lines: [{ skuId: 's1', productId, unitPriceFen: 1200, priceChanged: true, status: 'OUT_OF_STOCK', fulfillmentKind: 'SHIP' }],
    goodsTotalFen: 1200, ready: false, confirmRequired: true,
  } } })
  const page = mount('../pages/cart/cart.js')
  await page.onShow()
  assert.equal(page.data.rows[0].previousPrice, '¥11')
  assert.equal(page.data.rows[0].fulfillmentLabel, '快递发货')
  page.reselectSku(event({ id: 's1' }))
  assert.deepEqual(navigations, [`/pages/product/detail?productId=${productId}&selectSku=1&replaceSkuId=s1`])
  assert.equal(cart.read().length, 1)
  page.reselectSku(event({ id: 'missing' }))
  assert.equal(navigations.length, 1)
  await page.confirmPrice(event({ id: 's1' }))
  assert.equal(cart.read()[0].seenPriceFen, 1200)
})
function quote(addressRequired = false) {
  return { quoteId: 'q1', lines: [{ skuId: 's1', quantity: 2, unitPriceFen: 1200, lineAmountFen: 2400, status: 'OK', fulfillmentKind: 'REDEEM' }],
    goodsTotalFen: 2400, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0, payableFen: 2400,
    ready: true, confirmRequired: false, addressRequired, orderSubmissionAvailable: false, availablePaymentMethods: [] }
}
test('guest redeem checkout prompts login without address requirement, retains goods and requotes after login with gate closed', async () => {
  const navigations = setup()
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 2, seenPriceFen: 1200 }])
  const requests = []
  wx.request = ({ url, data, success }) => { requests.push({ url, data }); success({ statusCode: 200, data: { success: true, data: url.endsWith('/addresses') ? [] : quote() } }) }
  const page = mount('../pages/checkout/checkout.js')
  await page.onLoad()
  page.onShow()
  await page.submitOrder()
  assert.equal(page.data.loginPromptOpen, true)
  assert.equal(page.data.lines[0].quantity, 2)
  assert.equal(page.data.state, 'ready')
  page.dismissLoginPrompt()
  assert.equal(page.data.loginPromptOpen, false)
  await page.submitOrder()
  page.loginToContinue()
  assert.deepEqual(navigations, ['/pages/login/login?returnTo=checkout'])
  assert.deepEqual(storage.get('mall.checkoutRecovery.v1'), [{ skuId: 's1', quantity: 2, seenPriceFen: 1200 }])
  storage.set('mall.memberToken', 'member-new')
  await page.onShow()
  assert.equal(page.data.loginPromptOpen, false)
  assert.equal(page.data.loggedIn, true)
  assert.equal(page.data.canSubmit, false)
  assert.equal(requests.filter(({ url }) => url.endsWith('/quotes')).length, 2)
  assert.equal(requests.some(({ url }) => url.endsWith('/orders')), false)
})
test('reselect entry opens drawer after detail arrives and unload ignores late response', async () => {
  setup()
  const page = mount('../pages/product/detail.js')
  await page.onLoad({ productId, selectSku: '1' })
  assert.equal(page.data.drawerOpen, true)
  let respond
  wx.request = ({ success }) => { respond = success }
  const pending = page.loadDetail()
  page.onUnload()
  respond({ statusCode: 200, data: { success: true, data: product } })
  await pending
  assert.equal(page.data.drawerOpen, false)
  assert.equal(page.data.state, 'loading')
})

test('stock rejection keeps order uncreated, requotes actual stock and adjusts only quantities after confirmation', async () => {
  setup()
  storage.set('mall.memberToken', 'member')
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 2, seenPriceFen: 1200 }])
  let stockChanged = false
  const bodies = []
  wx.request = ({ url, data, success }) => {
    if (url.endsWith('/addresses')) return success({ statusCode: 200, data: { success: true, data: [] } })
    if (url.endsWith('/orders')) { stockChanged = true; return success({ statusCode: 409, data: { success: false, error: { code: 'OUT_OF_STOCK', message: '商品库存不足，请重新报价。' } } }) }
    bodies.push(data)
    const result = quote()
    result.orderSubmissionAvailable = true
    result.availablePaymentMethods = ['WECHAT']
    result.ready = !stockChanged || data.items[0].quantity <= 1
    result.lines = [{ ...result.lines[0], quantity: data.items[0].quantity, availableQuantity: stockChanged ? 1 : 5, status: result.ready ? 'OK' : 'OUT_OF_STOCK' }]
    success({ statusCode: 200, data: { success: true, data: result } })
  }
  const page = mount('../pages/checkout/checkout.js')
  await page.onLoad()
  assert.equal(page.data.canSubmit, true)
  await page.submitOrder()
  assert.equal(page.pendingSubmission, null)
  assert.equal(storage.has('mall.pendingOrder.v1'), false)
  assert.equal(page.data.stockPromptOpen, true)
  assert.equal(page.data.lines[0].availableQuantity, 1)
  assert.equal(page.data.stockAdjustable, true)
  assert.equal(page.items[0].quantity, 2)
  await page.adjustStock()
  assert.equal(page.items[0].quantity, 1)
  assert.equal(page.data.stockPromptOpen, false)
  assert.equal(bodies.at(-1).items[0].quantity, 1)
  assert.equal(page.data.canSubmit, true)
})

test('cart selection, cached prices, management and quantity controls preserve server checkout guard', async () => {
  const navigations = setup()
  cart.add({ skuId: 's1', quantity: 2, seenPriceFen: 1200, name: '商品' })
  let requests = 0
  wx.request = ({ success }) => { requests++; success({ statusCode: 200, data: { success: true, data: { ...quote(), lines: [{ ...quote().lines[0], productId }] } } }) }
  const page = mount('../pages/cart/cart.js')
  await page.onShow()
  assert.equal(page.data.selectedCount, 2)
  page.toggleManaging()
  assert.equal(page.data.managing, true)
  page.toggleManaging()
  assert.equal(page.data.managing, false)
  await page.toggleAll()
  assert.equal(page.data.allSelected, false)
  assert.equal(page.data.rows[0].price, '¥12')
  assert.equal(page.data.total, '¥0')
  page.checkout()
  assert.equal(navigations.length, 0)
  await page.toggle({ ...event({ id: 's1' }), detail: { value: ['selected'] } })
  await page.changeQuantity(event({ id: 's1', step: -1 }))
  assert.equal(page.data.selectedCount, 1)
  await page.changeQuantity(event({ id: 's1', step: -1 }))
  assert.equal(cart.read()[0].quantity, 1)
  await page.changeQuantity(event({ id: 'missing', step: 1 }))
  page.checkout()
  assert.equal(storage.get('mall.checkoutSelection.v1')[0].quantity, 1)
  assert.equal(navigations.at(-1), '/pages/checkout/checkout')
  page.onImageError(event({ id: 's1' }))
  assert.equal(page.data.failedImages.s1, true)
  await page.removeItem(event({ id: 's1' }))
  assert.equal(page.data.state, 'empty')
  page.shop(); page.openOrders()
  assert.deepEqual(navigations.slice(-2), ['/pages/index/index', '/pages/orders/list'])
  assert.ok(requests > 0)
})

test('cart reselect cancels without change and confirms an atomic replacement, merging an existing target SKU', async () => {
  const navigations = setup()
  wx.navigateBack = () => navigations.push('back')
  cart.add({ skuId: 'sold', quantity: 1, seenPriceFen: 800, name: '原规格' })
  cart.add({ skuId: 's2', quantity: 1, seenPriceFen: 2200, name: '礼盒' })
  const page = mount('../pages/product/detail.js')
  await page.onLoad({ productId, selectSku: '1', replaceSkuId: 'sold' })
  assert.equal(page.data.replacingCartItem, true)
  page.chooseSku(event({ id: 's2' }))
  page.closeSkuDrawer()
  assert.deepEqual(cart.read().map((row) => row.skuId), ['sold', 's2'])
  page.openSkuDrawer()
  page.chooseSku(event({ id: 's2' }))
  page.confirmAddToCart()
  assert.deepEqual(cart.read().map((row) => [row.skuId, row.quantity, row.seenPriceFen]), [['s2', 2, 2200]])
  assert.equal(navigations.at(-1), 'back')
})

test('cart reselect refuses changed source or merged quantity above stock, without removing any row', async () => {
  setup()
  cart.add({ skuId: 'sold', quantity: 2, seenPriceFen: 800 })
  cart.add({ skuId: 's2', quantity: 1, seenPriceFen: 2200 })
  const page = mount('../pages/product/detail.js')
  await page.onLoad({ productId, selectSku: '1', replaceSkuId: 'sold' })
  page.chooseSku(event({ id: 's2' }))
  page.confirmAddToCart()
  assert.match(page.data.drawerError, /库存/)
  assert.equal(cart.read().length, 2)
  cart.update('sold', { quantity: 1 })
  page.chooseSku(event({ id: 's1' }))
  page.confirmAddToCart()
  assert.match(page.data.drawerError, /购物车.*变化/)
  assert.equal(cart.read().length, 2)
})

test('reselect same SKU keeps other rows and selection state; missing or failed storage never deletes source', async () => {
  const navigations = setup()
  wx.navigateBack = ({ fail }) => fail()
  wx.redirectTo = ({ url }) => navigations.push(url)
  cart.add({ skuId: 's1', quantity: 2, seenPriceFen: 1100 })
  cart.add({ skuId: 'other', quantity: 1, seenPriceFen: 900 })
  cart.update('s1', { selected: false })
  const page = mount('../pages/product/detail.js')
  await page.onLoad({ productId, selectSku: '1', replaceSkuId: 's1' })
  page.confirmBuyNow()
  assert.equal(storage.has('mall.checkoutSelection.v1'), false)
  const before = structuredClone(cart.read())
  const write = wx.setStorageSync
  wx.setStorageSync = () => { throw new Error('无法保存') }
  page.confirmAddToCart()
  assert.deepEqual(cart.read(), before)
  assert.equal(page.data.drawerOpen, true)
  wx.setStorageSync = write
  page.confirmAddToCart()
  assert.deepEqual(cart.read().map((row) => [row.skuId, row.quantity, row.selected]), [['s1', 2, false], ['other', 1, true]])
  assert.equal(cart.read()[0].seenPriceFen, 1200)
  assert.equal(navigations.at(-1), '/pages/cart/cart')
  const missing = mount('../pages/product/detail.js')
  await missing.onLoad({ productId, selectSku: '1', replaceSkuId: 'missing' })
  missing.confirmAddToCart()
  assert.match(missing.data.drawerError, /购物车.*变化/)
  assert.equal(cart.read().length, 2)
})

test('drawer does not bypass unavailable selection or persist outside confirmation, and navigation failure can retry', async () => {
  setup()
  const page = await detail()
  page.confirmAddToCart(); page.confirmBuyNow()
  assert.deepEqual(cart.read(), [])
  page.openSkuDrawer(); page.openSkuDrawer()
  page.setData({ selectedSkuId: 'missing' })
  page.confirmBuyNow()
  assert.match(page.data.drawerError, /选择规格/)
  page.setData({ selectedSkuId: 'sold' })
  page.confirmAddToCart()
  assert.match(page.data.drawerError, /库存/)
  page.chooseSku(event({ id: 's1' }))
  page.setData({ quantity: 6 })
  page.confirmAddToCart()
  assert.match(page.data.drawerError, /库存/)
  page.setData({ quantity: 1 })
  wx.navigateTo = ({ fail }) => fail()
  page.confirmBuyNow()
  assert.equal(page.data.drawerOpen, true)
  assert.match(page.data.drawerError, /打开失败/)
  assert.equal(storage.get('mall.checkoutSelection.v1')[0].quantity, 1)
})

test('detail media fallbacks and page lifecycle refresh actual cart count and safely recover failed loads', async () => {
  const navigations = setup()
  const page = await detail()
  page.onShow()
  cart.add({ skuId: 's1', quantity: 3 })
  await page.onShow()
  assert.equal(page.data.cartCount, 3)
  page.openCart()
  assert.equal(navigations.at(-1), '/pages/cart/cart')
  page.onMediaChange({ detail: { current: 1 } })
  page.onImageError(event({ index: 0 })); page.onVideoError()
  assert.equal(page.data.mediaIndex, 1)
  assert.deepEqual(page.data.failedImages, { 0: true })
  assert.equal(page.data.videoFailed, true)
  let stopped = false
  wx.stopPullDownRefresh = () => { stopped = true }
  page.onPullDownRefresh()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(stopped, true)
  wx.request = ({ fail }) => fail()
  await page.loadDetail()
  assert.equal(page.data.state, 'error')
  page.openSkuDrawer(); page.chooseSku(event({ id: 's1' }))
  assert.equal(page.data.drawerOpen, false)
  assert.equal(page.onShareAppMessage().title, '商品详情')
  const invalid = mount('../pages/product/detail.js')
  await invalid.onLoad({})
  assert.equal(invalid.data.state, 'unavailable')
})

test('empty SKU response leaves a disabled recoverable drawer without inventing sellable stock', async () => {
  setup()
  wx.request = ({ success }) => success({ statusCode: 200, data: { success: true, data: { ...product, skus: [], mainImageUrl: null } } })
  const page = await detail()
  page.openSkuDrawer()
  assert.equal(page.data.selectedEligible, false)
  assert.equal(page.data.selectedAvailable, 0)
  page.confirmAddToCart()
  assert.match(page.data.drawerError, /选择规格/)
  assert.deepEqual(cart.read(), [])
})

test('stock recovery cannot adjust sold-out or malformed stock and failed requote remains recoverable', async () => {
  setup()
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 2 }])
  const page = mount('../pages/checkout/checkout.js')
  page.items = storage.get('mall.checkoutSelection.v1')
  wx.request = ({ fail }) => fail()
  await page.loadStockChange()
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.stockPromptOpen, true)
  assert.equal(page.data.stockAdjustable, false)
  page.adjustStock()
  assert.equal(page.items[0].quantity, 2)
  wx.request = ({ success }) => success({ statusCode: 200, data: { success: true, data: { ...quote(), ready: false, lines: [{ ...quote().lines[0], status: 'OUT_OF_STOCK', availableQuantity: 0 }] } } })
  await page.loadStockChange()
  assert.equal(page.data.stockAdjustable, false)
  assert.equal(page.data.stockLines[0].availableQuantity, 0)
  page.dismissStockPrompt()
  assert.equal(page.data.stockPromptOpen, false)
})

test('checkout guest address login and form recovery use existing routes and invalid benefit input never requotes', async () => {
  const navigations = setup()
  storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 2 }])
  let requests = 0
  wx.request = ({ url, success }) => { requests++; success({ statusCode: 200, data: { success: true, data: url.endsWith('/addresses') ? [] : { ...quote(true), availablePoints: 10 } } }) }
  const page = mount('../pages/checkout/checkout.js')
  await page.onLoad()
  page.chooseAddress()
  assert.equal(navigations.at(-1), '/pages/login/login?returnTo=checkout')
  storage.set('mall.memberToken', 'member')
  page.chooseAddress()
  assert.equal(navigations.at(-1), '/pages/addresses/addresses?select=1')
  page.openOrders(); page.backToCart()
  assert.deepEqual(navigations.slice(-2), ['/pages/orders/list', '/pages/cart/cart'])
  page.onImageError(event({ id: 's1' }))
  assert.equal(page.data.failedImages.s1, true)
  page.onPointsInput({ detail: { value: 'bad' } }); page.applyPoints()
  assert.match(page.data.benefitError, /非负整数/)
  page.onPointsInput({ detail: { value: '11' } }); page.applyPoints()
  assert.match(page.data.benefitError, /超过/)
  assert.equal(requests, 1)
  await page.clearBenefits()
  assert.equal(page.couponId, null)
  assert.equal(page.pointsToUse, 0)
  await page.confirmChanges()
  assert.equal(storage.get('mall.checkoutSelection.v1')[0].seenPriceFen, 1200)
})
