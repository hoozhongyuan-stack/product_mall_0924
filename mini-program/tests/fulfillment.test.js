const test = require('node:test')
const assert = require('node:assert/strict')
const { present, statusLabel } = require('../lib/orders')
const catalog = require('../lib/catalog')

const storage = new Map()
global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
global.wx = {
  getStorageSync: (key) => storage.get(key),
  setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key),
  showModal: ({ success }) => success({ confirm: true }),
  setClipboardData: ({ success }) => success(),
  navigateTo: () => {}, stopPullDownRefresh: () => {},
}

function pageAt(path) {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(path)]
  require(path)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
function success(options, data) { options.success({ statusCode: 200, data: { success: true, data } }) }

const order = {
  orderId: 'order-fulfillment', orderNo: 'O20260926', status: 'PAID', fulfillmentStatus: 'IN_PROGRESS',
  paymentMethod: 'OFFLINE', payableFen: 2000, goodsTotalFen: 2000, shippingFeeFen: 0,
  couponDiscountFen: 0, pointsDiscountFen: 0, createdAt: '2026-09-26T10:00:00Z',
  paidAt: '2026-09-26T10:01:00Z', expiresAt: '2026-09-27T10:00:00Z',
  shipment: { carrierCode: 'SF', carrierName: '顺丰速运', trackingNo: 'SF123456789', shippedAt: '2026-09-26T11:00:00Z',
    autoConfirmAt: '2026-10-06T11:00:00Z', confirmedAt: null, trackingStatus: 'UNAVAILABLE' },
  items: [
    { orderLineId: 'line-ship', name: '实物商品', fulfillmentKind: 'SHIP', quantity: 1, unitPriceFen: 1000,
      fulfillment: { kind: 'SHIP', status: 'IN_TRANSIT' } },
    { orderLineId: 'line-redeem', name: '到店服务', fulfillmentKind: 'REDEEM', quantity: 2, unitPriceFen: 500,
      fulfillment: { kind: 'REDEEM', status: 'PARTIAL', voucherCode: 'VX-123456', validUntil: '2026-12-31',
        redeemedQuantity: 1, voidedQuantity: 0, remainingQuantity: 1,
        events: [{ eventId: 'redeem-1', quantity: 1, remainingQuantity: 1, redeemedAt: '2026-09-26T12:00:00Z', reversedAt: null }] } },
  ],
}

test('mixed order shows independent authoritative fulfillment statuses and quantities', () => {
  const display = present(order)
  assert.equal(display.statusLabel, '履约中')
  assert.equal(display.items[0].fulfillmentStatus, '运输中')
  assert.equal(display.items[1].fulfillmentStatus, '部分核销')
  assert.equal(display.items[1].redemption.remainingQuantity, 1)
  assert.equal(display.items[1].redemption.validUntilLabel, '2026-12-31')
  assert.equal(display.items[1].redemption.redeemedQuantity, 1)
  assert.equal(display.items[1].redemption.events[0].remainingQuantity, 1)
  const reversedLine = { ...order.items[1], fulfillment: { ...order.items[1].fulfillment,
    events: [{ eventId: 'reverse-1', kind: 'REVERSE', quantity: 1,
      remainingQuantity: 2, redeemedAt: '2026-09-26T14:00:00Z', reversedAt: null }] } }
  assert.equal(present({ ...order, items: [order.items[0], reversedLine] }).items[1].redemption.events[0].actionLabel, '撤销核销')
  assert.equal(display.canConfirmReceipt, true)
  assert.equal(display.shipment.trackingNo, 'SF123456789')
  assert.match(display.trackingMessage, /物流轨迹暂不可用/)
  assert.equal(statusLabel({ ...order, fulfillmentStatus: 'COMPLETED' }), '已完成')
})

