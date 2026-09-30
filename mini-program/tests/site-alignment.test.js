const test = require('node:test')
const assert = require('node:assert/strict')
const storage = new Map()
function mount(file) {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
function setup() {
  storage.clear()
  const visits = []
  global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), navigateTo: ({ url }) => visits.push(url),
    navigateBack: () => visits.push('back'), showToast() {},
  }
  return visits
}
test('member address shortcut reaches existing address management without creating a new flow', () => {
  const visits = setup()
  storage.set('mall.memberToken', 'member')
  const page = mount('../pages/member/index.js')
  page.openAddresses()
  assert.deepEqual(visits, ['/pages/addresses/addresses'])
})
test('catalog image failure identifies only the affected product and clears on full reload', async () => {
  setup()
  wx.request = ({ success }) => success({ statusCode: 200, data: { success: true, data: { rows: [], total: 0 } } })
  const page = mount('../pages/index/index.js')
  page.listToken = 0
  page.onImageError({ currentTarget: { dataset: { id: 'p1' } } })
  assert.deepEqual(page.data.failedImages, { p1: true })
  await page.loadProducts(true)
  assert.deepEqual(page.data.failedImages, {})
})
test('address selection presents stored choice and ignores forged IDs without changing selection', async () => {
  const visits = setup()
  storage.set('mall.selectedAddressId', 'a1')
  wx.request = ({ success }) => success({ statusCode: 200, data: { success: true, data: [{ id: 'a1', recipientName: 'A' }] } })
  const page = mount('../pages/addresses/addresses.js')
  page.onLoad({ select: '1' })
  await page.onShow()
  assert.equal(page.data.selectedId, 'a1')
  page.select({ currentTarget: { dataset: { id: 'unknown' } } })
  assert.equal(storage.get('mall.selectedAddressId'), 'a1')
  assert.equal(visits.length, 0)
  page.select({ currentTarget: { dataset: { id: 'a1' } } })
  assert.deepEqual(visits, ['back'])
})
test('address auth failure exposes a login return compatible with the existing login page', () => {
  const visits = setup()
  const page = mount('../pages/addresses/addresses.js')
  page.login()
  assert.deepEqual(visits, ['/pages/login/login?returnTo=addresses'])
  const login = mount('../pages/login/login.js')
  login.onLoad({ returnTo: 'addresses' })
  assert.equal(login.returnTo, 'addresses')
  login.onLoad({ returnTo: 'https://outside.invalid/' })
  assert.equal(login.returnTo, '')
})
test('native region picker updates three address fields immutably and missing fields stay actionable', async () => {
  setup()
  const page = mount('../pages/addresses/edit.js')
  page.onLoad({})
  const before = page.data.form
  page.changeRegion({ detail: { value: ['上海市', '上海市', '徐汇区'] } })
  assert.notEqual(page.data.form, before)
  assert.equal(before.province, '')
  assert.equal(page.data.form.district, '徐汇区')
  page.changeRegion({ detail: { value: ['bad'] } })
  assert.equal(page.data.form.district, '徐汇区')
  await page.save()
  assert.equal(page.data.fieldErrors.recipientName, '请填写收货人姓名。')
  assert.equal(page.data.fieldErrors.province, undefined)
  page.change({ currentTarget: { dataset: { key: 'recipientName' } }, detail: { value: '张女士' } })
  assert.equal(page.data.fieldErrors.recipientName, '')
})
test('order shortcuts keep server fulfillment filters and points intersection without a client-side slice', async () => {
  const visits = setup()
  storage.set('mall.memberToken', 'member')
  const member = mount('../pages/member/index.js')
  member.openOrders({ currentTarget: { dataset: { fulfillment: 'WAITING_REDEMPTION' } } })
  assert.equal(visits.at(-1), '/pages/orders/list?fulfillment=WAITING_REDEMPTION')
  member.openOrders({ currentTarget: { dataset: { status: 'PENDING_PAYMENT' } } })
  assert.equal(visits.at(-1), '/pages/orders/list?status=PENDING_PAYMENT')
  member.openOrders({ currentTarget: { dataset: { fulfillment: 'unknown' } } })
  assert.equal(visits.at(-1), '/pages/orders/list')
  storage.set('mall.memberToken', 'member')
  const requests = []
  wx.request = ({ url, success }) => { requests.push(url); success({ statusCode: 200, data: { success: true, data: { items: [], total: 0, page: 1 } } }) }
  const orders = mount('../pages/orders/list.js')
  orders.onLoad({ orderKind: 'POINTS', fulfillment: 'IN_TRANSIT' })
  await orders.onShow()
  assert.match(requests.at(-1), /orderKind=POINTS/)
  assert.match(requests.at(-1), /fulfillment=IN_TRANSIT/)
  await orders.filter({ currentTarget: { dataset: { status: 'PENDING_PAYMENT' } } })
  assert.match(requests.at(-1), /status=PENDING_PAYMENT/)
  assert.doesNotMatch(requests.at(-1), /fulfillment=/)
  assert.match(requests.at(-1), /orderKind=POINTS/)
  orders.onLoad({ status: 'fake', fulfillment: 'fake' })
  assert.equal(orders.data.status, '')
  assert.equal(orders.data.fulfillment, '')
})
test('member count labels preserve real zero and show unknown for absent or malformed keys', () => {
  const { presentOverview } = require('../lib/member')
  const overview = { id: 'm', grade: { name: '普通会员' }, effectiveSpendFen: 0,
    points: { settledPoints: 0, frozenPoints: 0, availablePoints: 0, debtPoints: 0, expiredPendingPoints: 0, expiringPoints: 0 },
    rules: { grades: [], points: { earnUnitFen: 1, earnPoints: 1, deductPoints: 1, deductFen: 1, maxPercent: 1, validDays: 1, refundValidDays: 1 } } }
  assert.equal(presentOverview(overview).orderCountsLabels.waitingShipment, '--')
  const result = presentOverview({ ...overview, orderCounts: { pendingPayment: 0, waitingShipment: 5, inTransit: -1, waitingRedemption: '8' } })
  assert.deepEqual(result.orderCountsLabels, { pendingPayment: 0, waitingShipment: 5, inTransit: '--', waitingRedemption: '--', afterSale: '--' })
})
test('address edit loads current revision and preserves entered values after conflict, then creates a new address', async () => {
  const visits = setup()
  const saved = { id: 'a1', revision: 4, recipientName: '张女士', phone: '13800000000', province: '上海市', city: '上海市', district: '徐汇区', detail: '测试路1号', isDefault: true }
  const requests = []
  let conflict = true
  wx.request = ({ method, data, success }) => {
    requests.push({ method, data })
    success(method === 'GET' ? { statusCode: 200, data: { success: true, data: [saved] } } : conflict ?
      { statusCode: 409, data: { success: false, error: { message: '冲突' } } } : { statusCode: 200, data: { success: true, data: {} } })
  }
  const page = mount('../pages/addresses/edit.js')
  await page.onLoad({ id: 'a1' })
  page.change({ currentTarget: { dataset: { key: 'detail' } }, detail: { value: '已修改的门牌' } })
  page.change({ currentTarget: { dataset: { key: 'unexpected' } }, detail: { value: 'ignored' } })
  assert.equal(page.data.form.unexpected, undefined)
  page.toggleDefault({ detail: { value: false } })
  await page.save()
  assert.equal(requests.at(-1).data.expectedRevision, 4)
  assert.equal(page.data.form.detail, '已修改的门牌')
  assert.equal(page.data.form.isDefault, false)
  assert.equal(page.data.busy, false)
  assert.match(page.data.error, /已保留/)
  assert.equal(visits.length, 0)
  page.setData({ busy: true }); await page.save()
  assert.equal(requests.length, 2)
  page.setData({ busy: false }); conflict = false; page.addressId = ''
  await page.save()
  assert.equal(requests.at(-1).method, 'POST')
  assert.equal(requests.at(-1).data.expectedRevision, undefined)
  assert.equal(visits.at(-1), 'back')
  const missing = mount('../pages/addresses/edit.js')
  await missing.onLoad({ id: 'gone' })
  assert.equal(missing.data.state, 'error')
  assert.match(missing.data.error, /不存在/)
})
test('address delete requires confirmation and current revision, while refresh supports empty and auth states', async () => {
  const visits = setup()
  const page = mount('../pages/addresses/addresses.js')
  page.onLoad({})
  let requestCount = 0
  let response = [{ id: 'a1', revision: 7 }]
  wx.request = ({ method, data, success }) => {
    requestCount++
    if (method === 'DELETE') assert.deepEqual(data, { expectedRevision: 7 })
    success({ statusCode: 200, data: { success: true, data: response } })
  }
  await page.refresh()
  page.add(); page.edit({ currentTarget: { dataset: { id: 'a1' } } })
  assert.deepEqual(visits, ['/pages/addresses/edit', '/pages/addresses/edit?id=a1'])
  page.select({ currentTarget: { dataset: { id: 'a1' } } })
  assert.equal(visits.length, 2)
  page.remove({ currentTarget: { dataset: { id: 'missing' } } })
  let answer
  wx.showModal = ({ success }) => { answer = success }
  page.remove({ currentTarget: { dataset: { id: 'a1' } } })
  await answer({ confirm: false })
  assert.equal(requestCount, 1)
  response = []
  await answer({ confirm: true })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(page.data.state, 'empty')
  wx.request = ({ success }) => success({ statusCode: 401, data: { success: false, error: { message: '请登录' } } })
  await page.refresh()
  assert.equal(page.data.state, 'auth')
})
test('login keeps agreement gate, returns to address caller after success and reports failed authorization', async () => {
  const visits = setup()
  const page = mount('../pages/login/login.js')
  page.onLoad({ returnTo: 'addresses' })
  let authorizations = 0
  wx.login = ({ success }) => { authorizations++; success({ code: 'code' }) }
  wx.request = ({ success }) => success({ statusCode: 200, data: { success: true, data: { accessToken: 'member' } } })
  await page.login()
  assert.equal(authorizations, 0)
  page.toggleAgreement({ detail: { value: ['agree'] } })
  await page.login()
  assert.equal(visits.at(-1), 'back')
  assert.equal(storage.get('mall.memberToken'), 'member')
  page.setData({ busy: true }); await page.login()
  assert.equal(authorizations, 1)
  page.setData({ busy: false }); page.onLoad({})
  await page.login()
  assert.equal(visits.at(-1), '/pages/cart/cart')
  wx.login = ({ fail }) => fail()
  await page.login()
  assert.match(page.data.error, /微信登录失败/)
  assert.equal(page.data.busy, false)
  let feedback = ''
  wx.showToast = ({ title }) => { feedback = title }
  page.openPrivacy()
  assert.match(feedback, /暂不可查看/)
  wx.openPrivacyContract = ({ fail }) => fail()
  page.openPrivacy()
  assert.match(feedback, /暂不可查看/)
  page.back()
  assert.equal(visits.at(-1), 'back')
})
test('order progress uses server fulfillment states and hides misleading linear steps for refunds and aftersales', () => {
  const { present } = require('../lib/orders')
  const order = { orderId: 'o1', items: [], status: 'PAID', paymentMethod: 'WECHAT' }
  assert.equal(present({ ...order, fulfillmentStatus: 'IN_PROGRESS' }).progressSteps[2].label, '履约中')
  const waiting = present({ ...order, fulfillmentStatus: 'WAITING_SHIPMENT' }).progressSteps
  assert.equal(waiting[2].label, '待履约')
  assert.equal(waiting[3].reached, false)
  assert.equal(present({ ...order, fulfillmentStatus: 'COMPLETED' }).progressSteps[3].reached, true)
  for (const fulfillmentStatus of ['AFTER_SALE', 'REFUNDED', 'UNKNOWN']) {
    assert.deepEqual(present({ ...order, fulfillmentStatus }).progressSteps, [])
  }
  assert.deepEqual(present({ ...order, status: 'CLOSED' }).progressSteps, [])
  const unpaid = present({ ...order, status: 'PENDING_PAYMENT' }).progressSteps
  assert.equal(unpaid[0].reached, true)
  assert.equal(unpaid[1].reached, false)
})
