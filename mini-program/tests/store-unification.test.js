const test = require('node:test')
const assert = require('node:assert/strict')
const { presentStorefront } = require('../lib/storefront')
const base = 'https://uat.aition.xin'
const pageA = '11111111-1111-1111-1111-111111111111'
const pageB = '22222222-2222-2222-2222-222222222222'
function mount(file) {
  let definition
  global.Page = value => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
test('v2 navigation supports ordered identities, multiple micro pages and paired safe icons', () => {
  const items = [
    { key: 'first', label: '精选', target: { type: 'MICRO', pageId: pageA } },
    { key: 'second', label: '活动', target: { type: 'MICRO', pageId: pageB }, iconUrl: `/api/v1/app/assets/${pageA}/file`, selectedIconUrl: `/api/v1/app/assets/${pageB}/file` },
    { key: 'home-custom', label: '店铺', target: { type: 'HOME' } },
  ]
  const result = presentStorefront({ navigation: { schemaVersion: 2, items } }, base).navigation
  assert.deepEqual(result.map(x => x.key), ['first', 'second', 'home-custom'])
  assert.equal(result[0].path, `/pages/micro/detail?pageId=${pageA}`)
  assert.equal(result[1].selectedIconUrl, `${base}/api/v1/app/assets/${pageB}/file`)
  assert.notEqual(result[0].iconUrl, result[0].selectedIconUrl)
  assert.equal(result[2].path, '/pages/home/home')
})
test('v2 invalid targets, duplicate identities and sixth entries fall back as one safe configuration', () => {
  const item = { key: 'one', label: '页面', target: { type: 'MICRO', pageId: pageA } }
  for (const items of [[{ ...item, target: { type: 'MICRO', pageId: 'javascript:evil' } }], [item, item], Array.from({ length: 6 }, (_, i) => ({ ...item, key: `n${i}` })), [{ ...item, label: '' }], [{ ...item, target: { type: 'EXTERNAL' } }]]) {
    assert.equal(presentStorefront({ navigation: { schemaVersion: 2, items } }, base).navigation.length, 4)
  }
})
test('category image retry falls back from thumbnail and revisiting a category restores recovery', async () => {
  const page = mount('../pages/index/index.js')
  page.loadProducts = async () => {}
  const leaf = { id: 'computer', bannerImageUrl: `${base}/api/v1/app/assets/${pageA}/thumbnail`, bannerOriginalUrl: `${base}/api/v1/app/assets/${pageA}/file` }
  const phone = { id: 'phone', bannerImageUrl: 'phone' }
  page.setData({ roots: [{ id: 'digital', children: [leaf, phone] }], leaves: [leaf, phone], activeRootId: 'digital', activeLeafId: leaf.id, activeBannerUrl: leaf.bannerImageUrl })
  page.bannerImageError({ currentTarget: { dataset: { id: leaf.id } } })
  assert.equal(page.data.activeBannerUrl, leaf.bannerOriginalUrl)
  page.bannerImageError({ currentTarget: { dataset: { id: leaf.id, url: leaf.bannerImageUrl } } })
  assert.equal(page.data.failedBannerImages.computer, undefined)
  page.bannerImageError({ currentTarget: { dataset: { id: leaf.id } } })
  assert.equal(page.data.failedBannerImages.computer, true)
  await page.selectLeaf({ currentTarget: { dataset: { id: 'phone' } } })
  await page.selectLeaf({ currentTarget: { dataset: { id: 'computer' } } })
  assert.equal(page.data.failedBannerImages.computer, undefined)
  assert.equal(page.data.activeBannerUrl, leaf.bannerImageUrl)
  page.bannerImageError({ currentTarget: { dataset: { id: 'phone' } } })
  assert.equal(page.data.activeBannerUrl, leaf.bannerImageUrl)
})
test('shared product line image failures apply only to current image and recover on changed input', () => {
  let definition
  global.Component = value => { definition = value }
  delete require.cache[require.resolve('../components/commerce-line/index.js')]
  require('../components/commerce-line/index.js')
  const line = { data: { line: { image: 'new-image' }, failedImage: false }, setData(patch) { this.data = { ...this.data, ...patch } } }
  definition.methods.imageError.call(line, { currentTarget: { dataset: { url: 'old-image' } } })
  assert.equal(line.data.failedImage, false)
  definition.methods.imageError.call(line, { currentTarget: { dataset: { url: 'new-image' } } })
  assert.equal(line.data.failedImage, true)
  definition.observers['line.image'].call(line)
  assert.equal(line.data.failedImage, false)
})
test('category thumbnails are preferred while retaining originals for load failure', () => {
  const { categoryTree } = require('../lib/catalog')
  const roots = categoryTree([{ id: 'root', status: 'ACTIVE', name: '酒', imageUrl: `/api/v1/app/assets/${pageA}/file`, imageThumbnailUrl: `/api/v1/app/assets/${pageA}/thumbnail` }, { id: 'leaf', parentId: 'root', status: 'ACTIVE', name: '白酒', bannerImageUrl: `/api/v1/app/assets/${pageB}/file`, bannerThumbnailUrl: `/api/v1/app/assets/${pageB}/thumbnail` }], base)
  assert.equal(roots[0].imageUrl, `${base}/api/v1/app/assets/${pageA}/thumbnail`)
  assert.equal(roots[0].children[0].bannerOriginalUrl, `${base}/api/v1/app/assets/${pageB}/file`)
})
test('empty and switched-store cart exits management and resets totals', async () => {
  const storage = new Map()
  global.wx = { getStorageSync: key => storage.get(key), setStorageSync: (key, v) => storage.set(key, v), removeStorageSync: key => storage.delete(key) }
  require('../lib/dining-alliance').leave()
  const cart = mount('../pages/cart/cart.js')
  cart.setData({ managing: true, total: '¥100' })
  await cart.refresh()
  assert.equal(cart.data.managing, false)
  assert.equal(cart.data.total, '¥0')
  cart.toggleManaging()
  assert.equal(cart.data.managing, false)
})
test('micro navigation active state identifies exact page and restaurant home maps HOME', () => {
  const { activeNavigation } = require('../lib/storefront')
  const items = presentStorefront({ navigation: { schemaVersion: 2, items: [
    { key: 'a', label: '微页一', target: { type: 'MICRO', pageId: pageA } },
    { key: 'b', label: '微页二', target: { type: 'MICRO', pageId: pageB } },
    { key: 'current', label: '首页', target: { type: 'HOME' } },
  ] } }, base).navigation
  assert.equal(activeNavigation(items, { route: 'pages/micro/detail', options: { pageId: pageB } }), 'b')
  assert.equal(activeNavigation(items, { route: 'pages/dining/shop' }), 'current')
  assert.equal(activeNavigation(items, { route: 'pages/orders/detail' }), '')
})
test('restaurant checkout loads authoritative offline instructions and formats common product rows', async () => {
  const api = require('../lib/api'), dining = require('../lib/dining-alliance')
  const originalPost = api.post, originalGet = api.get
  global.getApp = () => ({ globalData: { apiBaseUrl: base } })
  global.wx = { getStorageSync: () => 'member' }
  dining.open({ sessionId: 'session', restaurant: { id: 'r', name: '餐厅' }, table: { number: '08' } })
  api.post = async () => ({ quoteId: 'q', ready: true, orderSubmissionAvailable: true, confirmRequired: false, addressRequired: false,
    dining: { restaurantId: 'r', tableNumber: '08', address: '原餐桌地址' }, availablePaymentMethods: ['OFFLINE'], goodsTotalFen: 6800, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0, payableFen: 6800,
    lines: [{ skuId: 's', name: '茶', quantity: 1, unitPriceFen: 6800, lineAmountFen: 6800, specs: [{ name: '规格', value: '一瓶' }] }] })
  api.get = async path => { assert.equal(path, '/api/v1/app/payments/offline-policy'); return { configured: true, instructions: '付款后等待核实' } }
  try {
    const page = mount('../pages/dining/checkout.js')
    await page.onLoad()
    assert.equal(page.data.offlinePolicy.instructions, '付款后等待核实')
    assert.equal(page.data.lines[0].price, '¥68')
    assert.equal(page.data.lines[0].specLabel, '规格：一瓶')
    assert.equal(page.data.canSubmit, true)
  } finally { api.post = originalPost; api.get = originalGet; dining.leave() }
})
test('configured fifth tab uses live dining navigation and active micro identity with capability query', async () => {
  const api = require('../lib/api'), dining = require('../lib/dining-alliance')
  const originalGet = api.get, paths = []
  global.wx = { getStorageSync: () => 'member', reLaunch: ({ url }) => paths.push(url) }
  dining.open({ sessionId: 'table-session', restaurant: { id: 'r' }, table: { number: '08' } })
  global.getCurrentPages = () => [{ route: 'pages/micro/detail', options: { pageId: pageA } }]
  api.get = async (_path, query) => { assert.deepEqual(query, { navigationVersion: 2 }); return { navigation: { schemaVersion: 2, items: [
    { key: 'one', label: '首页', target: { type: 'HOME' } },
    { key: 'two', label: '分类', target: { type: 'CATEGORY' } },
    { key: 'three', label: '购物车', target: { type: 'CART' } },
    { key: 'four', label: '微页一', target: { type: 'MICRO', pageId: pageA } },
    { key: 'five', label: '微页二', target: { type: 'MICRO', pageId: pageB } },
  ] } } }
  let definition
  global.Component = value => { definition = value }
  delete require.cache[require.resolve('../components/storefront-shell/index.js')]
  require('../components/storefront-shell/index.js')
  const shell = { data: structuredClone(definition.data), ...definition.methods, setData(patch) { this.data = { ...this.data, ...patch } } }
  try {
    await definition.lifetimes.attached.call(shell)
    assert.equal(shell.data.activeKey, 'four')
    shell.changeTab({ currentTarget: { dataset: { key: 'five' } } })
    assert.equal(paths[0], `/pages/micro/detail?pageId=${pageB}`)
    assert.equal(dining.current().sessionId, 'table-session')
    shell.changeTab({ currentTarget: { dataset: { key: 'one' } } })
    assert.equal(paths[1], '/pages/dining/shop?visit=1')
    shell.changeTab({ currentTarget: { dataset: { key: 'forged' } } })
    assert.equal(paths.length, 2)
  } finally { api.get = originalGet; dining.leave() }
})
test('order disclosure retains snapshot amount/address and pending deadline independently of cart', () => {
  const storage = new Map()
  global.wx = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key) }
  storage.set('mall.memberToken', 'member')
  const page = mount('../pages/orders/detail.js')
  page.acceptOrder({ orderId: 'order', status: 'PENDING_PAYMENT', paymentMethod: 'OFFLINE', createdAt: '2026-10-12T11:00:00Z', expiresAt: '2030-10-12T13:30:00Z', payableFen: 6800, goodsTotalFen: 6800, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0, address: { detail: '订单保存地址' }, items: [{ orderLineId: 'l', name: '茶', quantity: 1, unitPriceFen: 6800, payableFen: 6800, fulfillmentKind: 'SHIP' }], paymentInstructions: { instructions: '订单保存付款说明' } })
  require('../lib/cart').write([{ skuId: 's', quantity: 2, seenPriceFen: 6800 }])
  page.toggleDetails()
  assert.equal(page.data.detailsOpen, true)
  assert.equal(page.data.order.address.detail, '订单保存地址')
  assert.equal(page.data.total, '¥68')
  assert.equal(page.data.items[0].quantity, 1)
  assert.match(page.data.expiresLabel, /2030-10-12/)
  assert.equal(page.data.order.paymentInstructions.instructions, '订单保存付款说明')
})
test('category name search stays within selected secondary category', async () => {
  const api = require('../lib/api'), originalGet = api.get
  const page = mount('../pages/index/index.js')
  let query
  api.get = async (_path, value) => { query = value; return { rows: [], total: 0 } }
  page.listToken = 0
  page.setData({ activeRootId: 'digital', activeLeafId: 'computer', keyword: '电脑' })
  try { await page.loadProducts(true); assert.equal(query.categoryId, 'computer'); assert.equal(query.keyword, '电脑') }
  finally { api.get = originalGet }
})
test('homepage global search stays global while explicitly linked category search retains category', async () => {
  const api = require('../lib/api'), originalGet = api.get, queries = []
  api.get = async (path, query) => {
    if (path.endsWith('/categories')) return [{ id: 'root', status: 'ACTIVE', name: '数码', children: [] }, { id: 'computer', parentId: 'root', status: 'ACTIVE', name: '电脑' }]
    queries.push(query); return { rows: [], total: 0 }
  }
  try {
    const globalSearch = mount('../pages/index/index.js'); await globalSearch.onLoad({ keyword: '礼盒' })
    assert.equal(queries.at(-1).categoryId, undefined)
    await globalSearch.clearSearch()
    globalSearch.onSearchInput({ detail: { value: '电脑' } }); await globalSearch.submitSearch()
    assert.equal(queries.at(-1).categoryId, 'computer')
    const scopedSearch = mount('../pages/index/index.js'); await scopedSearch.onLoad({ keyword: '电脑', categoryId: 'computer' })
    assert.equal(queries.at(-1).categoryId, 'computer')
  } finally { api.get = originalGet }
})
test('shared discount labels have one minus for ordinary, dining and order amount rows', () => {
  const { discountMoney } = require('../lib/catalog')
  assert.equal(discountMoney(2000), '−¥20')
  assert.equal(discountMoney(0), '−¥0')
  assert.equal(discountMoney(-1), '待核算')
  assert.equal(discountMoney(undefined), '待核算')
})
test('offline policy failure preserves available WeChat checkout and exposes actionable offline error', async () => {
  const api = require('../lib/api'), dining = require('../lib/dining-alliance')
  const originalPost = api.post, originalGet = api.get
  global.getApp = () => ({ globalData: { apiBaseUrl: base } })
  global.wx = { getStorageSync: () => 'member' }
  dining.open({ sessionId: 'session', restaurant: { id: 'r' }, table: { number: '08' } })
  api.post = async () => ({ quoteId: 'q', ready: true, orderSubmissionAvailable: true, confirmRequired: false, addressRequired: false,
    dining: { restaurantId: 'r', tableNumber: '08', address: '餐桌地址' }, availablePaymentMethods: ['OFFLINE', 'WECHAT'], goodsTotalFen: 6800, shippingFeeFen: 0, couponDiscountFen: 0, pointsDiscountFen: 0, payableFen: 6800, lines: [] })
  api.get = async () => { throw new Error('付款说明读取失败') }
  try {
    const page = mount('../pages/dining/checkout.js'); await page.onLoad()
    assert.equal(page.data.state, 'ready'); assert.deepEqual(page.data.paymentChoices, ['WECHAT'])
    assert.equal(page.data.paymentMethod, 'WECHAT'); assert.equal(page.data.canSubmit, true)
    assert.match(page.data.offlinePolicyError, /读取失败/)
  } finally { api.post = originalPost; api.get = originalGet; dining.leave() }
})
