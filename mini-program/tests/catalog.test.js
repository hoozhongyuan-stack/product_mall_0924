const test = require('node:test')
const assert = require('node:assert/strict')
const catalog = require('../lib/catalog')
const api = require('../lib/api')

const base = 'http://127.0.0.1:8000'
global.getApp = () => ({ globalData: { apiBaseUrl: base } })

test('category tree excludes inactive items and keeps the two levels', () => {
  const tree = catalog.categoryTree([
    { id: 'root', parentId: null, status: 'ACTIVE', name: '酒类', sortOrder: 1 },
    { id: 'later', parentId: 'root', status: 'ACTIVE', name: '白酒', sortOrder: 20 },
    { id: 'first', parentId: 'root', status: 'ACTIVE', name: '红酒', sortOrder: 10 },
    { id: 'hidden', parentId: 'root', status: 'INACTIVE', name: '停用', sortOrder: 0 },
  ])
  assert.deepEqual(tree.map((item) => item.id), ['root'])
  assert.deepEqual(tree[0].children.map((item) => item.id), ['first', 'later'])
})

test('product presentation uses actual media and on-sale SKU data', () => {
  assert.equal(catalog.money(26800), '¥268')
  assert.equal(catalog.money(1299), '¥12.99')
  assert.equal(catalog.mediaUrl(base, 'https://outside.invalid/image'), '')
  const item = catalog.productDetail({
    productId: 'p1', name: '测试商品', fulfillmentKind: 'REDEEM',
    mainImageUrl: '/api/v1/app/assets/a/file', galleryImageUrls: ['/api/v1/app/assets/b/file'],
    videoUrl: '/api/v1/app/assets/c/file',
    skus: [{ skuId: 's1', specs: [{ name: '包装', value: '单瓶' }],
      listPriceFen: 26800, unit: { saleUnit: '瓶' } },
    { skuId: 's2', specs: [], listPriceFen: 12800 }],
    purchasable: false, availabilityMessage: '库存尚未配置，暂不可购买。',
  }, base)
  assert.deepEqual(item.images, [`${base}/api/v1/app/assets/a/file`, `${base}/api/v1/app/assets/b/file`])
  assert.equal(item.videoUrl, `${base}/api/v1/app/assets/c/file`)
  assert.equal(item.skus[0].label, '包装：单瓶')
  assert.equal(item.defaultSkuId, 's2')
  assert.equal(item.price, '¥128')
  assert.equal(item.fulfillment, '到店核销')
  assert.equal(item.purchasable, false)
})

test('API client sends encoded filters and reports delisted items', async () => {
  let requestedUrl = ''
  global.wx = { request(options) {
    requestedUrl = options.url
    options.success({ statusCode: 404, data: { success: false, error: { code: 'NOT_FOUND' } } })
  } }
  await assert.rejects(api.get('/api/v1/app/products', { keyword: '红 酒', page: 1 }),
    /内容已下架或暂不可用/)
  assert.equal(requestedUrl, `${base}/api/v1/app/products?keyword=%E7%BA%A2%20%E9%85%92&page=1`)
})

function mount(file) {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data),
    setData(patch) { this.data = { ...this.data, ...patch } } }
}

test('category page loads real filters, list, search and detail navigation', async () => {
  const requests = []
  let navigated = ''
  let delisted = false
  global.wx = {
    request(options) {
      requests.push(options.url)
      const parsed = new URL(options.url)
      const data = parsed.pathname.endsWith('/categories') ? [
        { id: 'root', parentId: null, name: '酒类', status: 'ACTIVE', sortOrder: 0 },
        { id: 'leaf', parentId: 'root', name: '葡萄酒', status: 'ACTIVE', sortOrder: 0 },
      ] : { rows: parsed.searchParams.get('keyword') === '未找到' || delisted ? [] : [{
        productId: 'p1', name: '经典干红', mainImageUrl: '/api/v1/app/assets/a/file',
        minListPriceFen: 26800, specsPreview: '包装：单瓶', availabilityCode: 'STOCK_NOT_READY',
      }], page: 1, pageSize: 20, total: parsed.searchParams.get('keyword') === '未找到' || delisted ? 0 : 1 }
      options.success({ statusCode: 200, data: { success: true, data } })
    },
    navigateTo({ url }) { navigated = url },
  }
  const page = mount('../pages/index/index.js')
  await page.onLoad()
  assert.equal(page.data.activeLeafId, 'leaf')
  assert.equal(page.data.products[0].price, '¥268')
  assert.ok(requests[1].includes('categoryId=leaf'))
  page.openProduct({ currentTarget: { dataset: { id: 'p1' } } })
  assert.equal(navigated, '/pages/product/detail?productId=p1')
  page.onShow()
  delisted = true
  await page.onShow()
  assert.equal(page.data.products.length, 0)
  assert.equal(page.data.listState, 'empty')
  page.onSearchInput({ detail: { value: ' 未找到 ' } })
  await page.submitSearch()
  assert.equal(page.data.listState, 'empty')
  assert.equal(page.data.keyword, '未找到')
  assert.ok(requests.at(-1).includes('keyword=%E6%9C%AA%E6%89%BE%E5%88%B0'))
  assert.ok(!requests.at(-1).includes('categoryId='))
})

