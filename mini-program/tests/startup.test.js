const test = require('node:test')
const assert = require('node:assert/strict')

const startup = require('../lib/startup')

function mount(file) {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data),
    setData(patch) { this.data = { ...this.data, ...patch } } }
}

test.afterEach(() => {
  startup.resetForTests()
  delete global.wx
  delete global.getApp
})

test('launch target keeps a shared product or scan query and rejects external paths', () => {
  startup.captureLaunch({ path: 'pages/product/detail', query: { productId: 'a-b', scene: 'qr/活动' } })
  assert.equal(startup.targetUrl(), '/pages/product/detail?productId=a-b&scene=qr%2F%E6%B4%BB%E5%8A%A8')
  startup.captureLaunch({ path: 'https://outside.invalid', query: {} })
  assert.equal(startup.targetUrl(), '/pages/home/home')
  startup.captureLaunch({ path: 'pages/launch/launch', query: {} })
  assert.equal(startup.targetUrl(), '/pages/home/home')
})

test('direct target redirects once on cold launch and not after completion or hot show', () => {
  const destinations = []
  global.wx = { redirectTo({ url }) { destinations.push(url) } }
  startup.captureLaunch({ path: 'pages/micro/detail', query: { pageId: 'p1' } })
  assert.equal(startup.redirectIfPending(), true)
  assert.equal(startup.redirectIfPending(), true)
  assert.deepEqual(destinations, ['/pages/launch/launch'])
  assert.equal(startup.completeLaunch(), '/pages/micro/detail?pageId=p1')
  assert.equal(startup.redirectIfPending(), false)
})

test('startup page waits for first render, uses fallback on GIF error, and skips once', async () => {
  const pending = []
  const destinations = []
  global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
  global.wx = {
    request(options) { pending.push(options) },
    reLaunch({ url }) { destinations.push(url) },
  }
  startup.captureLaunch({ path: 'pages/index/index', query: { keyword: '葡萄 酒' } })
  const page = mount('../pages/launch/launch.js')
  page.onLoad()
  assert.equal(page.data.remainingSeconds, 3)
  assert.equal(page.timer, undefined)
  pending[0].success({ statusCode: 200, data: { success: true,
    data: { versionId: 'v1', gifUrl: '/api/v1/app/assets/gif/file',
      fallbackUrl: '/api/v1/app/assets/fallback/file' } } })
  await Promise.resolve()
  assert.equal(page.data.mediaUrl, 'http://127.0.0.1:8000/api/v1/app/assets/gif/file')
  page.onGifError()
  assert.equal(page.data.mediaUrl, 'http://127.0.0.1:8000/api/v1/app/assets/fallback/file')
  page.onShow()
  page.onReady()
  assert.ok(page.timer)
  page.skip()
  page.skip()
  assert.deepEqual(destinations, ['/pages/index/index?keyword=%E8%91%A1%E8%90%84%20%E9%85%92'])
  page.onUnload()
})

test('unpublished startup still counts down with local brand fallback', async () => {
  const destinations = []
  global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
  global.wx = {
    request(options) { options.success({ statusCode: 404, data: { success: false,
      error: { code: 'STARTUP_UNPUBLISHED' } } }) },
    reLaunch({ url }) { destinations.push(url) },
  }
  startup.captureLaunch({ path: 'pages/home/home', query: {} })
  const page = mount('../pages/launch/launch.js')
  await page.onLoad()
  assert.equal(page.data.mediaUrl, '')
  assert.equal(page.data.mediaState, 'brand')
  page.onShow()
  page.onReady()
  assert.ok(page.timer)
  page.skip()
  assert.deepEqual(destinations, ['/pages/home/home'])
  page.onUnload()
})

test('three-second countdown starts onReady and restores the scan target only once', async () => {
  const originalTimeout = global.setTimeout
  const originalClearTimeout = global.clearTimeout
  const originalInterval = global.setInterval
  const originalClearInterval = global.clearInterval
  let timeout
  let navigations = []
  global.setTimeout = (callback, delay) => { timeout = { callback, delay }; return 1 }
  global.clearTimeout = () => {}
  global.setInterval = () => 2
  global.clearInterval = () => {}
  try {
    global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
    global.wx = {
      request(options) { options.fail({}) },
      reLaunch({ url }) { navigations.push(url) },
    }
    startup.captureLaunch({ path: 'pages/product/detail', query: { productId: 'sku-1' } })
    const page = mount('../pages/launch/launch.js')
    await page.onLoad()
    assert.equal(timeout, undefined)
    page.onShow()
    assert.equal(timeout, undefined)
    page.onReady()
    assert.equal(timeout.delay, 3000)
    assert.deepEqual(navigations, [])
    timeout.callback()
    page.skip()
    assert.deepEqual(navigations, ['/pages/product/detail?productId=sku-1'])
    page.onUnload()
  } finally {
    global.setTimeout = originalTimeout
    global.clearTimeout = originalClearTimeout
    global.setInterval = originalInterval
    global.clearInterval = originalClearInterval
  }
})

