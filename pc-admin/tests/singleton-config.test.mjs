import assert from 'node:assert/strict'
import test from 'node:test'
import { validateNavigation, validateCustomerService, copyConfig, updateNavigationItem, reconcileSavedConfig } from '../src/shared/singleton-config.mjs'

const navigation = { items: [
  { key: 'HOME', label: '首页', iconAssetId: null, selectedIconAssetId: null },
  { key: 'CATEGORY', label: '分类', iconAssetId: null, selectedIconAssetId: null },
  { key: 'CART', label: '购物车', iconAssetId: null, selectedIconAssetId: null },
  { key: 'ME', label: '我的', iconAssetId: null, selectedIconAssetId: null },
] }

test('navigation keeps exact order and requires icon pairs', () => {
  assert.equal(validateNavigation(navigation), '')
  assert.match(validateNavigation({ items: navigation.items.slice().reverse() }), /顺序/)
  assert.match(validateNavigation({ items: [{ ...navigation.items[0], label: '商店' }, ...navigation.items.slice(1)] }), /固定/)
  assert.match(validateNavigation({ items: [{ ...navigation.items[0], iconAssetId: 'id' }, ...navigation.items.slice(1)] }), /成对/)
})
test('navigation updates one item without mutating input', () => {
  const next = updateNavigationItem(navigation, 'CATEGORY', { iconAssetId: 'new-id' })
  assert.equal(navigation.items[1].label, '分类')
  assert.equal(next.items[1].iconAssetId, 'new-id')
  assert.equal(next.items[0], navigation.items[0])
})
test('service modes require their matching contact and clear inactive fields', () => {
  assert.equal(validateCustomerService({ enabled: false, mode: null, phone: '', qrAssetId: null, prompt: '', iconAssetId: null }), '')
  assert.match(validateCustomerService({ enabled: true, mode: 'PHONE', phone: '', qrAssetId: null, prompt: '', iconAssetId: null }), /手机号/)
  assert.match(validateCustomerService({ enabled: true, mode: 'QR', phone: '', qrAssetId: null, prompt: '', iconAssetId: null }), /二维码/)
  assert.match(validateCustomerService({ enabled: false, mode: null, phone: '13800138000', qrAssetId: null, prompt: '', iconAssetId: null }), /方式/)
  assert.match(validateCustomerService({ enabled: false, mode: 'PHONE', phone: '123', qrAssetId: null, prompt: '', iconAssetId: null }), /手机号/)
  const original = { enabled: true, mode: 'PHONE', phone: '13800138000', qrAssetId: null, prompt: '联系我们', iconAssetId: null }
  const cloned = copyConfig(original)
  assert.deepEqual(cloned, original)
  assert.notEqual(cloned, original)
  assert.deepEqual(copyConfig(new Proxy(original, {})), original)
})
test('save result normalization does not misreport a new edit', () => {
  const submitted = { enabled: true, mode: 'PHONE', phone: '13800138000', qrAssetId: null, prompt: ' 联系我们 ', iconAssetId: null }
  const server = { prompt: '联系我们', iconAssetId: null, enabled: true, mode: 'PHONE', phone: '13800138000', qrAssetId: null }
  const settled = reconcileSavedConfig(new Proxy(submitted, {}), submitted, server)
  assert.equal(settled.editedDuringSave, false)
  assert.deepEqual(settled.config, server)
  assert.equal(JSON.stringify(settled.config), settled.savedSnapshot)
  const newer = { ...submitted, prompt: '稍后联系' }
  const preserved = reconcileSavedConfig(newer, submitted, server)
  assert.equal(preserved.editedDuringSave, true)
  assert.deepEqual(preserved.config, newer)
  assert.equal(preserved.savedSnapshot, JSON.stringify(server))
})
