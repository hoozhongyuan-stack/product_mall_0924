import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ElementPlus from 'element-plus'
import InventoryView from '../../src/views/InventoryView.vue'
import StocktakePanel from '../../src/views/inventory/StocktakePanel.vue'
import InventoryListFooter from '../../src/views/inventory/InventoryListFooter.vue'
import { api, type Account } from '../../src/api'
import { confirmAction } from '../../src/shared/confirm'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
vi.mock('../../src/shared/confirm', () => ({ confirmAction: vi.fn() }))

const account = { accountId: 'first-actor', permissionCodes: ['inventory.read'] } as Account
const balances = [
  { warehouseId: 'w', warehouseName: '中心仓', skuId: 'pieces', productName: '按件商品', baseUnit: '件', onHandBaseUnits: 480, reservedBaseUnits: 0, availableBaseUnits: 480, poolSkuCodes: ['PIECE'], poolAnchorSkuCode: 'PIECE' },
  { warehouseId: 'w', warehouseName: '中心仓', skuId: 'bottles', productName: '按瓶商品', baseUnit: '瓶', onHandBaseUnits: 419, reservedBaseUnits: 2, availableBaseUnits: 417, poolSkuCodes: ['BOTTLE', 'BOX'], poolAnchorSkuCode: 'BOTTLE' },
]
const stocktakes = [{ stocktakeId: 's1', documentNo: 'COUNT-1', warehouseName: '中心仓', itemCount: 2, createdBy: '盘点员', createdAt: '2026-10-11T00:00:00Z', status: 'COUNTING' }]
const wrappers: ReturnType<typeof mount>[] = []
let objectUrl: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  objectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:export')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.restoreAllMocks()
  vi.resetAllMocks()
})

async function setup(kind: 'balances' | 'stocktakes') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: InventoryView, meta: { inventoryTab: 'balances' } }] })
  await router.push('/')
  await router.isReady()
  vi.mocked(api).mockImplementation(async path => path === '/warehouses' ? { items: [] } as never : {
    items: kind === 'balances' ? balances : stocktakes, page: new URLSearchParams(String(path).split('?')[1]).get('page') === '2' ? 2 : 1, pageSize: 20, total: 40,
  } as never)
  const wrapper = kind === 'balances'
    ? mount(InventoryView, { props: { account }, global: { plugins: [router, ElementPlus] } })
    : mount(StocktakePanel, { props: { warehouses: [], canManage: false, canReview: false }, global: { plugins: [ElementPlus] } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.get('thead input[type="checkbox"]').setValue(true)
  return wrapper
}
async function beginExport(wrapper: ReturnType<typeof mount>) {
  let resolve!: (value: boolean) => void
  vi.mocked(confirmAction).mockImplementationOnce(() => new Promise<boolean>(done => { resolve = done }))
  await wrapper.findAll('button').find(button => button.text() === '导出所选行 CSV')!.trigger('click')
  expect(confirmAction).toHaveBeenCalledOnce()
  expect(objectUrl).not.toHaveBeenCalled()
  return resolve
}

describe.each(['balances', 'stocktakes'] as const)('%s export confirmation lifetime', kind => {
  it('does not download an old snapshot after the component unmounts', async () => {
    const wrapper = await setup(kind)
    const confirm = await beginExport(wrapper)
    wrapper.unmount()
    wrappers.splice(wrappers.indexOf(wrapper), 1)
    confirm(true)
    await flushPromises()
    expect(objectUrl).not.toHaveBeenCalled()
  })
  it('does not download after a page change clears the selection', async () => {
    const wrapper = await setup(kind)
    const confirm = await beginExport(wrapper)
    wrapper.getComponent(InventoryListFooter).vm.$emit('page', 2)
    await flushPromises()
    confirm(true)
    await flushPromises()
    expect(objectUrl).not.toHaveBeenCalled()
  })
  it('does not download when the confirmation is refused', async () => {
    const wrapper = await setup(kind)
    const confirm = await beginExport(wrapper)
    confirm(false)
    await flushPromises()
    expect(objectUrl).not.toHaveBeenCalled()
  })
  it('downloads the unchanged selection after confirmation and keeps its unit columns', async () => {
    const wrapper = await setup(kind)
    const confirm = await beginExport(wrapper)
    confirm(true)
    await flushPromises()
    expect(objectUrl).toHaveBeenCalledOnce()
    const blob = objectUrl.mock.calls[0][0] as Blob
    const csv = await blob.text()
    if (kind === 'balances') {
      expect(csv).toContain('"账面","锁定","可售","基础单位"')
      expect(csv).toContain('"480","0","480","件"')
      expect(csv).toContain('"419","2","417","瓶"')
      expect(csv).not.toContain('899')
    } else {
      expect(csv).toContain('"COUNT-1","中心仓","2","盘点员"')
      expect(csv).toContain('盘点中')
    }
  })
})

it.each(['account', 'permission'] as const)('refuses inventory export after %s changes during confirmation', async change => {
  const wrapper = await setup('balances')
  const confirm = await beginExport(wrapper)
  await wrapper.setProps({ account: { ...account, accountId: change === 'account' ? 'next-actor' : account.accountId, permissionCodes: change === 'permission' ? [] : account.permissionCodes } })
  confirm(true)
  await flushPromises()
  expect(objectUrl).not.toHaveBeenCalled()
})
