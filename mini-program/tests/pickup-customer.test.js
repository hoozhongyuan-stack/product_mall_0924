const test = require('node:test')
const assert = require('node:assert/strict')
const images = require('../lib/voucher-image')
const api = require('../lib/api')
const storage = new Map()
global.wx = { getStorageSync: (key) => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key), setClipboardData: (options) => options.success(),
  openLocation: () => {}, makePhoneCall: () => {} }
function mount() {
  let definition; global.Page = (value) => { definition = value }
  delete require.cache[require.resolve('../pages/orders/detail')]; require('../pages/orders/detail')
  storage.set('mall.memberToken', 'a')
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
const order = { orderId: 'o', orderNo: 'O1', status: 'PAID', fulfillmentStatus: 'WAITING_PICKUP', deliveryMode: 'PICKUP',
  storeId: 's', storeName: '深圳1号前置仓', store: { address: '北山道88号', openingHours: '09:00-23:00', contactPhone: '13900000000', latitude: '22.55', longitude: '114.20' },
  paymentMethod: 'OFFLINE', payableFen: 180000, goodsTotalFen: 180000, shippingFeeFen: 0,
  items: [{ orderLineId: 'l', name: '飞天茅台', fulfillmentKind: 'SHIP', quantity: 1, saleUnit: '瓶', specs: [{ name: '度数', value: '53度' }] }],
  storeDelivery: { mode: 'PICKUP', status: 'WAITING_PICKUP', pickupCode: 'O6WDCEOPCTD2QN67WHKUYECXFS', pickupQrDataUrl: 'data:image/png;base64,iVBORw0KGgo=' } }
async function withImages(work) {
  const original = { write: images.write, cleanup: images.cleanup, release: images.release }
  const released = []; images.cleanup = () => {}; images.release = (path) => released.push(path)
  images.write = async () => 'wxfile://pickup.png'
  try { await work(released) } finally { Object.assign(images, original) }
}
test('pickup QR uses server PNG and exposes full backup only for paid ready order', async () => withImages(async () => {
  const page = mount(); page.acceptOrder(order); await Promise.resolve()
  assert.equal(page.data.pickupQrPath, 'wxfile://pickup.png')
  assert.equal(page.data.pickupCode, order.storeDelivery.pickupCode)
  assert.equal(page.data.pickupStatusLabel, '待自提')
  assert.equal(page.data.items[0].specLabel, '度数：53度')
  assert.equal(page.data.order.storeDelivery.pickupQrDataUrl, undefined)
}))
test('unpaid preparing completed and aftersale states never display pickup credential', async () => withImages(async () => {
  for (const [status, local] of [['PENDING_PAYMENT', 'WAITING_PICKUP'], ['PAID', 'WAITING_PREPARATION'], ['PAID', 'COMPLETED'], ['PAID', 'AFTER_SALE']]) {
    const page = mount(); page.acceptOrder({ ...order, status, storeDelivery: { ...order.storeDelivery, status: local, completedAt: '2026-10-11T10:00:00Z' } })
    await Promise.resolve(); assert.equal(page.data.pickupCode, ''); assert.equal(page.data.pickupQrPath, '')
    assert.notEqual(page.data.pickupStatusLabel, local)
  }
}))
test('QR image failure preserves backup and handler releases unusable file', async () => withImages(async (released) => {
  const page = mount(); images.write = async () => { throw new Error('write failed') }; page.acceptOrder(order); await Promise.resolve(); await Promise.resolve()
  assert.equal(page.data.pickupQrFailed, true); assert.equal(page.data.pickupCode, order.storeDelivery.pickupCode)
  page.setData({ pickupQrPath: 'wxfile://bad.png' }); page.onPickupImageError(); assert.deepEqual(released, ['wxfile://bad.png'])
}))
test('hide clears pickup private state and rejects late QR file and late order', async () => withImages(async (released) => {
  const page = mount(); let resolve; images.write = () => new Promise((done) => { resolve = done })
  page.acceptOrder(order); page.onHide(); resolve('wxfile://late.png'); await Promise.resolve()
  assert.equal(page.data.pickupCode, ''); assert.equal(page.data.pickupQrPath, ''); assert.deepEqual(released, ['wxfile://late.png'])
  page.acceptOrder(order); assert.equal(page.data.pickupCode, '')
}))
test('identity change discards late PNG and clears previously shown credential', async () => withImages(async (released) => {
  const page = mount(); let resolve; images.write = () => new Promise((done) => { resolve = done }); page.acceptOrder(order)
  storage.set('mall.memberToken', 'b'); resolve('wxfile://late.png'); await Promise.resolve()
  assert.equal(page.data.pickupCode, ''); assert.equal(page.data.state, 'auth'); assert.deepEqual(released, ['wxfile://late.png'])
}))
test('hide invalidates order refresh before any private response appears', async () => withImages(async () => {
  const page = mount(); page.orderId = 'o'; let resolve; const get = api.get
  api.get = () => new Promise((done) => { resolve = done })
  try { const pending = page.refresh(); page.onHide(); resolve(order); await pending; assert.equal(page.data.order, null); assert.equal(page.data.pickupCode, '') }
  finally { api.get = get }
}))
test('copy pickup refuses expired identity and completed state', async () => withImages(async () => {
  const page = mount(); let copied = ''; wx.setClipboardData = ({ data, success }) => { copied = data; success() }
  page.acceptOrder(order); page.copyPickup(); assert.equal(copied, order.storeDelivery.pickupCode)
  copied = ''; storage.set('mall.memberToken', 'b'); page.copyPickup(); assert.equal(copied, '')
}))
test('store actions use order snapshot and fail clearly when contact or coordinates missing', async () => withImages(async () => {
  const page = mount(); page.acceptOrder(order)
  let navigation, phone; wx.openLocation = (value) => { navigation = value }; wx.makePhoneCall = (value) => { phone = value }
  page.navigatePickup(); page.contactPickup(); assert.equal(navigation.latitude, 22.55); assert.equal(phone.phoneNumber, '13900000000')
  navigation.fail(); assert.match(page.data.actionError, /导航暂不可用/); phone.fail(); assert.match(page.data.actionError, /未能拨打/)
  page.acceptOrder({ ...order, store: { address: '保留原地址' } }); page.navigatePickup(); assert.match(page.data.actionError, /暂未提供导航/)
  page.contactPickup(); assert.match(page.data.actionError, /暂无前置仓联系电话/)
}))
test('missing QR has fallback, unload drops late write, copy callback ignores hidden screen', async () => withImages(async (released) => {
  const page = mount(); page.acceptOrder({ ...order, storeDelivery: { ...order.storeDelivery, pickupQrDataUrl: undefined } })
  assert.equal(page.data.pickupQrFailed, true)
  let copy; wx.setClipboardData = (options) => { copy = options }; page.copyPickup(); page.onHide(); copy.success(); assert.equal(page.data.actionMessage, '')
  const next = mount(); let resolve; images.write = () => new Promise((done) => { resolve = done }); next.acceptOrder(order)
  next.onUnload(); resolve('wxfile://disposed.png'); await Promise.resolve(); assert.deepEqual(released, ['wxfile://disposed.png'])
}))
test('failed private write after identity changes clears fallback credential', async () => withImages(async () => {
  const page = mount(); let reject; images.write = () => new Promise((_, fail) => { reject = fail }); page.acceptOrder(order)
  storage.set('mall.memberToken', 'b'); reject(new Error('denied')); await Promise.resolve(); await Promise.resolve()
  assert.equal(page.data.state, 'auth'); assert.equal(page.data.pickupCode, '')
}))
test('mixed redemption progress does not hide valid physical pickup credential', async () => withImages(async () => {
  const page = mount(); page.acceptOrder({ ...order, fulfillmentStatus: 'IN_PROGRESS' }); await Promise.resolve()
  assert.equal(page.data.pickupCode, order.storeDelivery.pickupCode); assert.equal(page.data.pickupQrPath, 'wxfile://pickup.png')
}))
