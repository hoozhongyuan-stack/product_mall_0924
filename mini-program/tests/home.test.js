const test = require('node:test')
const assert = require('node:assert/strict')

const base = 'http://127.0.0.1:8000'
const productId = '11111111-1111-1111-1111-111111111111'
const categoryId = '22222222-2222-2222-2222-222222222222'
const assetId = '33333333-3333-3333-3333-333333333333'
const pageId = '44444444-4444-4444-4444-444444444444'
global.getApp = () => ({ globalData: { apiBaseUrl: base } })

function mount(file) {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data),
    setData(patch) { this.data = { ...this.data, ...patch } } }
}

test('published home maps approved components and safe theme values', () => {
  const { homeContent } = require('../lib/home')
  const result = homeContent({ theme: {
    pageBackgroundColor: '#FFF5E8', headerBackgroundColor: 'red', brandTextColor: '#FFF8EF',
  }, components: [
    { componentId: 'notice', type: 'NOTICE', sortOrder: 3, visible: true,
      props: { text: '本期精选', link: { type: 'PRODUCT', targetId: productId } } },
    { componentId: 'hero', type: 'CAROUSEL', sortOrder: 1, visible: true,
      props: { slides: [{ assetId, link: { type: 'CATEGORY', targetId: categoryId } }] } },
    { componentId: 'hidden', type: 'FILING', sortOrder: 2, visible: false,
      props: { recordNo: '示例' } },
    { componentId: 'bad', type: 'NOTICE', sortOrder: 4, visible: true,
      props: { text: '安全', link: { type: 'URL', targetId: 'https://outside.invalid' } } },
    { componentId: 'divider', type: 'DIVIDER', sortOrder: 5, visible: true,
      props: { style: 'DASHED' } },
  ] }, base)
  assert.equal(result.theme.headerBackgroundColor, '#B63F32')
  assert.deepEqual(result.components.map((item) => item.id), ['hero', 'notice', 'bad', 'divider'])
  assert.equal(result.components[0].slides[0].imageUrl,
    `${base}/api/v1/app/assets/${assetId}/file`)
  assert.equal(result.components[2].link, null)
  assert.equal(result.components[3].dividerStyle, 'DASHED')
  assert.deepEqual(require('../lib/home').safeLink({ type: 'PAGE', targetId: pageId }),
    { type: 'PAGE', targetId: pageId })
  assert.equal(require('../lib/home').safeLink({ type: 'PAGE', targetId: '/pages/home/home' }), null)
  assert.equal(require('../lib/home').safeLink({ type: 'PAGE', targetId: { value: pageId } }), null)
})

test('home page reads published content and opens internal product/category/page/search targets', async () => {
  const navigations = []
  global.wx = {
    request(options) { options.success({ statusCode: 200, data: { success: true, data: {
      versionId: 'published-1', config: { theme: {}, components: [{
        componentId: 'notice', type: 'NOTICE', sortOrder: 1, visible: true,
        props: { text: '查看商品', link: { type: 'PRODUCT', targetId: productId } },
      }] },
    } } }) },
    navigateTo({ url }) { navigations.push(url) },
  }
  const page = mount('../pages/home/home.js')
  await page.onLoad()
  assert.equal(page.data.state, 'ready')
  assert.equal(page.data.components[0].text, '查看商品')
  page.openLink({ currentTarget: { dataset: { link: { type: 'PRODUCT', targetId: productId } } } })
  page.openLink({ currentTarget: { dataset: { link: { type: 'CATEGORY', targetId: categoryId } } } })
  page.openLink({ currentTarget: { dataset: { link: { type: 'PAGE', targetId: pageId } } } })
  page.onSearchInput({ detail: { value: '  干红  ' } })
  page.submitSearch()
  assert.deepEqual(navigations, [
    `/pages/product/detail?productId=${productId}`,
    `/pages/index/index?categoryId=${categoryId}`,
    `/pages/micro/detail?pageId=${pageId}`,
    '/pages/index/index?keyword=%E5%B9%B2%E7%BA%A2',
  ])
})

test('unpublished home keeps catalog available and never renders draft content', async () => {
  let target = ''
  global.wx = {
    request(options) { options.success({ statusCode: 404, data: { success: false,
      error: { code: 'HOME_UNPUBLISHED' } } }) },
    navigateTo({ url }) { target = url },
  }
  const page = mount('../pages/home/home.js')
  await page.onLoad()
  assert.equal(page.data.state, 'unpublished')
  assert.deepEqual(page.data.components, [])
  page.browseCatalog()
  assert.equal(target, '/pages/index/index')
})


