const test = require('node:test')
const assert = require('node:assert/strict')
const dashboard = require('../lib/store-dashboard')
const api = require('../lib/api')
function mount() {
  let definition
  global.Page = value => { definition = value }
  delete require.cache[require.resolve('../pages/store-center/order')]
  require('../pages/store-center/order')
  return { ...definition, data: structuredClone(definition.data), setData(value) { this.data = { ...this.data, ...value } } }
}
function setup(order) {
  const storage = new Map([['mall.memberToken', 'member']])
  global.wx = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key), showModal: value => value.success({ confirm: true }) }
  const page = mount()
  page.identity = 'member'; page.storeId = 's'; page.orderId = 'o'; page.generation = 1
  page.present(order)
  return page
}
const physical = { orderId: 'o', revision: 1, status: 'PAID', canFulfill: true, storeId: 's', storeName: '深圳仓', deliveryMode: 'DELIVERY',
  fulfillmentStatus: 'WAITING_PREPARATION', items: [{ orderLineId: 'ship', fulfillmentKind: 'SHIP', quantity: 2 }],
  storeDelivery: { status: 'WAITING_PREPARATION' } }
test('warehouse workbench has delivery and redemption duties, without express shipment', () => {
  const view = dashboard.present({ stats: { waitingPreparation: 2, waitingPickup: 1, waitingDelivery: 3, inTransit: 4, waitingRedemption: 5, waitingShipment: 99 } }, ['orders'])
  assert.deepEqual(view.todos.map(row => row.label), ['待备货', '待配送', '配送中', '待自提', '待核销'])
  assert.deepEqual(view.todos.map(row => row.count), [2, 3, 4, 1, 5])
  assert.ok(!dashboard.ORDER_FILTERS.some(row => row.value === 'WAITING_SHIPMENT'))
})
test('express physical order cannot prepare, dispatch, complete or ship from warehouse', async () => {
  const page = setup({ ...physical, deliveryMode: 'EXPRESS', storeDelivery: null })
  assert.equal(page.data.canShip, false)
  let calls = 0; const old = api.post; api.post = async () => { calls++; return physical }
  await page.act({ currentTarget: { dataset: { action: 'SHIP' } } })
  assert.equal(calls, 0); api.post = old
})
test('warehouse delivery item list excludes virtual service items', () => {
  const page = setup({ ...physical, items: [...physical.items, { orderLineId: 'service', fulfillmentKind: 'REDEEM', quantity: 5 }] })
  assert.deepEqual(page.data.items.map(row => row.orderLineId), ['ship'])
})
test('delivery failure requires a reason and exception supports retry', async () => {
  const page = setup({ ...physical, storeDelivery: { status: 'IN_TRANSIT' } })
  assert.equal(page.data.canFailDelivery, true)
  const old = api.post; let calls = 0
  api.post = async () => { calls++; return { ...physical, storeDelivery: { status: 'EXCEPTION', failureReason: '顾客不在' } } }
  await page.act({ currentTarget: { dataset: { action: 'FAIL_DELIVERY' } } })
  assert.equal(calls, 0); assert.match(page.data.error, /原因/)
  page.input({ currentTarget: { dataset: { key: 'note' } }, detail: { value: '顾客不在' } })
  await page.act({ currentTarget: { dataset: { action: 'FAIL_DELIVERY' } } })
  assert.equal(calls, 1); assert.equal(page.data.canRetryDelivery, true); assert.equal(page.data.canComplete, false)
  api.post = old
})
test('warehouse action cannot restore private order after authentication expires', async () => {
  const page = setup(physical), old = api.post
  api.post = async () => { wx.getStorageSync = () => ''; const error = new Error('登录已过期'); error.statusCode = 401; throw error }
  await page.act({ currentTarget: { dataset: { action: 'PREPARE' } } })
  assert.equal(page.data.order, null); assert.equal(page.data.state, 'auth'); assert.equal(page.pending, null)
  api.post = old
})
test('native warehouse UI separates physical handover from virtual redemption and removes express dispatch', () => {
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '../pages/store-center/order.wxml'), 'utf8')
  assert.match(source, /确认提货/)
  assert.match(source, /核销权益/)
  assert.doesNotMatch(source, /提交快递发货|自提核销|确认核销并完成自提/)
})
test('customer detail explains owner and allowed redemption venues alongside separate credentials', () => {
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '../pages/orders/detail.wxml'), 'utf8')
  assert.match(source, /履约方/)
  assert.match(source, /核销范围/)
  assert.match(source, /权益核销凭证/)
  assert.match(source, /自提凭证/)
})
const orders = require('../lib/orders')
const virtual = { orderLineId: 'service', name: '服务', fulfillmentKind: 'REDEEM', quantity: 5,
  fulfillment: { kind: 'REDEEM', status: 'PARTIAL', redeemScope: 'BOTH', redeemStoreIds: ['s'], redeemStores: [{ storeId: 's', name: '深圳仓' }],
    canRedeemStore: true, voucherRevision: 4, availableQuantity: 2, heldQuantity: 1, remainingQuantity: 3, redeemedQuantity: 2, voidedQuantity: 0,
    validUntil: '2026-12-31', events: [{ eventId: 'event', quantity: 2, actorKind: 'STORE', storeName: '深圳仓', redeemedAt: '2026-10-12T01:00:00Z' }] } }
