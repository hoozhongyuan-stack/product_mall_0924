const test = require('node:test')
const assert = require('node:assert/strict')
const location = require('../lib/address-location')
const poi = { poiName: '悦千山雅园', locationAddress: '北山道18号', province: '广东省', city: '深圳市', district: '盐田区', latitude: 22.61, longitude: 114.25 }
function mount(file) { let d; global.Page = v => { d = v }; delete require.cache[require.resolve(file)]; require(file); return { ...d, data: structuredClone(d.data), setData(v) { this.data = { ...this.data, ...v } } } }
function setup() { global.getApp = () => ({ globalData: { apiBaseUrl: 'https://example.test' } }); global.wx = { getStorageSync: () => 'token', navigateBack() {}, navigateTo() {} } }
test('location normalization rejects incomplete regions and nonnumeric coordinates', () => {
  assert.deepEqual(location.normalize(poi), poi)
  assert.equal(location.normalize({ ...poi, district: '' }), null)
  assert.equal(location.normalize({ ...poi, latitude: null }), null)
  assert.equal(location.normalize({ ...poi, longitude: 'bad' }), null)
})
test('delivery editor keeps coordinates when door details change and never guesses old door details', async () => {
  setup(); wx.request = ({ success }) => success({ statusCode: 200, data: { success: true, data: [{ id: 'old', recipientName: '张女士', phone: '13800000000', province: '广东省', city: '深圳市', district: '盐田区', detail: '原地址3栋1201', latitude: 22, longitude: 114, revision: 2 }] } })
  const p = mount('../pages/addresses/edit'); await p.onLoad({ id: 'old', deliveryMode: 'DELIVERY', storeId: 's1' }); assert.equal(p.data.form.doorDetail, ''); assert.equal(p.data.legacyDetail, '原地址3栋1201')
  p.acceptLocation(poi); p.change({ currentTarget: { dataset: { key: 'doorDetail' } }, detail: { value: '3栋1201' } }); assert.equal(p.data.form.latitude, 22.61); assert.equal(p.data.form.poiName, '悦千山雅园')
})
test('delivery editor blocks save without confirmed location and door', async () => {
  setup(); const p = mount('../pages/addresses/edit'); p.onLoad({ deliveryMode: 'DELIVERY' }); await p.save(); assert.match(p.data.fieldErrors.poiName, /收货位置/); assert.match(p.data.fieldErrors.doorDetail, /门牌/)
})
test('location search discards stale responses and requires explicit candidate confirmation', async () => {
  setup(); const pending = []; wx.request = ({ success }) => pending.push(success)
  const p = mount('../pages/addresses/location'); p.onLoad({ city: '深圳市' }); p.changeQuery({ detail: { value: '雅园' } }); const first = p.search(); p.changeQuery({ detail: { value: '大厦' } }); const second = p.search()
  pending[1]({ statusCode: 200, data: { success: true, data: { items: [poi] } } }); await second; pending[0]({ statusCode: 200, data: { success: true, data: { items: [] } } }); await first; assert.equal(p.data.items.length, 1); assert.equal(p.data.candidate, null)
  p.select({ currentTarget: { dataset: { index: 0 } } }); assert.equal(p.data.candidate.poiName, poi.poiName)
  let emitted; p.getOpenerEventChannel = () => ({ emit: (name, v) => { emitted = { name, v } } }); p.confirm(); assert.equal(emitted.name, 'locationSelected'); assert.deepEqual(emitted.v, poi)
})
test('hidden and switched-member location responses cannot populate candidates', async () => {
  setup(); let identity = 'one'; wx.getStorageSync = () => identity; let reply
  wx.request = ({ success }) => { reply = success }
  const p = mount('../pages/addresses/location'); p.onLoad({ city: '深圳市' }); p.changeQuery({ detail: { value: '小区' } }); const work = p.search(); p.onHide()
  reply({ statusCode: 200, data: { success: true, data: { items: [poi] } } }); await work; assert.equal(p.data.items.length, 0)
  p.onShow(); const switched = p.search(); identity = 'two'; reply({ statusCode: 200, data: { success: true, data: { items: [poi] } } }); await switched; assert.equal(p.data.items.length, 0)
})
test('native map result after unload is ignored and valid selected map name is preserved', async () => {
  setup(); let native
  wx.chooseLocation = v => { native = v }; wx.request = ({ success }) => success({ statusCode: 200, data: { success: true, data: poi } })
  const disposed = mount('../pages/addresses/location'); disposed.onLoad({}); disposed.nativeLocation(); disposed.onUnload(); native.success({ name: '错误回调', latitude: 22, longitude: 114 }); assert.equal(disposed.data.candidate, null)
  const p = mount('../pages/addresses/location'); p.onLoad({}); p.nativeLocation(); p.onHide(); native.success({ name: '明确选中大厦', address: '北山道20号', latitude: 22.61, longitude: 114.25 }); await new Promise(resolve => setImmediate(resolve)); assert.equal(p.data.candidate.poiName, '明确选中大厦'); assert.equal(p.data.candidate.locationAddress, '北山道20号')
})
test('express legacy save omits blank structured fields', async () => {
  setup(); let sent
  wx.request = ({ data, success }) => { sent = data; success({ statusCode: 200, data: { success: true, data: {} } }) }
  const p = mount('../pages/addresses/edit'); p.onLoad({ deliveryMode: 'EXPRESS' }); p.setData({ form: { ...p.data.form, recipientName: '张女士', phone: '13800000000', province: '广东省', city: '深圳市', district: '盐田区', detail: '北山道20号1201' } }); await p.save(); assert.equal(sent.poiName, undefined); assert.equal(sent.doorDetail, undefined); assert.equal(sent.detail, '北山道20号1201')
})
test('backgrounded save reconciles success on resume without retrying write', async () => {
  setup(); let respond, backed = 0; wx.navigateBack = () => backed++; wx.request = ({ success }) => { respond = success }
  const p = mount('../pages/addresses/edit'); p.onLoad({}); p.setData({ form: { ...p.data.form, recipientName: '张女士', phone: '13800000000', province: '广东省', city: '深圳市', district: '盐田区', detail: '北山道20号1201' } }); const saving = p.save(); p.onHide(); respond({ statusCode: 200, data: { success: true, data: {} } }); await saving; assert.equal(p.data.busy, false); assert.equal(backed, 0); p.onShow(); assert.equal(backed, 1)
})
test('save and use only selects after fresh range and current warehouse checks', async () => {
  setup(); const storage = new Map([['mall.memberToken', 'token'], ['mall.consumerStore.v1', { id: 's1' }]])
  wx.getStorageSync = key => storage.get(key); wx.setStorageSync = (key, value) => storage.set(key, value); let delta
  wx.navigateBack = value => { delta = value.delta }; let range = 'ELIGIBLE'
  wx.request = ({ method, success }) => success({ statusCode: 200, data: { success: true, data: method === 'GET' ? { code: range } : { id: 'new' } } })
  const p = mount('../pages/addresses/edit'); p.onLoad({ deliveryMode: 'DELIVERY', storeId: 's1', select: '1' }); p.setData({ form: { ...p.data.form, ...poi, doorDetail: '1201', recipientName: '张女士', phone: '13800000000' } }); await p.save(); assert.equal(storage.get('mall.selectedAddressId'), 'new'); assert.equal(delta, 2)
  storage.delete('mall.selectedAddressId'); range = 'OUT_OF_RANGE'; await p.save(); assert.equal(storage.get('mall.selectedAddressId'), undefined); assert.equal(delta, 1)
  range = 'ELIGIBLE'; storage.set('mall.consumerStore.v1', { id: 's2' }); await p.save(); assert.equal(storage.get('mall.selectedAddressId'), undefined); assert.equal(delta, 1)
})