test('schema 2 maps all new components with server-owned product prices', () => {
  const config = { schemaVersion: 2, components: [
    { componentId: 'title', type: 'TITLE', visible: true, sortOrder: 1, props: { text: '精选', subtitle: '本周推荐', align: 'CENTER', size: 24 }, appearance: { padding: 8, margin: 4, radius: 12, backgroundColor: '#FFFFFF' } },
    { componentId: 'image', type: 'IMAGE', visible: true, sortOrder: 2, props: { assetId, ratio: '16:9' } },
    { componentId: 'nav', type: 'NAVIGATION', visible: true, sortOrder: 3, props: { columns: 3, items: [{ title: '分类', assetId, link: { type: 'CATEGORY', targetId: categoryId } }] } },
    { componentId: 'products', type: 'PRODUCT_LIST', visible: true, sortOrder: 4, props: { limit: 2, layout: 'GRID', priceFen: 1 } },
  ] }
  const result = require('../lib/home').homeContent(config, base, { products: [
    { productId, name: '真实商品', priceFen: 1280, imageUrl: '', purchasable: false },
    { productId: 'invalid', name: '坏数据', priceFen: 1 },
  ] })
  assert.equal(result.components[0].titleStyle, 'text-align:center;font-size:48rpx;')
  assert.match(result.components[0].appearanceStyle, /padding:16rpx/)
  assert.match(result.components[0].appearanceStyle, /border-radius:24rpx/)
  assert.equal(result.components[1].ratio, '16:9')
  assert.equal(result.components[2].items[0].link.targetId, categoryId)
  assert.equal(result.components[3].products.length, 1)
  assert.equal(result.components[3].products[0].price, '12.80')
  assert.equal(result.components[3].products[0].link.targetId, productId)
  assert.equal(result.components[3].products[0].purchasable, false)
})

test('unknown components and unsupported schema return controlled fallbacks', () => {
  const home = require('../lib/home')
  assert.equal(home.homeContent({ components: [{ componentId: 'future', type: 'FUTURE', visible: true }] }, base).components[0].type, 'UNSUPPORTED')
  assert.throws(() => home.homeContent({ schemaVersion: 5, components: [] }, base), /更新/)
  assert.deepEqual(home.homeContent({ components: [{ componentId: 'products', type: 'PRODUCT_LIST', visible: true, props: {} }] }, base).components[0].products, [])
})

test('home sends supported schema and maps compatibility failures to actionable Chinese', async () => {
  global.wx = { request(options) {
    assert.match(options.url, /schemaVersion=4/)
    options.success({ statusCode: 409, data: { success: false, error: { code: 'PAGE_SCHEMA_UNSUPPORTED', message: 'Unsupported schema' } } })
  } }
  const page = mount('../pages/home/home.js')
  await page.onLoad()
  assert.equal(page.data.state, 'error')
  assert.match(page.data.error, /更新/)
})


test('new component defaults ignore malformed appearance, links and product values', () => {
  const home = require('../lib/home')
  const components = [
    { componentId: 't', type: 'TITLE', props: { text: 12, subtitle: null, align: 'unsafe', size: -1 }, appearance: null },
    { componentId: 'i', type: 'IMAGE', props: { assetId: 'bad', ratio: 'unsafe' }, appearance: { padding: -1, margin: 99, radius: '2', backgroundColor: 'red' } },
    { componentId: 'n', type: 'NAVIGATION', props: { columns: 5, items: [null, { title: 3 }, { title: '入口', link: { type: 'URL', targetId: 'https://bad' } }] } },
    { componentId: 'p', type: 'PRODUCT_LIST', props: { layout: 'SCROLL', limit: 1 } },
    { componentId: 'l', type: 'PRODUCT_LIST', props: { layout: 'LIST' } },
  ].map((item) => ({ ...item, visible: true }))
  const data = { p: [null, { productId, name: '合法', priceFen: 0, purchasable: true, imageUrl: 'https://example.test/file' }, { productId, name: '截断', priceFen: 100 }], l: [{ productId, name: '非法价', priceFen: -1 }] }
  const result = home.homeContent({ schemaVersion: 2, components }, base, data).components
  assert.equal(result[0].text, '')
  assert.equal(result[0].appearanceStyle, '')
  assert.equal(result[0].titleStyle, 'text-align:left;font-size:40rpx;')
  assert.equal(result[1].ratio, 'AUTO')
  assert.equal(result[1].appearanceStyle, '')
  assert.equal(result[2].columns, 4)
  assert.equal(result[2].items.length, 1)
  assert.equal(result[2].items[0].link, null)
  assert.equal(result[3].products.length, 1)
  assert.equal(result[3].products[0].price, '0.00')
  assert.equal(result[3].products[0].purchasable, true)
  assert.deepEqual(result[4].products, [])
})


test('product card resolves controlled relative app asset URL against API base', () => {
  const config = { components: [{ componentId: 'p', type: 'PRODUCT_LIST', visible: true, props: {} }] }
  const map = (url) => require('../lib/home').homeContent(config, base, { p: [{ productId, name: '商品', priceFen: 100, imageUrl: url }] }).components[0].products[0].imageUrl
  assert.equal(map(`/api/v1/app/assets/${assetId}/file`), `${base}/api/v1/app/assets/${assetId}/file`)
  assert.equal(map('/api/v1/admin/assets/private/file'), '')
  assert.equal(map('//outside.invalid/image'), '')
  assert.equal(map('/api/v1/app/assets/../private/file'), '')
})
