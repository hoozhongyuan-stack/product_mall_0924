import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import ElementPlus from 'element-plus'
import { router as applicationRouter } from '../../src/router'
import { authorizedSections } from '../../src/navigation'
import { api } from '../../src/api'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const account = { accountId: 'test', permissionCodes: ['inventory.read', 'inventory.manage'] }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => {
  vi.stubGlobal('confirm', vi.fn(() => false))
  vi.mocked(api).mockImplementation(async path => path === '/warehouses'
    ? { items: [{ warehouseId: 'w1', name: '中心仓', enabled: true, isDefault: true }] } as never
    : { items: [], total: 0, page: 1, pageSize: 20 } as never)
})
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
async function setup(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: applicationRouter.options.routes })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(defineComponent({ setup: () => ({ account }), template: '<RouterView v-slot="{ Component }"><component :is="Component" :account="account" /></RouterView>' }), { global: { plugins: [router, ElementPlus] } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
it('uses existing secondary navigation for six inventory tasks and keeps legacy entry', () => {
  const router = createRouter({ history: createMemoryHistory(), routes: applicationRouter.options.routes })
  const group = authorizedSections(router, account.permissionCodes).find(s => s.name === '库存')!
  expect(group.links.map(l => l.title)).toEqual(['库存查询', '仓库管理', '入库单', '出库单', '盘点单', '库存流水'])
  expect(group.hasSubnavigation).toBe(true)
  expect(router.resolve('/inventory').meta.permission).toBe('inventory.read')
})
it('protects same-component navigation and route leave, retaining cancelled values', async () => {
  const { wrapper, router } = await setup('/inventory/warehouses')
  await wrapper.findAll('button').find(b => b.text() === '新建仓库')!.trigger('click')
  await wrapper.get('input[placeholder="例如 WH-001"]').setValue('WH-NEW')
  await router.push('/inventory/inbounds')
  expect(router.currentRoute.value.path).toBe('/inventory/warehouses')
  expect((wrapper.get('input[placeholder="例如 WH-001"]').element as HTMLInputElement).value).toBe('WH-NEW')
  await router.push('/')
  expect(router.currentRoute.value.path).toBe('/inventory/warehouses')
  vi.mocked(window.confirm).mockReturnValue(true)
  await router.push('/inventory/inbounds')
  await flushPromises()
  expect(wrapper.text()).toContain('创建入库单')
  expect(wrapper.find('.inventory-tabs').exists()).toBe(false)
  await router.push('/inventory/warehouses')
  await flushPromises()
  expect(wrapper.find('input[placeholder="例如 WH-001"]').exists()).toBe(false)
})
it('restores selected panel on direct navigation and browser back', async () => {
  const { wrapper, router } = await setup('/inventory/outbounds')
  expect(wrapper.text()).toContain('新建出库单')
  await router.push('/inventory/ledgers')
  await flushPromises()
  expect(wrapper.text()).toContain('导出所选行 CSV')
  router.back()
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/inventory/outbounds')
})
it('splits store and mini-program without additional permissions', () => {
  const router = createRouter({ history: createMemoryHistory(), routes: applicationRouter.options.routes })
  const sections = authorizedSections(router, ['startup.read', 'wechat.integration.read'])
  expect(sections.find(s => s.name === '店铺')?.links.map(l => l.path)).toEqual(['/store/info'])
  expect(sections.find(s => s.name === '小程序')?.links.map(l => l.path)).toEqual(['/store/wechat-integration'])
  expect(sections.some(s => s.name === '店铺与小程序')).toBe(false)
})
it('blocks route updates and unload during a write, without discarding a pending warehouse', async () => {
  const { wrapper, router } = await setup('/inventory/warehouses')
  await wrapper.findAll('button').find(b => b.text() === '新建仓库')!.trigger('click')
  await wrapper.get('input[placeholder="例如 WH-001"]').setValue('WH-NEW')
  await wrapper.get('input[placeholder="例如 上海中心仓"]').setValue('新仓')
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
  await wrapper.get('form').trigger('submit')
  expect(wrapper.get('input[placeholder="例如 WH-001"]').attributes('disabled')).toBeDefined()
  expect(wrapper.findAll('button').find(b => b.text() === '取消')!.attributes('disabled')).toBeDefined()
  expect(wrapper.findAll('button').find(b => b.text() === '关闭表单')!.attributes('disabled')).toBeDefined()
  await router.push('/inventory/inbounds')
  expect(router.currentRoute.value.path).toBe('/inventory/warehouses')
  expect(window.confirm).not.toHaveBeenCalled()
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(true)
  finish({ warehouseId: 'w2' })
  await flushPromises()
  await router.push('/inventory/inbounds')
  expect(router.currentRoute.value.path).toBe('/inventory/inbounds')
})
it('blocks browser back for dirty drafts but does not prompt for same-panel filter URLs', async () => {
  const { wrapper, router } = await setup('/inventory')
  await router.push('/inventory/warehouses')
  await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '新建仓库')!.trigger('click')
  await wrapper.get('input[placeholder="例如 WH-001"]').setValue('WH-NEW')
  router.back()
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/inventory/warehouses')
  expect(window.confirm).toHaveBeenCalledTimes(1)
  await router.push('/inventory/warehouses?view=all')
  expect(router.currentRoute.value.query.view).toBe('all')
  expect(window.confirm).toHaveBeenCalledTimes(1)
})

const inbound = { inboundId: 'in-1', documentNo: 'IN-1', warehouseId: 'w1', warehouseName: '中心仓', status: 'DRAFT', revision: 1, reason: '采购', createdAt: '2026-09-29T00:00:00Z', createdBy: '库管', itemCount: 1, totalBaseUnits: 1, items: [] }
function inventoryRead(path: string) {
  if (path === '/warehouses') return { items: [{ warehouseId: 'w1', name: '中心仓', enabled: true, isDefault: true }] }
  if (path === '/inventory/inbounds/in-1') return inbound
  if (path.startsWith('/inventory/inbounds?')) return { items: [inbound], total: 1, page: 1, pageSize: 20 }
  return { items: [], total: 0, page: 1, pageSize: 20 }
}
it('discards an older detail read after leaving and reentering its panel', async () => {
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementation(async path => inventoryRead(path) as never)
  const { wrapper, router } = await setup('/inventory/inbounds')
  vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
  await wrapper.findAll('button').find(b => b.text() === '查看详情')!.trigger('click')
  await router.push('/inventory/outbounds')
  await router.push('/inventory/inbounds')
  finish(inbound)
  await flushPromises()
  expect(wrapper.text()).not.toContain('入库单明细 · IN-1')
  expect(wrapper.text()).not.toContain('正在读取入库单')
})
it('protects navigation while a stock confirmation dialog is awaiting a decision', async () => {
  const { ElMessageBox } = await import('element-plus')
  let decide!: (value: 'confirm') => void
  vi.spyOn(ElMessageBox, 'confirm').mockImplementation(() => new Promise(resolve => { decide = resolve }) as never)
  vi.mocked(api).mockImplementation(async path => inventoryRead(path) as never)
  const { wrapper, router } = await setup('/inventory/inbounds')
  await wrapper.findAll('button').find(b => b.text() === '查看详情')!.trigger('click')
  await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '确认入库')!.trigger('click')
  await router.push('/inventory/outbounds')
  expect(router.currentRoute.value.path).toBe('/inventory/inbounds')
  expect(api).not.toHaveBeenCalledWith(expect.stringContaining('/confirm'), expect.anything())
  decide('confirm')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/inventory/inbounds/in-1/confirm', expect.objectContaining({ method: 'POST' }))
  await router.push('/inventory/outbounds')
  expect(router.currentRoute.value.path).toBe('/inventory/outbounds')
})

