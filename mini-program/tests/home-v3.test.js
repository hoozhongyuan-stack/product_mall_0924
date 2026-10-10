const test = require('node:test')
const assert = require('node:assert/strict')
const base = 'https://mall.test'
const id = '11111111-1111-1111-1111-111111111111'
global.getApp = () => ({ globalData: { apiBaseUrl: base } })
function mount(file) {
  let definition
  global.Page = value => { definition = value }
  delete require.cache[require.resolve(file)]
  require(file)
  return { ...definition, data: structuredClone(definition.data), setData(patch) { this.data = { ...this.data, ...patch } } }
}
test('schema3 maps every fixed mosaic and design-unit spacers', () => {
  for (const [template, count] of [['TWO', 2], ['THREE', 3], ['FOUR', 4], ['FEATURED', 3]]) {
    const config = { schemaVersion: 3, components: [{ componentId: 'm', type: 'MOSAIC', visible: true, props: { template, gap: 8, items: Array.from({ length: count }, () => ({ assetId: id, title: '普通标题', link: { type: 'FUNCTION', targetId: 'SEARCH' } })) } }, { componentId: 's', type: 'SPACER', visible: true, props: { height: 48 } }] }
    const result = require('../lib/home').homeContent(config, base)
    assert.equal(result.components[0].template, template)
    assert.equal(result.components[0].items.length, count)
    assert.equal(result.components[0].gapStyle, 'gap:16rpx;')
    assert.equal(result.components[0].items[0].imageUrl, `${base}/api/v1/app/assets/${id}/file`)
    assert.equal(result.components[1].heightStyle, 'height:96rpx;')
  }
})
test('mosaic invalid shape is controlled and missing links/images never navigate externally', () => {
  const config = { schemaVersion: 3, components: [{ componentId: 'm', type: 'MOSAIC', visible: true, props: { template: 'TWO', gap: -1, items: [{ assetId: 'bad', link: { type: 'URL', targetId: 'https://bad.test' } }, null] } }] }
  const item = require('../lib/home').homeContent(config, base).components[0]
  assert.equal(item.items.length, 2)
  assert.equal(item.items[0].imageUrl, '')
  assert.equal(item.items[0].link, null)
  assert.equal(item.items[1].title, '')
  assert.equal(item.gapStyle, 'gap:0rpx;')
  config.components[0].props.template = 'UNKNOWN'
  assert.equal(require('../lib/home').homeContent(config, base).components[0].type, 'UNSUPPORTED')
})
test('published home sharing uses safe cover/title and suppresses drafts on failures', async () => {
  const menus = []
  let fail = false
  global.wx = { hideShareMenu() { menus.push('hide') }, showShareMenu() { menus.push('show') }, request(options) {
    assert.match(options.url, /schemaVersion=4/)
    if (fail) return options.fail({})
    options.success({ statusCode: 200, data: { success: true, data: { versionId: 'v', config: { schemaVersion: 3, components: [] }, share: { title: '品牌精选', description: '简介', coverUrl: `/api/v1/app/assets/${id}/file` } } } })
  } }
  const page = mount('../pages/home/home.js')
  await page.onLoad()
  assert.deepEqual(page.onShareAppMessage(), { title: '品牌精选', path: '/pages/home/home', imageUrl: `${base}/api/v1/app/assets/${id}/file` })
  assert.deepEqual(page.onShareTimeline(), { title: '品牌精选', imageUrl: `${base}/api/v1/app/assets/${id}/file` })
  assert.deepEqual(menus, ['hide', 'show'])
  fail = true
  await page.loadHome()
  assert.deepEqual(page.onShareAppMessage(), { title: '商城首页', path: '/pages/home/home' })
  assert.equal(menus.at(-1), 'hide')
})
test('micro sharing uses UUID path and safe timeline query with no private image URL', async () => {
  global.wx = { request(options) { options.success({ statusCode: 200, data: { success: true, data: { pageId: id, versionId: 'v', name: '活动', config: { components: [] }, share: { title: '', coverUrl: `${base}/api/v1/admin/assets/${id}/file` } } } }) } }
  const page = mount('../pages/micro/detail.js')
  await page.onLoad({ pageId: id })
  assert.deepEqual(page.onShareAppMessage(), { title: '活动', path: `/pages/micro/detail?pageId=${id}` })
  assert.deepEqual(page.onShareTimeline(), { title: '活动', query: `pageId=${id}` })
  await page.onLoad({ pageId: 'bad' })
  assert.deepEqual(page.onShareAppMessage(), { title: '商城首页', path: '/pages/home/home' })
})

test('share cover guard rejects private, external and malformed public URL forms', () => {
  const sharing = require('../lib/published-sharing')
  const cover = value => sharing.contentShare({ title: '  分享标题  ', coverUrl: value }, '备用', base)
  assert.equal(cover(`${base}/api/v1/app/assets/${id}/file`).imageUrl, `${base}/api/v1/app/assets/${id}/file`)
  for (const value of [`${base}/api/v1/admin/assets/${id}/file`, `https://outside.test/api/v1/app/assets/${id}/file`, `//mall.test/api/v1/app/assets/${id}/file`, `/api/v1/app/assets/${id}/file?private=1`, '/api/v1/app/assets/../file', null]) {
    assert.deepEqual(cover(value), { title: '分享标题', imageUrl: '' })
  }
  assert.deepEqual(sharing.contentShare(null, '备用', base), { title: '备用', imageUrl: '' })
  assert.deepEqual(sharing.message({ state: 'ready', versionId: 'v', share: { title: '活动', imageUrl: '' } }, 'invalid'), { title: '商城首页', path: '/pages/home/home' })
})
test('home never enables share on a success envelope without a published version', async () => {
  let shown = 0
  global.wx = { showShareMenu() { shown++ }, request(options) { options.success({ statusCode: 200, data: { success: true, data: { config: { schemaVersion: 3, components: [] }, share: { title: '草稿名' } } } }) } }
  const page = mount('../pages/home/home.js')
  await page.onLoad()
  assert.equal(page.data.state, 'error')
  assert.equal(shown, 0)
  assert.deepEqual(page.onShareAppMessage(), { title: '商城首页', path: '/pages/home/home' })
  assert.deepEqual(page.onShareTimeline(), { title: '商城首页' })
})