test('tracking query shows events, empty result, retry and rejects an old member response', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member-a')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = order.orderId; page.acceptOrder(order)
  let pending
  global.wx.request = (options) => { pending = options }
  const first = page.loadTracking()
  assert.equal(page.data.trackingState, 'loading')
  success(pending, { status: 'IN_TRANSIT', events: [{ time: '2026-09-26 12:00:00', description: '已揽收' }],
    retryAfter: '2026-09-26T12:15:00+08:00' })
  await first
  assert.equal(page.data.trackingEvents[0].description, '已揽收')
  assert.match(page.data.trackingMessage, /运输中/)
  const empty = page.loadTracking()
  success(pending, { status: 'NO_EVENTS', events: [], retryAfter: null })
  await empty
  assert.match(page.data.trackingMessage, /暂无物流轨迹/)
  const failed = page.loadTracking()
  pending.fail()
  await failed
  assert.equal(page.data.trackingState, 'error')
  assert.match(page.data.trackingMessage, /物流轨迹暂不可用/)
  const stale = page.loadTracking()
  storage.set('mall.memberToken', 'member-b')
  success(pending, { status: 'IN_TRANSIT', events: [{ time: '2026-09-26 12:00:00', description: '旧会员轨迹' }] })
  await stale
  assert.deepEqual(page.data.trackingEvents, [])
  page.onUnload()
})

test('voucher QR is paid-member data only; failed image keeps selectable code available', () => {
  storage.clear(); storage.set('mall.memberToken', 'member-a')
  const removed = []; let write
  global.wx.env = { USER_DATA_PATH: 'wxfile://usr' }
  global.wx.getFileSystemManager = () => ({
    writeFile(options) { write = options },
    unlink(options) { removed.push(options.filePath); options.success() },
  })
  const qrOrder = { ...order, items: [order.items[0], { ...order.items[1], fulfillment: {
    ...order.items[1].fulfillment, voucherQrDataUrl: 'data:image/png;base64,iVBORw0KGgo=' } }] }
  assert.equal(present(qrOrder).items[1].redemption.voucherQrAvailable, true)
  assert.equal(present(qrOrder).order.items[1].fulfillment.voucherQrDataUrl, undefined)
  assert.equal(present({ ...qrOrder, status: 'PENDING_PAYMENT' }).items[1].redemption, null)
  const page = pageAt('../pages/orders/detail.js'); page.orderId = order.orderId; page.acceptOrder(qrOrder)
  assert.equal(page.data.voucherQrLoading['line-redeem'], true)
  assert.equal(write.encoding, 'base64')
  write.success()
  return Promise.resolve().then(() => {
    assert.match(page.data.voucherQrPaths['line-redeem'], /^wxfile:\/\/usr\/mall-voucher-/)
    page.onVoucherImageError({ currentTarget: { dataset: { lineId: 'line-redeem' } } })
    assert.equal(page.data.failedVoucherImages['line-redeem'], true)
    assert.equal(page.data.items[1].redemption.voucherCode, 'VX-123456')
    assert.equal(removed.length, 1)
    let copied = ''; let copyCount = 0
    global.wx.setClipboardData = ({ data, success }) => { copied = data; copyCount += 1; success() }
    page.copyVoucher({ currentTarget: { dataset: { code: 'VX-123456' } } })
    assert.equal(copied, 'VX-123456')
    page.acceptOrder({ ...qrOrder, status: 'PENDING_PAYMENT' })
    page.copyVoucher({ currentTarget: { dataset: { code: 'VX-123456' } } })
    assert.equal(copyCount, 1)
    page.onUnload()
    delete global.wx.env; delete global.wx.getFileSystemManager
  })
})

test('QR written after member switch is deleted without displaying the old voucher', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member-a')
  const removed = []; let write
  global.wx.env = { USER_DATA_PATH: 'wxfile://usr' }
  global.wx.getFileSystemManager = () => ({ writeFile(options) { write = options },
    unlink(options) { removed.push(options.filePath); options.success() } })
  const qrOrder = { ...order, items: [order.items[0], { ...order.items[1], fulfillment: {
    ...order.items[1].fulfillment, voucherQrDataUrl: 'data:image/png;base64,iVBORw0KGgo=' } }] }
  const page = pageAt('../pages/orders/detail.js'); page.orderId = order.orderId; page.acceptOrder(qrOrder)
  storage.set('mall.memberToken', 'member-b')
  write.success(); await Promise.resolve()
  assert.deepEqual(page.data.voucherQrPaths, {})
  assert.equal(removed.length, 1)
  page.onUnload()
  delete global.wx.env; delete global.wx.getFileSystemManager
})

