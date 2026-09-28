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
