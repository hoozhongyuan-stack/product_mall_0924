import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessageBox } from 'element-plus'
import StocktakePanel from '../../src/views/inventory/StocktakePanel.vue'
import { api } from '../../src/api'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.mocked(api).mockReset() })
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
it.each([
  ['COUNTING', '录入实盘', '提交差异审核', 'confirm'],
  ['PENDING_REVIEW', '审核差异', '审核并生成调整流水', 'confirm'],
  ['PENDING_REVIEW', '审核差异', '退回补充', 'prompt'],
] as const)('emits busy during %s decision %s, and releases it on cancel', async (status, openLabel, actionLabel, method) => {
  let cancel!: (reason: string) => void
  vi.spyOn(ElMessageBox, method).mockImplementation(() => new Promise((_, reject) => { cancel = reject }) as never)
  const detail = { stocktakeId: 's1', documentNo: 'COUNT1', warehouseId: 'w1', warehouseName: '中心仓', status, revision: 1,
    createdAt: '2026-09-29T00:00:00Z', itemCount: 1,
    items: [{ skuId: 'sku', skuCode: 'CODE', productName: '商品', baseUnit: '件', countedBaseUnits: 0, bookAtStartBaseUnits: 0, bookAtSubmitBaseUnits: 0, currentBookBaseUnits: 0, currentReservedBaseUnits: 0, reservedAtSubmitBaseUnits: 0, deltaBaseUnits: 0, bookChanged: false, reason: '' }] }
  vi.mocked(api).mockImplementation(async path => path.endsWith('/s1') ? detail as never : { items: [detail], page: 1, pageSize: 20, total: 1 } as never)
  const wrapper = mount(StocktakePanel, { props: { warehouses: [], canManage: true, canReview: true }, global: { plugins: [ElementPlus] } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === openLabel)!.trigger('click')
  await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === actionLabel)!.trigger('click')
  await flushPromises()
  expect(wrapper.emitted('busy')?.at(-1)).toEqual([true])
  expect(vi.mocked(api).mock.calls.every(([, init]) => !init?.method)).toBe(true)
  cancel('cancel')
  await flushPromises()
  expect(wrapper.emitted('busy')?.at(-1)).toEqual([false])
  expect(vi.mocked(api).mock.calls.every(([, init]) => !init?.method)).toBe(true)
})
