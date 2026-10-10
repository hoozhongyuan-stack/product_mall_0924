import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api, type Account } from '../../src/api'
import DashboardView from '../../src/views/DashboardView.vue'
import BusinessSummaryView from '../../src/views/BusinessSummaryView.vue'
import { router as applicationRouter } from '../../src/router'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const account: Account = { accountId: 'operator', loginName: 'operator', displayName: '运营', kind: 'OWNER', enabled: true, revision: 1, groupIds: [], permissionCodes: ['business.report.read', 'catalog.read', 'orders.read'] }
const metric = { paidOrderCount: 1, paidAmountFen: 100, pointsExchangeCount: 0, refundCount: 0, refundAmountFen: 0, netAmountFen: 100 }
const summary = { from: '2026-07-13', to: '2026-10-10', timeZone: 'Asia/Shanghai', totals: { ...metric, paidAmountFen: 9000 }, days: Array.from({ length: 90 }, (_, index) => ({ ...metric, date: new Date(Date.UTC(2026, 6, 13 + index)).toISOString().slice(0, 10) })) }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-10T04:00:00Z')); vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue(summary) })
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.useRealTimers() })
async function setup(component: typeof DashboardView | typeof BusinessSummaryView, permissions = account.permissionCodes, embedded = false) {
  const router = createRouter({ history: createMemoryHistory(), routes: applicationRouter.options.routes })
  await router.push('/')
  const wrapper = mount(component, { props: { account: { ...account, permissionCodes: permissions }, embedded }, global: { plugins: [router] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
describe('task-first workbench and concise business detail', () => {
  it('places permitted common tasks before reporting and keeps complete entries collapsed', async () => {
    const wrapper = await setup(DashboardView)
    expect(wrapper.get('[aria-label="常用工作"]').text()).toContain('商品管理')
    expect(wrapper.get('[aria-label="常用工作"]').text()).not.toContain('库存')
    expect(wrapper.html().indexOf('常用工作')).toBeLessThan(wrapper.html().indexOf('今日经营'))
    expect(wrapper.get('details.dashboard-links').attributes('open')).toBeUndefined()
  })
  it('never requests report data without report permission', async () => {
    const wrapper = await setup(DashboardView, ['catalog.read'])
    expect(api).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('商品管理')
    expect(wrapper.text()).not.toContain('今日经营')
  })
  it('shows today from the actual row instead of relabelling 90-day totals', async () => {
    const wrapper = await setup(BusinessSummaryView, account.permissionCodes, true)
    expect(wrapper.get('[aria-label="今日经营"]').text()).toContain('1.00')
    expect(wrapper.get('[aria-label="今日经营"]').text()).not.toContain('90.00')
    expect(wrapper.get('details.business-history').attributes('open')).toBeUndefined()
    expect(wrapper.get('[aria-label="区间经营合计"]').text()).toContain('90.00')
  })
  it('paginates latest first, preserves source order and totals, resets on query', async () => {
    const wrapper = await setup(BusinessSummaryView)
    expect(wrapper.findAll('tbody tr')).toHaveLength(10)
    expect(wrapper.findAll('tbody tr')[0].text()).toContain('2026-10-10')
    await wrapper.get('button[aria-label="下一页每日明细"]').trigger('click')
    expect(wrapper.findAll('tbody tr')[0].text()).toContain('2026-09-30')
    expect(summary.days[0].date).toBe('2026-07-13')
    expect(wrapper.get('[aria-label="区间经营合计"]').text()).toContain('90.00')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.findAll('tbody tr')[0].text()).toContain('2026-10-10')
  })
  it('does not fabricate zero when the response does not include today', async () => {
    vi.mocked(api).mockResolvedValue({ ...summary, days: summary.days.slice(0, -1) })
    const wrapper = await setup(BusinessSummaryView, account.permissionCodes, true)
    expect(wrapper.text()).toContain('当前数据未包含今日明细')
    expect(wrapper.find('[aria-label="今日经营"]').exists()).toBe(false)
  })
  it('uses the server timezone near midnight and preserves zero-activity days', async () => {
    vi.setSystemTime(new Date('2026-10-09T16:30:00Z'))
    vi.mocked(api).mockResolvedValue({ ...summary, days: [{ ...metric, paidOrderCount: 0, paidAmountFen: 0, netAmountFen: 0, date: '2026-10-10' }] })
    const wrapper = await setup(BusinessSummaryView, account.permissionCodes, true)
    expect(wrapper.get('[aria-label="今日经营"]').text()).toContain('0.00')
    expect(wrapper.text()).toContain('2026-10-10 · Asia/Shanghai')
  })
  it('shows empty reporting honestly and leaves all task access permission-based', async () => {
    const wrapper = await setup(DashboardView, [])
    expect(wrapper.text()).toContain('暂无常用业务权限')
    expect(wrapper.text()).toContain('当前没有业务操作权限')
    vi.mocked(api).mockResolvedValue({ ...summary, totals: { ...metric, paidOrderCount: 0 } })
    const report = await setup(BusinessSummaryView)
    expect(report.text()).toContain('当前区间暂无支付、退款或积分兑换记录')
    expect(report.find('tbody').exists()).toBe(false)
  })
  it('bounds the last page and preserves exact query filters while resetting paging', async () => {
    const wrapper = await setup(BusinessSummaryView)
    for (let page = 1; page < 9; page++) await wrapper.get('button[aria-label="下一页每日明细"]').trigger('click')
    expect(wrapper.get('button[aria-label="下一页每日明细"]').attributes('disabled')).toBeDefined()
    await wrapper.get('button[aria-label="上一页每日明细"]').trigger('click')
    expect(wrapper.text()).toContain('第 8 / 9 页')
    await wrapper.findAll('input')[0].setValue('2026-10-01')
    await wrapper.findAll('input')[1].setValue('2026-10-10')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(api).toHaveBeenLastCalledWith('/business-summary?from=2026-10-01&to=2026-10-10')
    await wrapper.findAll('button').find(button => button.text() === '近 90 天')!.trigger('click'); await flushPromises()
    expect(api).toHaveBeenLastCalledWith('/business-summary?')
    expect(wrapper.text()).toContain('第 1 / 9 页')
  })
  it('keeps errors and retry visible above collapsed history', async () => {
    vi.mocked(api).mockRejectedValue(new Error('经营数据暂不可用'))
    const wrapper = await setup(BusinessSummaryView, account.permissionCodes, true)
    expect(wrapper.get('[role="alert"]').text()).toContain('经营数据暂不可用')
    vi.mocked(api).mockResolvedValue(summary)
    await wrapper.get('button.text-button').trigger('click'); await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.get('[aria-label="今日经营"]').exists()).toBe(true)
  })
})
