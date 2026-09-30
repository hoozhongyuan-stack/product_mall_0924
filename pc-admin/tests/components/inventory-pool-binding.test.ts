import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import InventoryPoolBindingPanel from '../../src/views/inventory/InventoryPoolBindingPanel.vue'
import { api } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const row = { productId: 'product-1', productRevision: 1, productNo: 'P-1', name: '葡萄酒', categoryId: 'c', fulfillmentKind: 'SHIP', status: 'DRAFT', mainImage: null, minListPriceFen: 1000, maxListPriceFen: 2000, skuCount: 2, onSaleSkuCount: 0, matchedSkuIds: [] }
const bindings = [
  { skuId: 'source', skuCode: 'BOTTLE-6', skuRevision: 4, baseUnit: '瓶', poolId: null, anchorSkuId: 'source', poolBaseUnit: null, shared: false, poolAnchorSkuCode: 'BOTTLE-6' },
  { skuId: 'anchor', skuCode: 'BOTTLE-1', skuRevision: 2, baseUnit: '瓶', poolId: null, anchorSkuId: 'anchor', poolBaseUnit: null, shared: false, poolAnchorSkuCode: 'BOTTLE-1' },
]
beforeEach(() => {
  vi.mocked(api).mockReset()
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.startsWith('/inventory/pool-product-options?')) return { items: [row] } as never
    if (path.startsWith('/inventory/pool-bindings?')) return { items: bindings } as never
    if (path === '/inventory/pool-bindings') return { skuId: 'source', poolId: 'pool-1', anchorSkuId: 'anchor', changed: true } as never
    throw new Error(`unexpected request ${path}`)
  })
})

describe('inventory pool binding', () => {
  it('searches a product and shows source, target, and no automatic balance merge', async () => {
    const wrapper = mount(InventoryPoolBindingPanel)
    await wrapper.get('input[placeholder="输入商品名称或编码"]').setValue('葡萄酒')
    await wrapper.get('.inventory-pool-search').trigger('submit'); await flushPromises()
    await wrapper.get('.inventory-pool-products button').trigger('click'); await flushPromises()
    expect(wrapper.get('.inventory-pool-bindings').text()).toContain('BOTTLE-6')
    expect(wrapper.get('.inventory-pool-bindings').text()).toContain('BOTTLE-1')
    expect(wrapper.text()).toContain('绑定不会自动合并任何余额')
    expect(vi.mocked(api).mock.calls.some(([path]) => path.startsWith('/inventory/pool-product-options?keyword=%E8%91%A1%E8%90%84%E9%85%92'))).toBe(true)
    expect(vi.mocked(api).mock.calls.find(([path]) => path.startsWith('/inventory/pool-bindings?'))?.[0]).toContain('productId=product-1')
    wrapper.unmount()
  })
  it('uses anchorSkuId and source revision when target pool has not yet been created', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
    const wrapper = mount(InventoryPoolBindingPanel)
    await wrapper.get('.inventory-pool-search').trigger('submit'); await flushPromises()
    await wrapper.get('.inventory-pool-products button').trigger('click'); await flushPromises()
    await wrapper.findAll('.inventory-pool-form select')[0]!.setValue('source')
    await wrapper.findAll('.inventory-pool-form select')[1]!.setValue('anchor')
    await wrapper.get('.inventory-pool-form').trigger('submit'); await flushPromises()
    const call = vi.mocked(api).mock.calls.find(([path, init]) => path === '/inventory/pool-bindings' && init?.method === 'POST')!
    expect(JSON.parse(call[1]!.body as string)).toEqual({ skuId: 'source', anchorSkuId: 'anchor', expectedSkuRevision: 4 })
    wrapper.unmount()
  })
  it('shows a server rejection without claiming the balance or binding changed', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm')
    const previous = vi.mocked(api).getMockImplementation()!
    vi.mocked(api).mockImplementation((path, init) => path === '/inventory/pool-bindings' && init?.method === 'POST'
      ? Promise.reject(new Error('源 SKU 已有库存流水，禁止绑定。')) : previous(path, init))
    const wrapper = mount(InventoryPoolBindingPanel)
    await wrapper.get('.inventory-pool-search').trigger('submit'); await flushPromises()
    await wrapper.get('.inventory-pool-products button').trigger('click'); await flushPromises()
    await wrapper.findAll('.inventory-pool-form select')[0]!.setValue('source')
    await wrapper.findAll('.inventory-pool-form select')[1]!.setValue('anchor')
    await wrapper.get('.inventory-pool-form').trigger('submit'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('源 SKU 已有库存流水，禁止绑定。')
    expect(wrapper.get('.inventory-pool-bindings').text()).toContain('尚未建立')
    wrapper.unmount()
  })
})