test('home links open a selected category or prefilled cross-category search', async () => {
  const requests = []
  global.wx = {
    request(options) {
      requests.push(options.url)
      const data = options.url.includes('/categories') ? [
        { id: 'root', parentId: null, name: '葡萄酒', status: 'ACTIVE', sortOrder: 1 },
        { id: 'other', parentId: 'root', name: '干白', status: 'ACTIVE', sortOrder: 0 },
        { id: 'leaf', parentId: 'root', name: '干红', status: 'ACTIVE', sortOrder: 1 },
      ] : { rows: [], page: 1, pageSize: 20, total: 0 }
      options.success({ statusCode: 200, data: { success: true, data } })
    },
  }
  const categoryPage = mount('../pages/index/index.js')
  await categoryPage.onLoad({ categoryId: 'leaf' })
  assert.equal(categoryPage.data.activeLeafId, 'leaf')
  assert.ok(requests.at(-1).includes('categoryId=leaf'))
  const searchPage = mount('../pages/index/index.js')
  await searchPage.onLoad({ keyword: '干红', focusSearch: '1' })
  assert.equal(searchPage.data.keyword, '干红')
  assert.equal(searchPage.data.searchFocused, true)
  assert.ok(requests.at(-1).includes('keyword=%E5%B9%B2%E7%BA%A2'))
})

test('detail page shows only received SKU choices and handles delisting', async () => {
  let delisted = false
  global.wx = {
    request(options) {
      options.success(delisted ? { statusCode: 404, data: { success: false } } : {
        statusCode: 200, data: { success: true, data: {
          productId: '11111111-1111-1111-1111-111111111111', name: '经典干红',
          mainImageUrl: '/api/v1/app/assets/a/file', galleryImageUrls: [],
          skus: [
            { skuId: 's1', listPriceFen: 26800, specs: [{ name: '容量', value: '750ml' }] },
            { skuId: 's2', listPriceFen: 39800, specs: [{ name: '容量', value: '1L' }] },
            { skuId: 's3', listPriceFen: 19800, specs: [{ name: '容量', value: '小瓶' }] },
          ], availabilityMessage: '库存尚未配置，暂不可购买。', purchasable: false,
        } },
      })
    },
    setNavigationBarTitle() {},
  }
  const page = mount('../pages/product/detail.js')
  await page.onLoad({ productId: '11111111-1111-1111-1111-111111111111' })
  assert.equal(page.data.selectedPrice, '¥198')
  assert.equal(page.data.selectedSkuId, 's3')
  page.chooseSku({ currentTarget: { dataset: { id: 's2' } } })
  assert.equal(page.data.selectedPrice, '¥398')
  delisted = true
  await page.loadDetail()
  assert.equal(page.data.state, 'unavailable')
  assert.equal(page.data.product, null)
})

test('newer delisting response wins over a stale detail response', async () => {
  const pending = []
  global.wx = {
    request(options) { pending.push(options) },
    setNavigationBarTitle() {},
  }
  const page = mount('../pages/product/detail.js')
  const first = page.onLoad({ productId: '11111111-1111-1111-1111-111111111111' })
  const second = page.loadDetail()
  pending[1].success({ statusCode: 404, data: { success: false } })
  await second
  pending[0].success({ statusCode: 200, data: { success: true, data: {
    productId: '11111111-1111-1111-1111-111111111111', name: '已下架商品', skus: [],
  } } })
  await first
  assert.equal(page.data.state, 'unavailable')
  assert.equal(page.data.product, null)
})

test('unavailable direct product link returns to catalog without a page to pop', () => {
  let action = ''
  global.wx = {
    reLaunch({ url }) { action = `launch:${url}` },
    navigateBack() { action = 'back' },
  }
  const page = mount('../pages/product/detail.js')
  global.getCurrentPages = () => [{}]
  page.backToCatalog()
  assert.equal(action, 'launch:/pages/index/index')
  global.getCurrentPages = () => [{}, {}]
  page.backToCatalog()
  assert.equal(action, 'back')
})