test('private voucher is only shown for a paid order and closed order does not offer receipt confirmation', () => {
  const pending = present({ ...order, status: 'PENDING_PAYMENT', fulfillmentStatus: 'WAITING_PAYMENT' })
  assert.equal(pending.items[1].redemption, null)
  assert.equal(pending.canConfirmReceipt, false)
  const closed = present({ ...order, status: 'CLOSED', fulfillmentStatus: 'CLOSED' })
  assert.equal(closed.canConfirmReceipt, false)
})

test('receipt confirmation requires user confirmation, uses server order, and blocks duplicate taps', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js')
  page.orderId = order.orderId
  page.acceptOrder(order)
  let requested = 0
  let complete
  global.wx.request = (options) => {
    requested += 1
    assert.match(options.url, /\/confirm-receipt$/)
    assert.deepEqual(options.data, {})
    complete = () => success(options, { ...order, fulfillmentStatus: 'IN_PROGRESS', shipment: { ...order.shipment, confirmedAt: '2026-09-26T13:00:00Z' },
      items: order.items.map((line) => line.fulfillmentKind === 'SHIP' ? { ...line, fulfillment: { kind: 'SHIP', status: 'COMPLETED' } } : line) })
  }
  const first = page.confirmReceipt()
  await Promise.resolve()
  await page.confirmReceipt()
  assert.equal(requested, 1)
  complete(); await first
  assert.equal(page.data.canConfirmReceipt, false)
  assert.equal(page.data.items[0].fulfillmentStatus, '已收货')
  assert.equal(page.data.statusLabel, '履约中')
})

test('receipt confirmation failure keeps shipped status and permits refresh', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = order.orderId; page.acceptOrder(order)
  global.wx.request = (options) => options.fail()
  await page.confirmReceipt()
  assert.equal(page.data.canConfirmReceipt, true)
  assert.match(page.data.actionError, /刷新/)
  assert.equal(page.data.items[0].fulfillmentStatus, '运输中')
})

test('logout while receipt response is in flight cannot restore a private order', async () => {
  storage.clear(); storage.set('mall.memberToken', 'member-a')
  const page = pageAt('../pages/orders/detail.js'); page.orderId = order.orderId; page.acceptOrder(order)
  let request
  global.wx.request = (options) => { request = options }
  const confirming = page.confirmReceipt()
  await Promise.resolve()
  storage.set('mall.memberToken', 'member-b')
  success(request, order)
  await confirming
  assert.equal(page.data.state, 'auth')
  assert.equal(page.data.order, null)
  assert.deepEqual(page.data.items, [])
  assert.equal(page.data.shipment, null)
})

test('redeem validity date comes from product and quote service, without client calculation', async () => {
  const product = { productId: 'p1', name: '到店服务', fulfillmentKind: 'REDEEM', redeemValidUntil: '2026-12-31',
    skus: [{ skuId: 's1', listPriceFen: 1000, availableQuantity: 1, cartEligible: true }] }
  assert.equal(catalog.productDetail(product, 'http://127.0.0.1:8000').redeemValidUntil, '2026-12-31')
  assert.equal(catalog.productDetail({ ...product, fulfillmentKind: 'SHIP' }, 'http://127.0.0.1:8000').redeemValidUntil, '')
  storage.clear(); storage.set('mall.checkoutSelection.v1', [{ skuId: 's1', quantity: 1 }])
  global.wx.request = (options) => success(options, {
    lines: [{ skuId: 's1', name: '到店服务', fulfillmentKind: 'REDEEM', redeemValidUntil: '2026-12-31',
      unitPriceFen: 1000, lineAmountFen: 1000, quantity: 1 }],
    goodsTotalFen: 1000, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0,
    payableFen: 1000, availablePaymentMethods: [], ready: true,
  })
  const page = pageAt('../pages/checkout/checkout.js')
  await page.onLoad()
  assert.equal(page.data.lines[0].redeemValidUntil, '2026-12-31')
})
