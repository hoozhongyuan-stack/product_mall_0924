import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import InventorySkuPicker from '../../src/views/inventory/InventorySkuPicker.vue'
import { api } from '../../src/api'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const sku = { skuId: 'sku1', skuCode: 'SKU-RED', productNo: 'P1', productName: '同名商品', specs: [{ name: '颜色', value: '红色' }, { name: '容量', value: '500mL' }], mainImage: null, baseUnit: '瓶', saleUnit: '箱', ratio: 6, unitVersionId: 'u1', warehouseStock: { warehouseId: 'w1', onHandBaseUnits: 20, reservedBaseUnits: 3, availableBaseUnits: 17 } }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.mocked(api).mockReset() })
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); document.body.innerHTML = '' })
function setup(props = {}) {
  const wrapper = mount(InventorySkuPicker, { props: { warehouseId: 'w1', modelValue: null, ...props }, attachTo: document.body, global: { plugins: [ElementPlus], stubs: { teleport: true, transition: false } } })
  wrappers.push(wrapper)
  return wrapper
}
async function open(wrapper: ReturnType<typeof setup>) {
  await wrapper.findAll('button').find(b => b.text() === '选择商品 / SKU')!.trigger('click')
  await flushPromises()
}
it('displays ordered specs, codes, warehouse quantities and emits the chosen identity', async () => {
  vi.mocked(api).mockResolvedValue({ items: [sku], total: 1, page: 1, pageSize: 10 } as never)
  const wrapper = setup()
  await open(wrapper)
  expect(wrapper.text()).toContain('颜色：红色')
  expect(wrapper.text()).toContain('容量：500mL')
  expect(wrapper.text()).toContain('SKU-RED')
  expect(wrapper.text()).toContain('可售 17 瓶')
  await wrapper.findAll('button').find(b => b.text() === '选用')!.trigger('click')
  expect(wrapper.emitted('select')?.[0]).toEqual([sku])
})
it('searches the whole result set, pages remotely, and disables already selected SKU rows', async () => {
  vi.mocked(api).mockResolvedValue({ items: [sku], total: 31, page: 1, pageSize: 10 } as never)
  const wrapper = setup({ excludedIds: ['sku1'] })
  await open(wrapper)
  expect(wrapper.findAll('button').find(b => b.text() === '已添加')!.attributes('disabled')).toBeDefined()
  await wrapper.get('input[placeholder="商品名称、规格、商品编号或 SKU 编码"]').setValue(' 红色 ')
  await wrapper.get('.inventory-sku-search').trigger('submit')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/inventory/skus?page=1&pageSize=10&warehouseId=w1&keyword=%E7%BA%A2%E8%89%B2')
  await wrapper.get('button.btn-next').trigger('click')
  await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/inventory/skus?page=2&pageSize=10&warehouseId=w1&keyword=%E7%BA%A2%E8%89%B2')
})
it('ignores stale warehouse results and retries errors without presenting failure as zero stock', async () => {
  let resolveOld!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }) as never)
  const wrapper = setup()
  await open(wrapper)
  vi.mocked(api).mockRejectedValueOnce(new Error('读取失败'))
  await wrapper.setProps({ warehouseId: 'w2' })
  await flushPromises()
  resolveOld({ items: [sku], total: 1, page: 1, pageSize: 10 })
  await flushPromises()
  expect(wrapper.text()).toContain('读取失败')
  expect(wrapper.text()).not.toContain('可售 17')
  vi.mocked(api).mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 10 } as never)
  await wrapper.findAll('button').find(b => b.text() === '重试')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('没有匹配的商品')
})
it('retains selected identity after search and clears failed thumbnails when target changes', async () => {
  const wrapper = setup({ modelValue: { ...sku, mainImage: { assetId: 'a1', adminUrl: '/image1' } } })
  expect(wrapper.text()).toContain('SKU-RED')
  await wrapper.get('img').trigger('error')
  expect(wrapper.text()).toContain('暂无图片')
  await wrapper.setProps({ modelValue: { ...sku, mainImage: { assetId: 'a2', adminUrl: '/image2' } } })
  expect(wrapper.get('img').attributes('src')).toBe('/image2')
})
