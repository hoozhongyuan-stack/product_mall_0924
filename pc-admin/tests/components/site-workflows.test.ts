import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import DashboardView from '../../src/views/DashboardView.vue'
import OrdersView from '../../src/views/OrdersView.vue'
import { router as applicationRouter } from '../../src/router'
import { api, type Account } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const account: Account = { accountId: 'site-test', loginName: 'operator', displayName: '运营人员', kind: 'OWNER', enabled: true, revision: 1, groupIds: [], permissionCodes: [] }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => vi.mocked(api).mockReset())
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
async function render(component: typeof DashboardView | typeof OrdersView, permissionCodes: string[]) {
  const router = createRouter({ history: createMemoryHistory(), routes: applicationRouter.options.routes })
  await router.push('/')
  const wrapper = mount(component, { props: { account: { ...account, permissionCodes } }, global: { plugins: [router], stubs: { 'el-dialog': true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
it('shows only authorized business shortcuts and never requests financial data without report access', async () => {
  const wrapper = await render(DashboardView, ['catalog.read', 'asset.upload'])
  expect(wrapper.find('a[href="/catalog"]').exists()).toBe(true)
  expect(wrapper.find('a[href="/assets"]').exists()).toBe(true)
  expect(wrapper.find('a[href="/orders"]').exists()).toBe(false)
  expect(api).not.toHaveBeenCalled()
})
it('uses the real report on the workbench and supports retry after failure', async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error('暂时无法读取经营数据')).mockResolvedValue({ from: '2026-09-01', to: '2026-09-28', timeZone: 'Asia/Shanghai', totals: { paidOrderCount: 3, paidAmountFen: 12345, refundCount: 0, refundAmountFen: 0, netAmountFen: 12345, pointsExchangeCount: 1 }, days: [] } as never)
  const wrapper = await render(DashboardView, ['business.report.read'])
  expect(wrapper.get('[role="alert"]').text()).toContain('暂时无法读取经营数据')
  await wrapper.get('[role="alert"] button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('123.45')
  expect(api).toHaveBeenLastCalledWith('/business-summary?')
  expect(wrapper.findAll('h1')).toHaveLength(1)
})
it('submits fulfillment and search to server, resets all filters and pagination together', async () => {
  vi.mocked(api).mockResolvedValue({ items: [], total: 60, pageSize: 20 } as never)
  const wrapper = await render(OrdersView, ['order.read'])
  await wrapper.get('input[placeholder="搜索订单号"]').setValue('  M2026001  ')
  await wrapper.get('select[aria-label="履约待办"]').setValue('WAITING_SHIPMENT')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/orders?page=1&pageSize=20&fulfillment=WAITING_SHIPMENT&search=M2026001')
  await wrapper.findAll('button').find(button => button.text() === '下一页')!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/orders?page=2&pageSize=20&fulfillment=WAITING_SHIPMENT&search=M2026001')
  await wrapper.findAll('button').find(button => button.text() === '重置')!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/orders?page=1&pageSize=20')
  expect((wrapper.get('input[placeholder="搜索订单号"]').element as HTMLInputElement).value).toBe('')
})

it('shows permission guidance for an empty account and removes report data after permissions change', async () => {
  const empty = await render(DashboardView, [])
  expect(empty.text()).toContain('当前没有业务操作权限')
  expect(api).not.toHaveBeenCalled()
  vi.mocked(api).mockResolvedValue({ from: '2026-09-01', to: '2026-09-28', timeZone: 'Asia/Shanghai', totals: { paidOrderCount: 0, paidAmountFen: 0, refundCount: 0, refundAmountFen: 0, netAmountFen: 0, pointsExchangeCount: 0 }, days: [] } as never)
  const wrapper = await render(DashboardView, ['business.report.read'])
  expect(wrapper.text()).toContain('当前区间暂无支付')
  await wrapper.setProps({ account })
  await flushPromises()
  expect(wrapper.find('[aria-label="区间经营合计"]').exists()).toBe(false)
  expect(wrapper.find('a[href="/business/summary"]').exists()).toBe(false)
})
