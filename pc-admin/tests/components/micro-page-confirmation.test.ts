import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import MicroPageView from '../../src/views/MicroPageView.vue'
import { api } from '../../src/api'
import { confirmAction } from '../../src/shared/confirm'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
vi.mock('../../src/shared/confirm', () => ({ confirmAction: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); sessionStorage.clear() })
async function setup() {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify({ success: true, data: url.includes('/products') ? { rows: [] } : [] }))))
  vi.mocked(api).mockReset()
  vi.mocked(confirmAction).mockReset()
  const draft = (id: string) => ({ pageId: id, name: id, revision: 1, config: { schemaVersion: 1, pageType: 'MICRO', theme: {}, components: [{ componentId: 'same-id', type: 'NOTICE', sortOrder: 1, visible: true, props: { text: id } }] } })
  vi.mocked(api).mockImplementation(async (path, init) => {
    if (init?.method === 'POST') return draft('new') as never
    if (/^\/pages\/[^/]+\/draft$/.test(path)) return draft(path.split('/')[2]!) as never
    if (path.startsWith('/pages?')) return { rows: ['p1', 'p2'].map(pageId => ({ pageId, name: pageId, revision: 1, publishedRevision: null })), total: 2, page: 1, pageSize: 10 } as never
    return [] as never
  })
  const account = reactive({ accountId: 'actor', permissionCodes: ['page.read', 'page.edit'] })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/pages/micro', component: MicroPageView, props: { account } }] })
  await router.push('/pages/micro?pageId=p1')
  const wrapper = mount(defineComponent({ template: '<RouterView />' }), { global: { plugins: [router], stubs: { PublicationHistory: true, HomePagePreview: true, PageComponentEditor: true, ElDialog: true } } })
  wrappers.push(wrapper); await flushPromises()
  return { router, wrapper, account }
}
function button(wrapper: ReturnType<typeof mount>, name: string) { return wrapper.findAll('button').find(b => b.text() === name)! }
it('does not remove a matching component from a different page after browser navigation while confirming', async () => {
  const { router, wrapper } = await setup()
  await wrapper.get('.home-component-select').trigger('click')
  let allow!: (value: boolean) => void
  vi.mocked(confirmAction).mockImplementationOnce(() => new Promise(resolve => { allow = resolve }))
  await button(wrapper, '移除组件').trigger('click')
  await router.push('/pages/micro?pageId=p2'); await flushPromises()
  allow(true); await flushPromises()
  expect(wrapper.get('.home-component-list').text()).toContain('公告栏')
  expect(wrapper.get('.micro-editor-actions input').element).toHaveProperty('value', 'p2')
})
it.each(['switch', 'create', 'reload'])('does not apply stale %s confirmation after navigation to another page', async action => {
  const { router, wrapper } = await setup()
  await wrapper.get('.micro-editor-actions input').setValue('unsaved')
  let allow!: (value: boolean) => void
  vi.mocked(confirmAction).mockImplementationOnce(() => new Promise(resolve => { allow = resolve }))
  if (action === 'create') { await wrapper.get('input[placeholder="例如 品牌故事"]').setValue('brand'); await wrapper.get('form.micro-create').trigger('submit') }
  else if (action === 'reload') await button(wrapper, '重新读取').trigger('click')
  else await wrapper.get('.micro-page-rows button:last-child').trigger('click')
  await router.push('/pages/micro?pageId=other'); await flushPromises()
  allow(true); await flushPromises()
  expect(router.currentRoute.value.query.pageId).toBe('other')
  expect(vi.mocked(api).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})

it.each(['remove', 'create'])('refuses pending %s when edit permission is revoked during confirmation', async action => {
  const { wrapper, account } = await setup()
  let allow!: (value: boolean) => void
  vi.mocked(confirmAction).mockImplementationOnce(() => new Promise(resolve => { allow = resolve }))
  if (action === 'remove') {
    await wrapper.get('.home-component-select').trigger('click')
    await button(wrapper, '移除组件').trigger('click')
  } else {
    await wrapper.get('.micro-editor-actions input').setValue('unsaved')
    await wrapper.get('input[placeholder="例如 品牌故事"]').setValue('brand')
    await wrapper.get('form.micro-create').trigger('submit')
  }
  account.permissionCodes = ['page.read']
  allow(true); await flushPromises()
  expect(wrapper.get('.home-component-list').text()).toContain('公告栏')
  expect(vi.mocked(api).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})
