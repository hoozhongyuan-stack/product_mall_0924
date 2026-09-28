import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import HomePageView from '../../src/views/HomePageView.vue'
import MicroPageView from '../../src/views/MicroPageView.vue'

const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); sessionStorage.clear() })
const envelope = (data: unknown, status = 200) => new Response(JSON.stringify({ success: true, data }), { status })
const config = { schemaVersion: 1, pageType: 'HOME', theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#ffffff', brandTextColor: '#000000' }, components: [{ componentId: 'notice', type: 'NOTICE', sortOrder: 1, visible: true, props: { text: 'hello' } }] }
const account = { accountId: 'a1', permissionCodes: ['page.edit'] }
const categoryRows = [{ id: 'cat-1', name: '分类' }]
const productRows = [{ productId: 'product-1', name: '商品' }]
const pageRows = [{ pageId: 'p2', name: '已发布', revision: 1, publishedRevision: 1 }]

async function setup(component: typeof HomePageView, category: () => Response | Promise<Response>, product = () => envelope({ rows: productRows })) {
  vi.stubGlobal('fetch', vi.fn((input: string) => {
    if (input.includes('/app/categories')) return Promise.resolve(category())
    if (input.includes('/app/products')) return Promise.resolve(product())
    if (input.includes('/draft')) return Promise.resolve(envelope({ pageId: 'p1', name: '页面', revision: 1, config }))
    if (input.includes('/pages?')) return Promise.resolve(envelope({ rows: pageRows, page: 1, pageSize: 10, total: 1 }))
    throw new Error(`Unexpected fetch ${input}`)
  }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/editor', component, props: { account } }, { path: '/pages/home', component: { template: '<div />' } }] })
  await router.push('/editor?pageId=p1')
  await router.isReady()
  const wrapper = mount(RouterView, { global: { plugins: [router], stubs: {
    PublicationHistory: true, HomePagePreview: true,
    PageComponentEditor: { name: 'PageComponentEditor', props: ['categories', 'products', 'pages'], template: '<div data-testid="target-editor" />' },
    ElDialog: true, ElInput: true,
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.get('.home-component-select').trigger('click')
  return wrapper
}

for (const [name, component] of [['首页', HomePageView], ['微页面', MicroPageView]] as const) {
  describe(`${name} target loading`, () => {
    for (const [failure, response] of [
      ['HTTP error with misleading success envelope', () => envelope(categoryRows, 503)],
      ['application error envelope', () => new Response(JSON.stringify({ success: false, error: { message: 'not available' } }))],
      ['malformed list', () => envelope({ rows: [] })],
      ['malformed JSON', () => new Response('not json')],
    ] as const) {
      it(`shows a recoverable warning for ${failure} and clears it after retry`, async () => {
        let failing = true
        const wrapper = await setup(component, () => failing ? response() : envelope(categoryRows))
        expect(wrapper.text()).toContain('部分目标列表暂不可用')
        expect(wrapper.findComponent({ name: 'PageComponentEditor' }).props('categories')).toEqual([])
        const retry = wrapper.findAll('button').find(button => button.text() === '重试读取目标')
        expect(retry).toBeDefined()
        failing = false
        await retry!.trigger('click')
        await flushPromises()
        expect(wrapper.text()).not.toContain('部分目标列表暂不可用')
        expect(wrapper.findComponent({ name: 'PageComponentEditor' }).props('categories')).toEqual(categoryRows)
      })
    }
    it('clears old choices when that source fails on a subsequent reload', async () => {
      let second = false
      const wrapper = await setup(component, () => second ? envelope(categoryRows, 503) : envelope(categoryRows), () => second ? envelope({ rows: productRows }) : envelope({}, 503))
      expect(wrapper.findComponent({ name: 'PageComponentEditor' }).props('categories')).toEqual(categoryRows)
      second = true
      const retry = wrapper.findAll('button').find(button => button.text() === '重试读取目标')
      expect(retry).toBeDefined()
      await retry!.trigger('click')
      await flushPromises()
      expect(wrapper.findComponent({ name: 'PageComponentEditor' }).props('categories')).toEqual([])
      expect(wrapper.findComponent({ name: 'PageComponentEditor' }).props('products')).toEqual(productRows)
    })
  })
}