test('customer uses transaction redemption scope, records actual actor and hides exhausted or expired credentials', () => {
  const order = { ...physical, fulfillmentOwner: 'STORE', fulfillmentStoreName: '深圳仓', inventoryWarehouse: { warehouseId: 'w', name: '深圳仓库存' }, items: [virtual] }
  const view = orders.present(order)
  assert.equal(view.fulfillmentOwnerLabel, '深圳仓')
  assert.match(view.items[0].redemption.scopeLabel, /平台.*深圳仓/)
  assert.match(view.items[0].redemption.events[0].actorLabel, /深圳仓/)
  assert.equal(view.items[0].redemption.canUse, true)
  assert.equal(orders.present({ ...order, items: [{ ...virtual, fulfillment: { ...virtual.fulfillment, status: 'EXPIRED' } }] }).items[0].redemption.canUse, false)
})
test('warehouse redemption validates quantity and uses shared voucher revision with exact retry key', async () => {
  const page = setup({ ...physical, deliveryMode: 'EXPRESS', storeDelivery: null, items: [virtual] })
  assert.equal(page.data.redeemItems.length, 1)
  const input = (key, value) => page.redemptionInput({ currentTarget: { dataset: { lineId: 'service', key } }, detail: { value } })
  input('voucherCode', 'CUSTOMER-CODE'); input('quantity', '3')
  const old = api.post, attempts = []; api.post = async (path, body, headers) => { attempts.push({ path, body: structuredClone(body), headers }); throw new Error('网络失败') }
  const event = { currentTarget: { dataset: { lineId: 'service' } } }
  await page.redeem(event); assert.equal(attempts.length, 0); assert.match(page.data.error, /可核销/)
  input('quantity', '2'); await page.redeem(event); assert.equal(attempts.length, 1)
  assert.match(attempts[0].path, /\/orders\/o\/redemptions$/)
  assert.equal(attempts[0].body.expectedRevision, 4); assert.equal(attempts[0].body.quantity, 2)
  assert.equal(page.data.uncertain, true)
  const oldGet = api.get; api.get = async () => ({ ...physical, items: [virtual] })
  await page.send(); assert.deepEqual(attempts[1], attempts[0]); api.post = old; api.get = oldGet
})
test('warehouse virtual authorization denies platform-only and another warehouse scopes', () => {
  for (const fulfillment of [{ ...virtual.fulfillment, redeemScope: 'PLATFORM' }, { ...virtual.fulfillment, redeemStoreIds: ['another'] }]) {
    const page = setup({ ...physical, items: [{ ...virtual, fulfillment }] })
    assert.equal(page.data.redeemItems.length, 0)
  }
})
test('warehouse redemption clears form after authoritative success and never exposes server credential fields', async () => {
  const page = setup({ ...physical, items: [{ ...virtual, fulfillment: { ...virtual.fulfillment, voucherCode: 'PRIVATE', voucherQrDataUrl: 'data:image/png;base64,AA==' } }] })
  assert.equal(page.data.redeemItems[0].fulfillment.voucherCode, undefined)
  page.data.redemptionDrafts = { service: { voucherCode: 'CUSTOMER', quantity: '1' } }
  const old = api.post; let calls = 0
  api.post = async () => { calls++; return { ...physical, items: [{ ...virtual, fulfillment: { ...virtual.fulfillment, redeemedQuantity: 3, remainingQuantity: 2, availableQuantity: 1, voucherRevision: 5 } }] } }
  await page.redeem({ currentTarget: { dataset: { lineId: 'service' } } })
  assert.equal(calls, 1); assert.equal(page.pending, null); assert.deepEqual(page.data.redemptionDrafts, {})
  assert.equal(page.data.redeemItems[0].fulfillment.availableQuantity, 1)
  assert.match(page.data.actionMessage, /核销/); api.post = old
})
test('denied warehouse read clears order and reload recovers without leaving stale private data', async () => {
  const page = setup(physical), old = api.get
  api.get = async () => { const error = new Error('当前账号无权限'); error.statusCode = 403; throw error }
  await page.load(); assert.equal(page.data.order, null); assert.equal(page.data.state, 'denied')
  api.get = async () => physical
  await page.load(); assert.equal(page.data.state, 'ready'); assert.equal(page.data.error, ''); api.get = old
})
test('server physical capability freezes only the blocked operation and foreign warehouses cannot act', () => {
  const page = setup({ ...physical, canFulfill: false })
  assert.equal(page.data.canPrepare, false)
  page.present({ ...physical, fulfillmentStatus: 'AFTER_SALE', canFulfill: true })
  assert.equal(page.data.canPrepare, true)
  page.present({ ...physical, storeId: 'foreign', canFulfill: true })
  assert.equal(page.data.canPrepare, false)
})
test('cancelled redemption confirmation sends nothing and duplicate confirmation opens once', async () => {
  const page = setup({ ...physical, items: [virtual] }), old = api.post
  page.data.redemptionDrafts = { service: { voucherCode: 'CODE', quantity: '1' } }
  api.post = async () => { throw new Error('must not send') }
  wx.showModal = options => options.success({ confirm: false })
  const event = { currentTarget: { dataset: { lineId: 'service' } } }
  await page.redeem(event); assert.equal(page.pending, undefined); assert.equal(page.data.busy, false)
  let complete, dialogs = 0; wx.showModal = options => { dialogs++; complete = options.success }
  const first = page.redeem(event); await page.redeem(event); assert.equal(dialogs, 1)
  complete({ confirm: false }); await first; api.post = old
})
test('redemption outcome after hide retains original request and stale account cannot submit it', async () => {
  const page = setup({ ...physical, items: [virtual] }), old = api.post, oldGet = api.get
  page.data.redemptionDrafts = { service: { voucherCode: 'CODE', quantity: '1' } }
  let complete; api.post = () => new Promise(resolve => { complete = resolve })
  const first = page.redeem({ currentTarget: { dataset: { lineId: 'service' } } }); await Promise.resolve()
  const pending = structuredClone(page.pending); page.onHide(); complete({ ...physical, items: [virtual] }); await first
  assert.equal(page.data.order, null); assert.deepEqual(page.pending, pending)
  api.get = async () => ({ ...physical, items: [virtual] }); await page.onShow()
  assert.equal(page.data.uncertain, true); assert.equal(page.data.error.includes('待确认'), true)
  wx.getStorageSync = () => 'another'; let calls = 0; api.post = async () => { calls++ }
  await page.send(); assert.equal(calls, 0); api.post = old; api.get = oldGet
})
test('warehouse can read frozen expired or completed service progress without offering a write', async () => {
  const old = api.post; let calls = 0; api.post = async () => { calls++ }
  for (const status of ['EXPIRED', 'COMPLETED', 'WAITING_REDEMPTION']) {
    const page = setup({ ...physical, items: [{ ...virtual, fulfillment: { ...virtual.fulfillment, status, availableQuantity: 0, canRedeemStore: false } }] })
    assert.equal(page.data.redeemItems.length, 1); assert.equal(page.data.redeemItems[0].canRedeem, false)
    await page.redeem({ currentTarget: { dataset: { lineId: 'service' } } }); assert.equal(calls, 0)
  }
  api.post = old
})
test('redemption rejects malformed input and unavailable voucher revision before any write', async () => {
  const page = setup({ ...physical, items: [virtual] }), old = api.post
  let writes = 0; api.post = async () => { writes++ }
  const event = { currentTarget: { dataset: { lineId: 'service' } } }
  for (const draft of [{ voucherCode: '', quantity: '1' }, { voucherCode: 'x'.repeat(201), quantity: '1' }, { voucherCode: 'CODE', quantity: '1.5' }, { voucherCode: 'CODE', quantity: '1', note: 'x'.repeat(501) }]) {
    page.data.redemptionDrafts = { service: draft }; await page.redeem(event); assert.equal(writes, 0); assert.ok(page.data.error)
  }
  page.present({ ...physical, items: [{ ...virtual, fulfillment: { ...virtual.fulfillment, voucherRevision: null } }] })
  assert.equal(page.data.redeemItems[0].canRedeem, false)
  page.redemptionInput({ currentTarget: { dataset: { lineId: 'service', key: 'voucherCode' } }, detail: { value: 'CODE' } })
  assert.deepEqual(page.data.redemptionDrafts, {})
  api.post = old
})
test('redemption definitive conflict releases pending while network retry keeps its original intent', async () => {
  const page = setup({ ...physical, items: [virtual] }), old = api.post
  page.data.redemptionDrafts = { service: { voucherCode: 'CODE', quantity: '1' } }
  api.post = async () => { const error = new Error('次数已变化，请重新加载'); error.statusCode = 409; throw error }
  await page.redeem({ currentTarget: { dataset: { lineId: 'service' } } })
  assert.equal(page.pending, null); assert.equal(page.data.uncertain, false); assert.match(page.data.error, /重新加载/)
  api.post = old
})
test('customer local delivery progress shows actual stage and does not invent completion during an exception', () => {
  const delivery = orders.present({ ...physical, fulfillmentStatus: 'DELIVERING', storeDelivery: { status: 'IN_TRANSIT' } })
  assert.deepEqual(delivery.progressSteps.map(step => step.label), ['已付款', '备货', '待配送', '配送中', '已送达'])
  assert.equal(delivery.progressSteps[3].reached, true); assert.equal(delivery.progressSteps[4].reached, false)
  const failed = orders.present({ ...physical, fulfillmentStatus: 'EXCEPTION', storeDelivery: { status: 'EXCEPTION', failureReason: '顾客不在' } })
  assert.equal(failed.progressSteps[4].reached, false); assert.match(failed.statusCopy, /顾客不在/)
  const pickup = orders.present({ ...physical, deliveryMode: 'PICKUP', storeDelivery: { status: 'WAITING_PICKUP' } })
  assert.deepEqual(pickup.progressSteps.map(step => step.label), ['已付款', '备货', '待自提', '已提货'])
})
test('missing authoritative warehouse ownership or physical capability cannot enable write controls', () => {
  for (const facts of [{ ...physical, canFulfill: undefined }, { ...physical, canFulfill: true, storeId: undefined, fulfillmentStoreId: undefined }]) {
    const page = setup(facts); assert.equal(page.data.canPrepare, false)
  }
})
test('backgrounding during redemption confirmation does not leave the resumed page busy', async () => {
  const page = setup({ ...physical, items: [virtual] }), oldGet = api.get
  page.data.redemptionDrafts = { service: { voucherCode: 'CODE', quantity: '1' } }
  let confirm; wx.showModal = options => { confirm = options.success }
  const action = page.redeem({ currentTarget: { dataset: { lineId: 'service' } } }); assert.equal(page.data.busy, true)
  page.onHide(); confirm({ confirm: true }); await action
  api.get = async () => ({ ...physical, items: [virtual] }); await page.onShow()
  assert.equal(page.data.busy, false); assert.equal(page.pending, undefined); api.get = oldGet
})
test('delivery completion asks for evidence before posting and new attempts clear previous success', async () => {
  const page = setup({ ...physical, storeDelivery: { status: 'IN_TRANSIT' } }), old = api.post
  let calls = 0; api.post = async () => { calls++; throw new Error('网络失败') }
  page.data.actionMessage = '前次操作成功'
  await page.act({ currentTarget: { dataset: { action: 'COMPLETE' } } })
  assert.equal(calls, 0); assert.match(page.data.error, /5/)
  page.data.note = '已当面交付给顾客'; await page.act({ currentTarget: { dataset: { action: 'COMPLETE' } } })
  assert.equal(calls, 1); assert.equal(page.data.actionMessage, ''); api.post = old
})

