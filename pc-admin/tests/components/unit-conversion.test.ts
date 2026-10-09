import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import ProductCreateForm from '../../src/views/catalog/ProductCreateForm.vue'
import ProductSpecEditor from '../../src/views/catalog/ProductSpecEditor.vue'
import { api } from '../../src/api'
import type { ProductDetail } from '../../src/views/catalog/types'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
vi.mock('../../src/shared/confirm', () => ({ confirmAction: vi.fn().mockResolvedValue(true) }))
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.clearAllMocks() })
const button = (w: ReturnType<typeof mount>, text: string) => w.findAll('button').find(b => b.text() === text)!
const detail: ProductDetail = { productId: 'p', productNo: 'P', name: '酒', categoryId: 'c', fulfillmentKind: 'SHIP', redeemValidUntil: null, status: 'DRAFT', manuallyOffSale: false, descriptionHtml: '', productRevision: 1, mainImage: null, galleryImages: [], video: null,
  unitConversion: { axisKey: 'u', baseOptionKey: 'b', ratios: [{ optionKey: 'b', ratio: 1 }, { optionKey: 'x', ratio: 6 }] },
  specAxes: [{ id: 'u', name: '单位', sortOrder: 0, options: [{ id: 'b', value: '瓶', sortOrder: 0 }, { id: 'x', value: '箱', sortOrder: 1 }] }], skus: ['b', 'x'].map((id, index) => ({ skuId: id, skuCode: id, skuRevision: 1, productId: 'p', productRevision: 1, productNo: 'P', productName: '酒', productStatus: 'DRAFT', fulfillmentKind: 'SHIP', categoryId: 'c', specs: [], specOptionIds: [id], listPriceFen: 1000, gradePrices: [], saleStatus: 'OFF_SALE', unit: { baseUnit: '瓶', saleUnit: index ? '箱' : '瓶', ratio: index ? 6 : 1 } })) }
it('creates unit specifications and derives each row unit without repeated editing', async () => {
  vi.mocked(api).mockResolvedValue({})
  const w = mount(ProductCreateForm, { props: { categories: [{ id: 'r', parentId: null, name: '根', status: 'ACTIVE', sortOrder: 0, revision: 1 }, { id: 'c', parentId: 'r', name: '酒', status: 'ACTIVE', sortOrder: 0, revision: 1 }] }, global: { provide: { 'admin-account': ref({ permissionCodes: ['catalog.write'] }) }, stubs: { ProductMediaEditor: true, ProductDescriptionEditor: true } } }); wrappers.push(w)
  await w.get('[aria-label="开启多单位换算"]').setValue(true)
  await w.get('[aria-label="单位名称 1"]').setValue('瓶')
  await button(w, '添加单位').trigger('click')
  await w.get('[aria-label="单位名称 2"]').setValue('箱')
  await w.get('[aria-label="单位换算比 2"]').setValue(6)
  await button(w, '生成 SKU 组合').trigger('click')
  expect(w.findAll('.sku-edit-table tbody tr')).toHaveLength(2)
  expect(w.find('[aria-label="基本单位 瓶"]').exists()).toBe(false)
  expect(w.text()).toContain('1 箱＝6 瓶')
  await w.get('[placeholder="例如 PROD-001"]').setValue('P')
  await w.get('[placeholder="请输入商品名称"]').setValue('酒')
  await w.get('select').setValue('c')
  await w.get('[aria-label="SKU 编码 瓶"]').setValue('B')
  await w.get('[aria-label="日常价 B"]').setValue('10')
  await w.get('[aria-label="SKU 编码 箱"]').setValue('X')
  await w.get('[aria-label="日常价 X"]').setValue('55')
  await w.get('form').trigger('submit'); await flushPromises()
  const sent = JSON.parse(vi.mocked(api).mock.calls[0]![1]!.body as string)
  expect(sent.specAxes[0].name).toBe('单位')
  expect(sent.unitConversion.baseOptionKey).toBe(sent.specAxes[0].options[0].clientKey)
  expect(sent.skus.map((s: { unit: unknown }) => s.unit)).toEqual([{ baseUnit: '瓶', saleUnit: '瓶', ratio: 1 }, { baseUnit: '瓶', saleUnit: '箱', ratio: 6 }])
})
it('unit label and ratio edits preserve SKU IDs and invalidate preview', async () => {
  vi.mocked(api).mockResolvedValue({ previewToken: 't', retained: [], added: [], removed: [] })
  const w = mount(ProductSpecEditor, { props: { product: detail, grades: [], canEdit: true, basicDirty: false } }); wrappers.push(w)
  await w.get('[aria-label="单位名称 2"]').setValue('整箱')
  await w.get('[aria-label="单位换算比 2"]').setValue(12)
  expect(button(w, '核对规格变更影响').attributes('disabled')).toBeDefined()
  await button(w, '生成 / 更新 SKU 组合').trigger('click')
  expect(w.text()).toContain('1 整箱＝12 瓶')
  await button(w, '核对规格变更影响').trigger('click'); await flushPromises()
  const sent = JSON.parse(vi.mocked(api).mock.calls[0]![1]!.body as string)
  expect(sent.skus.map((s: { id: string }) => s.id)).toEqual(['b', 'x'])
  expect(sent.skus[1].unit).toEqual({ baseUnit: '瓶', saleUnit: '整箱', ratio: 12 })
  await w.get('[aria-label="单位换算比 2"]').setValue(6)
  expect(w.find('[aria-label="服务端变更预览"]').exists()).toBe(false)
})

