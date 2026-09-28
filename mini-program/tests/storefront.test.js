const test = require('node:test')
const assert = require('node:assert/strict')

const base = 'http://127.0.0.1:8000'
const asset = '11111111-1111-1111-1111-111111111111'

test('storefront navigation keeps the four fixed routes and safe icon fallbacks', () => {
  const { presentStorefront, NAVIGATION } = require('../lib/storefront')
  const result = presentStorefront({
    navigation: { versionId: 'published', items: [
      { key: 'HOME', label: '改名', iconUrl: `/api/v1/app/assets/${asset}/file`, selectedIconUrl: 'https://other.invalid/icon.png' },
      { key: 'CATEGORY', label: '分类' }, { key: 'CART', label: '购物车' }, { key: 'ME', label: '我的' },
    ] },
    customerService: { enabled: false },
  }, base)
  assert.deepEqual(result.navigation.map((item) => [item.key, item.label, item.path]),
    NAVIGATION.map((item) => [item.key, item.label, item.path]))
  assert.equal(result.navigation[0].iconUrl, `${base}/api/v1/app/assets/${asset}/file`)
  assert.match(result.navigation[0].selectedIconUrl, /^\/assets\/nav\//)
  assert.equal(result.service, null)
})

test('malformed navigation falls back; service accepts only safe published phone or QR', () => {
  const { presentStorefront } = require('../lib/storefront')
  const bad = presentStorefront({ navigation: { items: [{ key: 'HOME' }] },
    customerService: { enabled: true, mode: 'QR', qrUrl: 'https://other.invalid/qr.png' } }, base)
  assert.equal(bad.navigation.length, 4)
  assert.equal(bad.service, null)
  const phone = presentStorefront({ customerService: { enabled: true, mode: 'PHONE',
    phone: '13800000000', prompt: '联系店铺', iconUrl: `/api/v1/app/assets/${asset}/file` } }, base)
  assert.deepEqual({ mode: phone.service.mode, phone: phone.service.phone, prompt: phone.service.prompt },
    { mode: 'PHONE', phone: '13800000000', prompt: '联系店铺' })
  const qr = presentStorefront({ customerService: { enabled: true, mode: 'QR',
    qrUrl: `/api/v1/app/assets/${asset}/file` } }, base)
  assert.equal(qr.service.qrUrl, `${base}/api/v1/app/assets/${asset}/file`)
  assert.equal(presentStorefront({ customerService: { enabled: true, mode: 'PHONE',
    phone: 'javascript:alert(1)' } }, base).service, null)
})

test('storefront shell navigates only fixed routes and phone/QR require user action', async () => {
  const paths = []
  const calls = []
  global.getApp = () => ({ globalData: { apiBaseUrl: base } })
  global.getCurrentPages = () => [{ route: 'pages/home/home' }]
  global.wx = {
    request({ success }) { success({ statusCode: 200, data: { success: true, data: {
      navigation: { items: [] }, customerService: { enabled: true, mode: 'PHONE', phone: '13800000000' },
    } } }) },
    reLaunch({ url }) { paths.push(url) },
    makePhoneCall({ phoneNumber }) { calls.push(phoneNumber) },
  }
  let definition
  global.Component = (value) => { definition = value }
  delete require.cache[require.resolve('../components/storefront-shell/index.js')]
  require('../components/storefront-shell/index.js')
  const shell = { data: { ...structuredClone(definition.data), ...{ showNavigation: true, showService: true } },
    setData(patch) { this.data = { ...this.data, ...patch } } }
  Object.assign(shell, definition.methods)
  await definition.lifetimes.attached.call(shell)
  shell.changeTab({ currentTarget: { dataset: { key: 'HOME' } } })
  shell.changeTab({ currentTarget: { dataset: { key: 'CATEGORY' } } })
  shell.changeTab({ currentTarget: { dataset: { key: 'EVIL' } } })
  assert.deepEqual(paths, ['/pages/index/index'])
  assert.deepEqual(calls, [])
  shell.openService()
  assert.deepEqual(calls, ['13800000000'])
})

test('QR entry opens only after tap and network failure hides contact without hiding navigation', async () => {
  let fail = false
  global.getApp = () => ({ globalData: { apiBaseUrl: base } })
  global.getCurrentPages = () => [{ route: 'pages/member/index' }]
  global.wx = {
    request({ success, fail: reject }) {
      if (fail) return reject()
      success({ statusCode: 200, data: { success: true, data: {
        customerService: { enabled: true, mode: 'QR', qrUrl: `/api/v1/app/assets/${asset}/file` },
      } } })
    },
  }
  let definition
  global.Component = (value) => { definition = value }
  delete require.cache[require.resolve('../components/storefront-shell/index.js')]
  require('../components/storefront-shell/index.js')
  const shell = { data: structuredClone(definition.data),
    setData(patch) { this.data = { ...this.data, ...patch } } }
  Object.assign(shell, definition.methods)
  await definition.lifetimes.attached.call(shell)
  assert.equal(shell.data.qrOpen, false)
  assert.equal(shell.data.service.mode, 'QR')
  shell.openService()
  assert.equal(shell.data.qrOpen, true)
  shell.onQrError()
  assert.equal(shell.data.qrFailed, true)
  shell.closeQr()
  fail = true
  await shell.loadStorefront()
  assert.equal(shell.data.service, null)
  assert.equal(shell.data.items.length, 4)
})
