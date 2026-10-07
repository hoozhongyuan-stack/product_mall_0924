import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import ProductCreateForm from '../../src/views/catalog/ProductCreateForm.vue'
import { api } from '../../src/api'
import { confirmAction } from '../../src/shared/confirm'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
vi.mock('../../src/shared/confirm', () => ({ confirmAction: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.clearAllMocks() })
function setup() {
  const wrapper = mount(ProductCreateForm, { attachTo: document.body, props: { categories: [{ id: 'root', parentId: null, name: '酒', status: 'ACTIVE', sortOrder: 0, revision: 1 }, { id: 'leaf', parentId: 'root', name: '白酒', status: 'ACTIVE', sortOrder: 0, revision: 1 }], grades: [{ id: 'gold', code: 'GOLD', name: '金卡', rank: 1, enabled: true }] }, global: { provide: { 'admin-account': ref({ permissionCodes: ['catalog.write'] }) }, stubs: { ProductMediaEditor: true, ProductDescriptionEditor: true } } })
  wrappers.push(wrapper)
  return wrapper
}
function button(wrapper: ReturnType<typeof mount>, text: string) { return wrapper.findAll('button').find(b => b.text() === text)! }
async function fillBasic(wrapper: ReturnType<typeof mount>) {
  await wrapper.get('input[placeholder="例如 PROD-001"]').setValue('P1')
  await wrapper.get('input[placeholder="请输入商品名称"]').setValue('测试商品')
  await wrapper.get('select').setValue('leaf')
}
async function createOptions(wrapper: ReturnType<typeof mount>) {
  await button(wrapper, '添加规格项').trigger('click')
  await wrapper.get('input[placeholder="例如 容量"]').setValue('包装')
  await wrapper.get('input[placeholder="例如 500ml"]').setValue('单瓶')
  await button(wrapper, '添加规格值').trigger('click')
  await wrapper.findAll('input[placeholder="例如 500ml"]')[1]!.setValue('整箱')
  await button(wrapper, '生成 SKU 组合').trigger('click')
  await wrapper.get('[aria-label="SKU 编码 单瓶"]').setValue('SINGLE')
  await wrapper.get('[aria-label="日常价 SINGLE"]').setValue('10.00')
  await wrapper.get('[aria-label="SKU 编码 整箱"]').setValue('BOX')
  await wrapper.get('[aria-label="日常价 BOX"]').setValue('50.00')
}
describe('product creation SKU identity and payload', () => {
  it('keeps retained prices against stable option IDs when earlier values are removed', async () => {
    vi.mocked(confirmAction).mockResolvedValue(true)
    const wrapper = setup()
    await createOptions(wrapper)
    await wrapper.get('[aria-label="移除规格值 单瓶"]').trigger('click')
    await button(wrapper, '重新生成 SKU 组合').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect((wrapper.get('[aria-label="日常价 BOX"]').element as HTMLInputElement).value).toBe('50.00')
    expect(confirmAction).toHaveBeenCalledWith(expect.stringContaining('1 个 SKU'))
  })
  it('keeps all drafts if removal confirmation is cancelled', async () => {
    vi.mocked(confirmAction).mockResolvedValue(false)
    const wrapper = setup()
    await createOptions(wrapper)
    await wrapper.get('[aria-label="移除规格值 单瓶"]').trigger('click')
    await button(wrapper, '重新生成 SKU 组合').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect((wrapper.get('[aria-label="日常价 SINGLE"]').element as HTMLInputElement).value).toBe('10.00')
    expect(wrapper.text()).toContain('请重新生成组合并核对后保存')
  })
  it('submits dynamic grade prices with the existing server contract', async () => {
    vi.mocked(api).mockResolvedValue({})
    const wrapper = setup()
    await fillBasic(wrapper)
    await button(wrapper, '生成 SKU 组合').trigger('click')
    await wrapper.get('[aria-label="SKU 编码 默认规格"]').setValue('DEFAULT')
    await wrapper.get('[aria-label="日常价 DEFAULT"]').setValue('10.00')
    await wrapper.get('[aria-label="金卡价 DEFAULT"]').setValue('9.99')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const sent = JSON.parse(vi.mocked(api).mock.calls[0]![1]!.body as string)
    expect(sent.skus[0]).toMatchObject({ skuCode: 'DEFAULT', listPriceFen: 1000, gradePrices: [{ gradeId: 'gold', priceFen: 999 }], saleStatus: 'OFF_SALE', unit: { baseUnit: '件', saleUnit: '件', ratio: 1 } })
    expect(sent.specAxes).toEqual([])
    expect(wrapper.emitted('created')).toHaveLength(1)
  })
  it('focuses an invalid duplicate SKU code and preserves entered prices', async () => {
    const wrapper = setup()
    await fillBasic(wrapper)
    await createOptions(wrapper)
    await wrapper.get('[aria-label="SKU 编码 整箱"]').setValue('single')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.findAll('[aria-invalid="true"]')).toHaveLength(2)
    expect(document.activeElement?.getAttribute('aria-label')).toBe('SKU 编码 单瓶')
    expect(wrapper.text()).toContain('不重复的编码')
    expect(api).not.toHaveBeenCalled()
  })
})