it('other specifications combine with units and all same-unit rows derive the configured ratio', async () => {
  const w = mount(ProductSpecEditor, { props: { product: detail, grades: [], canEdit: true, basicDirty: false } }); wrappers.push(w)
  await button(w, '添加规格项').trigger('click')
  await w.get('[placeholder="例如 包装规格"]').setValue('度数')
  await w.get('[placeholder="例如 单瓶装"]').setValue('53°')
  await button(w, '添加规格值').trigger('click')
  await w.findAll('[placeholder="例如 单瓶装"]')[1]!.setValue('43°')
  await button(w, '生成 / 更新 SKU 组合').trigger('click')
  expect(w.findAll('.sku-edit-table tbody tr')).toHaveLength(4)
  expect(w.findAll('.sku-conversion-cell').map(cell => cell.text())).toEqual(['1 瓶＝1 瓶', '1 瓶＝1 瓶', '1 箱＝6 瓶', '1 箱＝6 瓶'])
  expect(button(w, '添加规格项').attributes('disabled')).toBeDefined()
  expect(api).not.toHaveBeenCalled()
})
it('invalid conversion blocks regeneration, preserves entered values and makes no request', async () => {
  const w = mount(ProductSpecEditor, { attachTo: document.body, props: { product: detail, grades: [], canEdit: true, basicDirty: false } }); wrappers.push(w)
  await w.get('[aria-label="单位换算比 2"]').setValue(1.5)
  await button(w, '生成 / 更新 SKU 组合').trigger('click'); await flushPromises()
  expect(w.text()).toContain('正整数换算比')
  expect((w.get('[aria-label="单位换算比 2"]').element as HTMLInputElement).value).toBe('1.5')
  expect(document.activeElement?.getAttribute('aria-label')).toBe('单位换算比 2')
  expect(api).not.toHaveBeenCalled()
})
it('view-only products show conversion configuration without allowing edits', () => {
  const w = mount(ProductSpecEditor, { props: { product: detail, grades: [], canEdit: false, basicDirty: false } }); wrappers.push(w)
  expect(w.get('[aria-label="开启多单位换算"]').attributes('disabled')).toBeDefined()
  expect(w.get('[aria-label="单位名称 2"]').attributes('disabled')).toBeDefined()
  expect(w.get('[aria-label="单位换算比 2"]').attributes('disabled')).toBeDefined()
  expect(w.find('[aria-label="单位 b"]').exists()).toBe(false)
  expect(api).not.toHaveBeenCalled()
})