it('selects current page rows and resets selection when querying again', async () => {
  vi.mocked(api).mockImplementation(async path => inventoryRead(path) as never)
  const { wrapper } = await setup('/inventory/inbounds')
  expect(wrapper.text()).toContain('已选 0 条')
  await wrapper.get('tbody input[type="checkbox"]').setValue(true)
  await flushPromises()
  expect(wrapper.text()).toContain('已选 1 条')
  expect(wrapper.text()).toContain('导出所选行 CSV')
  await wrapper.get('form.inventory-filters').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('已选 0 条')
  expect(api).toHaveBeenCalledWith(expect.stringContaining('pageSize=20'))
})

it('locks stale selection while loading and accepts a cleared date range', async () => {
  vi.mocked(api).mockImplementation(async path => inventoryRead(path) as never)
  const { wrapper } = await setup('/inventory/inbounds')
  await wrapper.get('tbody input[type="checkbox"]').setValue(true)
  expect(wrapper.text()).toContain('已选 1 条')
  let finish!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
  wrapper.findComponent({ name: 'ElDatePicker' }).vm.$emit('update:modelValue', null)
  await wrapper.get('form.inventory-filters').trigger('submit')
  expect((wrapper.get('tbody input[type="checkbox"]').element as HTMLInputElement).disabled).toBe(true)
  finish({ items: [inbound], total: 1, page: 1, pageSize: 20 })
  await flushPromises()
  expect(wrapper.text()).toContain('已选 0 条')
  expect((wrapper.get('tbody input[type="checkbox"]').element as HTMLInputElement).disabled).toBe(false)
})
