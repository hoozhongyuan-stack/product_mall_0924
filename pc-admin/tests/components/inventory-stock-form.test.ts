import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import StockDocumentForm from '../../src/views/inventory/StockDocumentForm.vue'
import InventorySkuPicker from '../../src/views/inventory/InventorySkuPicker.vue'
import { api } from '../../src/api'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const sku = { skuId: 's1', skuCode: 'CODE', productName: '商品', productNo: 'P1', specs: [{ name: '颜色', value: '红' }], mainImage: null, baseUnit: '瓶', saleUnit: '箱', ratio: 6, unitVersionId: 'u1', warehouseStock: { warehouseId: 'w1', onHandBaseUnits: 20, reservedBaseUnits: 2, availableBaseUnits: 18 } }
const warehouse = { warehouseId: 'w1', name: '中心仓', code: 'MAIN', enabled: true, isDefault: true }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.mocked(api).mockResolvedValue({ items: [{ onHandBaseUnits: 20, reservedBaseUnits: 2, availableBaseUnits: 18 }], total: 1, page: 1, pageSize: 1 } as never) })
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.mocked(api).mockReset() })
function setup(props = {}) {
  const wrapper = mount(StockDocumentForm, { props: { kind: 'outbound', warehouses: [warehouse], saving: false, error: '', ...props }, global: { plugins: [ElementPlus], stubs: { InventorySkuPicker: true } } })
  wrappers.push(wrapper)
  return wrapper
}
async function choose(wrapper: ReturnType<typeof setup>, value = sku, index = 0) {
  wrapper.findAllComponents(InventorySkuPicker)[index]!.vm.$emit('select', value)
  await flushPromises()
}
it('uses the common picker, submits existing contract and changes stock only after later confirmation', async () => {
  const wrapper = setup()
  await choose(wrapper)
  await wrapper.get('input[placeholder="正整数"]').setValue('2')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual({ warehouseId: 'w1', reason: 'DAMAGE', note: '', items: [{ skuId: 's1', quantity: 2, unit: 'BASE' }] })
  expect(api).toHaveBeenCalledWith(expect.stringContaining('/inventory/balances?'))
  expect(wrapper.text()).toContain('账面 20')
  expect(wrapper.text()).toContain('锁定 2')
})
it('shows warehouse references for inbound too and rejects duplicate SKU rows', async () => {
  const wrapper = setup({ kind: 'inbound' })
  await choose(wrapper)
  await wrapper.get('input[placeholder="例如 采购入库"]').setValue('采购')
  await wrapper.get('input[placeholder="正整数"]').setValue('1')
  await wrapper.findAll('button').find(b => b.text().includes('添加商品行'))!.trigger('click')
  expect(wrapper.findAllComponents(InventorySkuPicker)[1]!.props('excludedIds')).toEqual(['s1'])
  await choose(wrapper, sku, 1)
  await wrapper.findAll('input[placeholder="正整数"]')[1]!.setValue('2')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.text()).toContain('同一 SKU 只能填写一行')
  expect(wrapper.emitted('submit')).toBeUndefined()
  expect(wrapper.text()).toContain('账面 20')
})
it('rejects fractional quantities and preserves the same request key for an unchanged retry', async () => {
  const wrapper = setup()
  await choose(wrapper)
  await wrapper.get('input[placeholder="正整数"]').setValue('1.5')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')).toBeUndefined()
  await wrapper.get('input[placeholder="正整数"]').setValue('3')
  await wrapper.get('form').trigger('submit')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')?.[0]?.[1]).toBe(wrapper.emitted('submit')?.[1]?.[1])
  await wrapper.get('input[placeholder="正整数"]').setValue('4')
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')?.[2]?.[1]).not.toBe(wrapper.emitted('submit')?.[0]?.[1])
})
it('blocks picker and form mutation while saving and presents failed inventory as unknown', async () => {
  vi.mocked(api).mockRejectedValue(new Error('库存读取失败'))
  const wrapper = setup()
  await choose(wrapper)
  expect(wrapper.text()).toContain('库存读取失败')
  expect(wrapper.text()).not.toContain('当前可售：0')
  await wrapper.setProps({ saving: true })
  expect(wrapper.findComponent(InventorySkuPicker).props('disabled')).toBe(true)
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('submit')).toBeUndefined()
})
