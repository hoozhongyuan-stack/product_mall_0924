const test = require('node:test')
const assert = require('node:assert/strict')

const base = 'http://127.0.0.1:8000'
const firstId = '11111111-1111-1111-1111-111111111111'
const secondId = '22222222-2222-2222-2222-222222222222'
const assetId = '33333333-3333-3333-3333-333333333333'
global.getApp = () => ({ globalData: { apiBaseUrl: base } })

function mount(file) {
  let definition
  global.Page = (value) => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data),
    setData(patch) { this.data = { ...this.data, ...patch } } }
}

function publishedConfig() {
  return { theme: { pageBackgroundColor: '#FFF5E8', headerBackgroundColor: '#B63F32',
    brandTextColor: '#FFF8EF' }, components: [
    { componentId: 'hero', type: 'CAROUSEL', sortOrder: 1, visible: true, props: {
      slides: [{ assetId, link: { type: 'PAGE', targetId: secondId } }],
    } },
    { componentId: 'search', type: 'SEARCH', sortOrder: 2, visible: true, props: {} },
    { componentId: 'notice', type: 'NOTICE', sortOrder: 3, visible: true, props: {
      text: '活动说明', link: { type: 'URL', targetId: 'https://outside.invalid' },
    } },
    { componentId: 'image', type: 'IMAGE_HOTZONE', sortOrder: 4, visible: true,
      props: { assetId, areas: [] } },
    { componentId: 'divider', type: 'DIVIDER', sortOrder: 5, visible: true, props: {} },
    { componentId: 'filing', type: 'FILING', sortOrder: 6, visible: true,
      props: { recordNo: '备案展示' } },
  ] }
}

test('published micro page loads mapped components and allows another published page', async () => {
  const urls = []
  const destinations = []
  let title = ''
  global.getCurrentPages = () => [{ route: 'pages/home/home', options: {} },
    { route: 'pages/micro/detail', options: { pageId: firstId } }]
  global.wx = {
    request(options) {
      urls.push(options.url)
      options.success({ statusCode: 200, data: { success: true, data: {
        pageId: firstId, versionId: 'version-1', name: '中秋专题', config: publishedConfig(),
      } } })
    },
    navigateTo({ url }) { destinations.push(url) },
    setNavigationBarTitle({ title: next }) { title = next },
  }
  const page = mount('../pages/micro/detail.js')
  await page.onLoad({ pageId: firstId })
  assert.deepEqual(urls, [`${base}/api/v1/app/pages/${firstId}`])
  assert.equal(page.data.state, 'ready')
  assert.equal(page.data.name, '中秋专题')
  assert.equal(title, '中秋专题')
  assert.deepEqual(page.data.components.map((item) => item.type),
    ['CAROUSEL', 'SEARCH', 'NOTICE', 'IMAGE_HOTZONE', 'DIVIDER', 'FILING'])
  page.openLink({ currentTarget: { dataset: { link: { type: 'PAGE', targetId: secondId } } } })
  page.openLink({ currentTarget: { dataset: { link: { type: 'URL', targetId: 'https://outside.invalid' } } } })
  assert.deepEqual(destinations, [`/pages/micro/detail?pageId=${secondId}`])
})

test('micro page blocks repeated page in stack and page-stack overflow', async () => {
  const destinations = []
  const messages = []
  global.getCurrentPages = () => [{ route: 'pages/micro/detail', options: { pageId: secondId } },
    { route: 'pages/micro/detail', options: { pageId: firstId } }]
  global.wx = {
    request(options) { options.success({ statusCode: 200, data: { success: true, data: {
      pageId: firstId, versionId: 'v1', name: '活动', config: publishedConfig(),
    } } }) },
    navigateTo({ url }) { destinations.push(url) },
    showToast({ title }) { messages.push(title) },
    setNavigationBarTitle() {},
  }
  const page = mount('../pages/micro/detail.js')
  await page.onLoad({ pageId: firstId })
  page.openLink({ currentTarget: { dataset: { link: { type: 'PAGE', targetId: secondId } } } })
  assert.deepEqual(destinations, [])
  assert.match(messages[0], /已打开/)
  global.getCurrentPages = () => Array.from({ length: 10 }, (_, index) => ({
    route: 'pages/index/index', options: { index },
  }))
  page.openLink({ currentTarget: { dataset: { link: { type: 'PAGE', targetId: secondId } } } })
  assert.deepEqual(destinations, [])
  assert.match(messages[1], /层级/)
})

test('catalog recovery replaces the page when navigation stack is full', async () => {
  let destination = ''
  global.getCurrentPages = () => Array.from({ length: 10 }, () => ({
    route: 'pages/index/index', options: {},
  }))
  global.wx = {
    redirectTo({ url }) { destination = url },
    navigateTo() { assert.fail('full stack must not navigateTo') },
  }
  const page = mount('../pages/micro/detail.js')
  page.browseCatalog()
  assert.equal(destination, '/pages/index/index')
})

test('unpublished micro page hides content and provides retry and return', async () => {
  let calls = 0
  let back = 0
  global.getCurrentPages = () => [{ route: 'pages/home/home', options: {} },
    { route: 'pages/micro/detail', options: { pageId: firstId } }]
  global.wx = {
    request(options) {
      calls += 1
      options.success({ statusCode: 404, data: { success: false,
        error: { code: 'PAGE_UNPUBLISHED' } } })
    },
    navigateBack() { back += 1 },
  }
  const page = mount('../pages/micro/detail.js')
  await page.onLoad({ pageId: firstId })
  assert.equal(page.data.state, 'unpublished')
  assert.deepEqual(page.data.components, [])
  await page.loadPage()
  assert.equal(calls, 2)
  page.goBack()
  assert.equal(back, 1)
})

test('published page with no visible components has an explicit empty state', async () => {
  global.getCurrentPages = () => [{ route: 'pages/micro/detail', options: { pageId: firstId } }]
  global.wx = {
    request(options) { options.success({ statusCode: 200, data: { success: true, data: {
      pageId: firstId, versionId: 'v1', name: '暂无组件', config: { components: [] },
    } } }) },
    setNavigationBarTitle() {},
  }
  const page = mount('../pages/micro/detail.js')
  await page.onLoad({ pageId: firstId })
  assert.equal(page.data.state, 'empty')
  assert.deepEqual(page.data.components, [])
})

test('network failure is retryable and invalid page ID never reaches server', async () => {
  let calls = 0
  global.getCurrentPages = () => [{ route: 'pages/micro/detail', options: {} }]
  global.wx = {
    request(options) { calls += 1; options.fail({}) },
    reLaunch({ url }) { assert.equal(url, '/pages/home/home') },
  }
  const page = mount('../pages/micro/detail.js')
  await page.onLoad({ pageId: 'https://outside.invalid' })
  assert.equal(calls, 0)
  assert.equal(page.data.state, 'error')
  page.goBack()
  await page.onLoad({ pageId: firstId })
  assert.equal(page.data.state, 'error')
  assert.match(page.data.error, /网络/)
  await page.loadPage()
  assert.equal(calls, 2)
})