test('a shared target is intercepted before its product request', () => {
  const destinations = []
  global.wx = {
    redirectTo({ url }) { destinations.push(url) },
    request() { assert.fail('target must not load before startup') },
  }
  startup.captureLaunch({ path: 'pages/product/detail', query: { productId: 'p1' } })
  const page = mount('../pages/product/detail.js')
  page.onLoad({ productId: 'p1' })
  assert.deepEqual(destinations, ['/pages/launch/launch'])
})

test('failed startup redirect resumes the original target instead of leaving it blank', async () => {
  let requests = 0
  global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
  global.wx = {
    redirectTo({ fail }) { fail({ errMsg: 'navigation failed' }) },
    request(options) {
      requests += 1
      options.success({ statusCode: 200, data: { success: true,
        data: { versionId: 'home-1', config: { theme: {}, components: [] } } } })
    },
  }
  startup.captureLaunch({ path: 'pages/home/home', query: {} })
  const page = mount('../pages/home/home.js')
  await page.onLoad()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(requests, 1)
  assert.equal(page.data.state, 'ready')
  assert.equal(startup.redirectIfPending(), false)
})

test('backgrounding pauses visible countdown; returning resumes without a new launch', async () => {
  const originalTimeout = global.setTimeout
  const originalClearTimeout = global.clearTimeout
  const originalInterval = global.setInterval
  const originalClearInterval = global.clearInterval
  const originalNow = Date.now
  const delays = []
  let now = 10000
  global.setTimeout = (_callback, delay) => { delays.push(delay); return delays.length }
  global.clearTimeout = () => {}
  global.setInterval = () => 100
  global.clearInterval = () => {}
  Date.now = () => now
  try {
    global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
    global.wx = { request(options) { options.fail({}) } }
    startup.captureLaunch({ path: 'pages/home/home', query: {} })
    const page = mount('../pages/launch/launch.js')
    await page.onLoad()
    page.onShow()
    page.onReady()
    assert.deepEqual(delays, [3000])
    now += 1200
    page.onHide()
    assert.equal(page.data.remainingSeconds, 2)
    page.onShow()
    assert.deepEqual(delays, [3000, 1800])
    page.onUnload()
  } finally {
    global.setTimeout = originalTimeout
    global.clearTimeout = originalClearTimeout
    global.setInterval = originalInterval
    global.clearInterval = originalClearInterval
    Date.now = originalNow
  }
})

test('ordinary launch from a previously selected category opens home while explicit links retain their route', () => {
  startup.captureLaunch({ path: 'pages/index/index', query: {}, scene: 1001 })
  assert.equal(startup.completeLaunch(), '/pages/home/home')
  startup.captureLaunch({ path: 'pages/index/index', query: {}, scene: 1007 })
  assert.equal(startup.completeLaunch(), '/pages/index/index')
  startup.captureLaunch({ path: 'pages/index/index', query: { categoryId: 'wine' }, scene: 1001 })
  assert.equal(startup.completeLaunch(), '/pages/index/index?categoryId=wine')
  startup.captureLaunch({ path: 'pages/product/detail', query: { productId: 'p1' }, scene: 1011 })
  assert.equal(startup.completeLaunch(), '/pages/product/detail?productId=p1')
})

test('fullscreen startup keeps skip below native capsule', async () => {
 global.getApp = () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } })
 global.wx = { getMenuButtonBoundingClientRect: () => ({ bottom: 56 }), request(r) { r.fail() } }
 const page = mount('../pages/launch/launch.js'); await page.onLoad(); assert.equal(page.data.skipTop, 72)
 const fs = require('node:fs'), path = require('node:path')
 const markup = fs.readFileSync(path.join(__dirname, '../pages/launch/launch.wxml'), 'utf8')
 const config = JSON.parse(fs.readFileSync(path.join(__dirname, '../pages/launch/launch.json'), 'utf8'))
 assert.equal(config.navigationStyle, 'custom'); assert.match(markup, /mode="aspectFill"/); assert.doesNotMatch(markup, /品牌名称/)
})
