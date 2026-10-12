import assert from 'node:assert/strict'
import test from 'node:test'
import { validateNavigation, validateCustomerService, copyConfig, updateNavigationItem, reconcileSavedConfig, normalizeNavigation, moveNavigationItem, removeNavigationItem, appendNavigationItem } from '../src/shared/singleton-config.mjs'

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

test('v2 navigation migrates v1, allows custom targets and five entries immutably', () => {
  const migrated = normalizeNavigation(navigation)
  assert.equal(migrated.schemaVersion, 2)
  assert.equal(migrated.items[0].target.type, 'HOME')
  assert.equal(navigation.schemaVersion, undefined)
  const next = appendNavigationItem(migrated, 'topic')
  const configured = updateNavigationItem(next, 'topic', { label: '专题', target: { type: 'MICRO', pageId: 'd7367cb7-9723-4cba-bbb7-129b57158e32' } })
  assert.equal(validateNavigation(configured), '')
  assert.equal(appendNavigationItem(configured, 'sixth'), configured)
  assert.equal(moveNavigationItem(configured, 'topic', -1).items[3].key, 'topic')
  assert.equal(removeNavigationItem(configured, 'topic').items.length, 4)
  assert.equal(removeNavigationItem({schemaVersion:2, items:[configured.items[0]]}, 'HOME').items.length, 1)
})
test('v2 navigation validates labels, identities, targets and media pairs', () => {
  const base=normalizeNavigation(navigation)
  for (const patch of [{label:''},{label:'01234567890'},{label:'坏\n标签'},{target:{type:'MICRO',pageId:''}},{target:{type:'UNKNOWN'}},{target:{type:'HOME',pageId:'p'}}]) {
    assert.notEqual(validateNavigation(updateNavigationItem(base,'HOME',patch)), '')
  }
  assert.match(validateNavigation({...base,items:[base.items[0],base.items[0]]}), /唯一/)
  assert.match(validateNavigation({...base,items:[]}), /1.*5/)
  assert.match(validateNavigation(updateNavigationItem(base,'HOME',{iconAssetId:'icon'})), /成对/)
  assert.equal(validateNavigation(updateNavigationItem(base,'HOME',{label:'商城',target:{type:'CART'}})), '')
})
