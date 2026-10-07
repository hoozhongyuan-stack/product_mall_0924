import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import ProductCreateForm from '../../src/views/catalog/ProductCreateForm.vue'
import ProductSpecEditor from '../../src/views/catalog/ProductSpecEditor.vue'
import { confirmAction } from '../../src/shared/confirm'
import type { ProductDetail } from '../../src/views/catalog/types'
vi.mock('../../src/shared/confirm', () => ({ confirmAction: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.clearAllMocks() })
const product: ProductDetail = { productId: 'p1', productNo: 'P1', name: '草稿', categoryId: 'c1', fulfillmentKind: 'SHIP', redeemValidUntil: null, status: 'DRAFT', manuallyOffSale: false, descriptionHtml: '', productRevision: 1, mainImage: null, galleryImages: [], video: null, specAxes: [], skus: [] }
function button(wrapper: ReturnType<typeof mount>, text: string) { return wrapper.findAll('button').find(b => b.text() === text)! }
describe.each(['create', 'edit'] as const)('protects unit-only SKU drafts in %s form', form => {
  it.each(['基本单位', '销售单位', '换算比'])('confirms removal when only %s changed and preserves input on cancel', async field => {
    vi.mocked(confirmAction).mockResolvedValue(false)
    const wrapper = form === 'create'
      ? mount(ProductCreateForm, { props: { categories: [] }, global: { provide: { 'admin-account': ref({ permissionCodes: ['catalog.write'] }) }, stubs: { ProductMediaEditor: true, ProductDescriptionEditor: true } } })
      : mount(ProductSpecEditor, { props: { product, grades: [], canEdit: true, basicDirty: false } })
    wrappers.push(wrapper)
    await button(wrapper, form === 'create' ? '生成 SKU 组合' : '生成 / 更新 SKU 组合').trigger('click')
    const target = wrapper.get(`[aria-label="${field} 默认规格"]`)
    await target.setValue(field === '换算比' ? '6' : '箱')
    await button(wrapper, '添加规格项').trigger('click')
    await wrapper.get(`input[placeholder="${form === 'create' ? '例如 容量' : '例如 包装规格'}"]`).setValue('包装')
    await wrapper.get(`input[placeholder="${form === 'create' ? '例如 500ml' : '例如 单瓶装'}"]`).setValue('单瓶')
    await button(wrapper, form === 'create' ? '重新生成 SKU 组合' : '生成 / 更新 SKU 组合').trigger('click')
    await flushPromises()
    expect(confirmAction).toHaveBeenCalledWith(expect.stringContaining('1 个'))
    expect((wrapper.get(`[aria-label="${field} 默认规格"]`).element as HTMLInputElement).value).toBe(field === '换算比' ? '6' : '箱')
  })
})