test('unknown redemption survives reopen without plaintext credentials and reconciles server request key', async () => {
  const page = setup({ ...physical, items: [virtual] }), oldPost = api.post, oldGet = api.get
  page.data.redemptionDrafts = { service: { voucherCode: 'PRIVATE-CREDENTIAL', quantity: '1' } }
  api.post = async () => { throw new Error('timeout') }
  await page.redeem({ currentTarget: { dataset: { lineId: 'service' } } })
  const key = page.pending.key
  const durable = require('../lib/store-fulfillment-pending')
  const saved = wx.getStorageSync(durable.storageKey('member', 's', 'o'))
  assert.equal(JSON.stringify(saved).includes('PRIVATE-CREDENTIAL'), false)
  assert.equal(saved.key, key)
  assert.equal(durable.read('other', 's', 'o'), null)
  assert.equal(durable.read('member', 'other', 'o'), null)
  page.onUnload(); const reopened = mount(); api.get = async () => ({ ...physical, items: [virtual] })
  await reopened.onLoad({ storeId: 's', id: 'o' })
  assert.equal(reopened.pending.key, key); assert.equal(reopened.data.needsRetryCredential, true)
  let calls = 0; api.post = async (_, body, headers) => { calls++; assert.equal(body.voucherCode, 'PRIVATE-CREDENTIAL'); assert.equal(headers['Idempotency-Key'], key); throw new Error('timeout') }
  await reopened.send(); assert.equal(calls, 0)
  reopened.retryCredentialInput({ detail: { value: 'WRONG' } }); await reopened.send(); assert.equal(calls, 0)
  reopened.retryCredentialInput({ detail: { value: 'PRIVATE-CREDENTIAL' } }); await reopened.send(); assert.equal(calls, 1)
  api.get = async () => ({ ...physical, items: [{ ...virtual, fulfillment: { ...virtual.fulfillment, events: [{ requestKey: key }] } }] })
  await reopened.load(); assert.equal(reopened.pending, null); assert.equal(reopened.data.uncertain, false)
  assert.equal(durable.read('member', 's', 'o'), null)
  api.post = oldPost; api.get = oldGet
})
test('storage failure prevents fulfillment from sending an unrecoverable operation', async () => {
  const page = setup(physical), oldPost = api.post; let calls = 0
  wx.setStorageSync = () => { throw new Error('quota') }; api.post = async () => { calls++; return physical }
  await page.act({ currentTarget: { dataset: { action: 'PREPARE' } } })
  assert.equal(calls, 0); assert.match(page.data.error, /保存/); api.post = oldPost
})
test('durable physical retry reconciles the authoritative local delivery event collection', () => {
  const durable = require('../lib/store-fulfillment-pending')
  assert.equal(durable.confirmed({ localDeliveryEvents: [{ requestKey: 'request' }] }, 'request'), true)
  assert.equal(durable.confirmed({ localDeliveryEvents: [{ requestKey: 'other' }] }, 'request'), false)
})
test('silent storage loss also blocks an unrecoverable fulfillment request', async () => {
  const page = setup(physical), oldPost = api.post; let calls = 0
  wx.setStorageSync = () => {}; api.post = async () => { calls++; return physical }
  await page.act({ currentTarget: { dataset: { action: 'PREPARE' } } })
  assert.equal(calls, 0); assert.match(page.data.error, /保存/); api.post = oldPost
})
test('same-page unknown retry reads authoritative request events before replaying', async () => {
  const page = setup(physical), oldPost = api.post, oldGet = api.get; let calls = 0, reads = 0
  api.post = async () => { calls++; throw new Error('lost response') }
  await page.act({ currentTarget: { dataset: { action: 'PREPARE' } } })
  api.get = async () => { reads++; return { ...physical, localDeliveryEvents: [{ requestKey: page.pending.key }], storeDelivery: { status: 'WAITING_DELIVERY' } } }
  await page.send(); assert.equal(reads, 1); assert.equal(calls, 1)
  assert.equal(page.pending, null); assert.equal(page.data.uncertain, false); assert.equal(page.data.canDispatch, true)
  api.post = oldPost; api.get = oldGet
})
test('partial local handover never marks the final whole-order timeline step completed', () => {
  for (const deliveryMode of ['DELIVERY', 'PICKUP']) {
    const view = orders.present({ ...physical, deliveryMode, fulfillmentStatus: 'AFTER_SALE', storeDelivery: { status: 'COMPLETED' },
      items: [{ ...physical.items[0], fulfillment: { kind: 'SHIP', status: 'AFTER_SALE', deliveredQuantity: 1, remainingPhysicalQuantity: 1 } }] })
    const last = view.progressSteps.at(-1)
    assert.equal(last.label, deliveryMode === 'DELIVERY' ? '部分送达' : '部分提货')
    assert.equal(last.reached, false); assert.equal(last.current, true)
  }
})
