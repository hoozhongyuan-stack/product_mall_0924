import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { EditorContent } from '@tiptap/vue-3'
import ProductManagement from '../../src/views/catalog/ProductManagement.vue'
import { api, type Account } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const account: Account = { accountId: 'pilot', loginName: 'operator', displayName: '运营', kind: 'OWNER', enabled: true, revision: 1, groupIds: [], permissionCodes: ['catalog.read', 'catalog.write', 'sku.status.write', 'sku.price.write', 'sku.unit.write'] }
const categories = [{ id: 'root', parentId: null, name: '酒水', status: 'ACTIVE' }, { id: 'leaf', parentId: 'root', name: '葡萄酒', status: 'ACTIVE' }]
const sku = { skuId: 'sku1', skuCode: 'SKU-1', skuRevision: 3, productId: 'p1', productRevision: 4, productNo: 'P-1', productName: '测试商品', categoryId: 'leaf', fulfillmentKind: 'SHIP', specs: [], listPriceFen: 888, gradePrices: [], productStatus: 'DRAFT', saleStatus: 'OFF_SALE', unit: { baseUnit: '瓶', saleUnit: '瓶', ratio: 1 } }
const product = { productId: 'p1', productNo: 'P-1', name: '测试商品', categoryId: 'leaf', fulfillmentKind: 'SHIP', redeemValidUntil: null, status: 'DRAFT', descriptionHtml: '', productRevision: 4, mainImage: null, galleryImages: [], video: null, specAxes: [], skus: [{ ...sku, specOptionIds: [] }] }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => {
  vi.stubGlobal('confirm', vi.fn(() => false))
  vi.mocked(api).mockReset()
  vi.mocked(api).mockImplementation(async (path, init) => {
    if (path === '/categories') return categories as never
    if (path === '/member-grades') return [] as never
    if (path.startsWith('/product-rows')) return { rows: [{ productId: 'p1', productRevision: 4, productNo: 'P-1', name: '测试商品', categoryId: 'leaf', fulfillmentKind: 'SHIP', status: 'DRAFT', mainImage: null, minListPriceFen: 888, maxListPriceFen: 888, skuCount: 1, onSaleSkuCount: 0, matchedSkuIds: [] }], page: 1, pageSize: 20, total: 1 } as never
    if (path.startsWith('/sku-rows')) return { rows: [sku], page: 1, pageSize: 20, total: 1 } as never
    if (path === '/products/p1') return (init?.method ? { ...product, name: '新的商品名', productRevision: 5 } : product) as never
    throw new Error(`unexpected request ${path}`)
  })
})
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
async function setup() {
  const wrapper = mount(ProductManagement, { attachTo: document.body, props: { account }, global: { provide: { 'admin-account': ref(account) }, stubs: { AssetPicker: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(b => b.text() === text)!

describe('focused product workspace', () => {
  it('pages by SPU and expands SKU details without treating the SKU as a product row', async () => {
    const wrapper = await setup()
    expect(vi.mocked(api).mock.calls.some(([path]) => path.startsWith('/product-rows?'))).toBe(true)
    expect(wrapper.get('.catalog-pagination').text()).toContain('共 1 个商品')
    expect(wrapper.findAll('.catalog-product-table tbody > tr')).toHaveLength(1)
    await button(wrapper, '展开规格').trigger('click'); await flushPromises()
    expect(wrapper.get('.catalog-sku-card').text()).toContain('SKU-1')
    expect(wrapper.findAll('.catalog-product-table tbody > tr')).toHaveLength(2)
    expect(vi.mocked(api).mock.calls.some(([path]) => path === '/products/p1')).toBe(true)
  })
  it('keeps SPU selection scoped to product actions and SKU batches in their own view', async () => {
    const wrapper = await setup()
    await wrapper.get('input[aria-label="选择商品 P-1"]').setValue(true)
    expect(wrapper.text()).toContain('已选 1 个商品')
    expect(button(wrapper, '导出所选商品').attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('button').some(b => b.text() === '批量下架 SKU')).toBe(false)
    await button(wrapper, 'SKU 批量管理').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('已选 0 个 SKU')
    expect(wrapper.findAll('button').some(b => b.text() === '批量下架 SKU')).toBe(true)
  })
  it('previews category changes by selected SPU IDs without loading SKU details', async () => {
    const previous = vi.mocked(api).getMockImplementation()!
    vi.mocked(api).mockImplementation((path, init) => path === '/products/batch-category/preview'
      ? Promise.resolve({ previewToken: 'token', productCount: 1, skuCount: 1, otherSkuCount: 0,
        items: [{ productId: 'p1', productNo: 'P-1', selectedSkuCount: 1, totalSkuCount: 1,
          productRevision: 4, canChange: true }] }) : previous(path, init))
    const wrapper = await setup()
    await wrapper.get('input[aria-label="选择商品 P-1"]').setValue(true)
    await button(wrapper, '批量调整分类').trigger('click')
    await wrapper.get('.catalog-batch-category select').setValue('leaf')
    await button(wrapper, '核对影响范围').trigger('click'); await flushPromises()
    const call = vi.mocked(api).mock.calls.find(([path]) => path === '/products/batch-category/preview')!
    expect(JSON.parse(call[1]!.body as string)).toEqual({ productIds: ['p1'], categoryId: 'leaf' })
    expect(vi.mocked(api).mock.calls.some(([path]) => path === '/products/p1')).toBe(false)
    expect(wrapper.get('.catalog-impact-summary').text()).toContain('1 个商品')
  })
  it('requires explicit SKU selection before first SPU publish and sends one product revision', async () => {
    const previous = vi.mocked(api).getMockImplementation()!
    vi.mocked(api).mockImplementation((path, init) => {
      if (path === '/products/batch-sale-status/preview') {
        const payload = JSON.parse(String(init?.body))
        return Promise.resolve({ items: [{ productId: 'p1', productNo: 'P-1', productRevision: 4,
          status: 'DRAFT', skuCount: 1, onSaleSkuCount: 0,
          skuOptions: [{ skuId: 'sku1', skuCode: 'SKU-1', hasUnit: true }],
          canChange: payload.items[0].initialSkuIds.length > 0,
          reason: payload.items[0].initialSkuIds.length ? undefined : '请先选择 SKU' }] })
      }
      if (path === '/products/batch-sale-status') return Promise.resolve({
        results: [{ id: 'p1', success: true, revision: 5 }], successCount: 1, failedCount: 0,
      })
      return previous(path, init)
    })
    const wrapper = await setup()
    await button(wrapper, '上架商品').trigger('click'); await flushPromises()
    expect((button(wrapper, '确认执行').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('.catalog-batch-preview input[type="checkbox"]').setValue(true)
    await button(wrapper, '核对影响范围').trigger('click'); await flushPromises()
    expect((button(wrapper, '确认执行').element as HTMLButtonElement).disabled).toBe(false)
    await button(wrapper, '确认执行').trigger('click'); await flushPromises()
    const sent = vi.mocked(api).mock.calls.find(([path]) => path === '/products/batch-sale-status')!
    expect(JSON.parse(String(sent[1]?.body))).toEqual({ saleStatus: 'ON_SALE', items: [
      { productId: 'p1', expectedRevision: 4, initialSkuIds: ['sku1'] },
    ] })
  })
  it('hides SPU sale actions without SKU status permission', async () => {
    const limited = { ...account, permissionCodes: account.permissionCodes.filter((code) => code !== 'sku.status.write') }
    const wrapper = mount(ProductManagement, { attachTo: document.body, props: { account: limited },
      global: { provide: { 'admin-account': ref(limited) }, stubs: { AssetPicker: true } } })
    wrappers.push(wrapper)
    await flushPromises()
    await wrapper.get('input[aria-label="选择商品 P-1"]').setValue(true)
    expect(wrapper.text()).toContain('商品上下架还需要 SKU 状态权限')
    expect(wrapper.findAll('button').some((item) => ['上架商品', '下架商品', '批量上架商品', '批量下架商品'].includes(item.text()))).toBe(false)
  })
  it('keeps rich description changes under the existing dirty and product revision boundary', async () => {
    const wrapper = await setup()
    await button(wrapper, '编辑商品').trigger('click'); await flushPromises()
    const editor = wrapper.getComponent(EditorContent).props('editor')!
    editor.commands.insertContent('<h2>新图文说明</h2><p>商品特点</p>'); await flushPromises()
    await button(wrapper, '返回商品列表').trigger('click')
    expect(window.confirm).toHaveBeenCalled()
    expect(wrapper.get('[aria-label="商品编辑工作区"]').isVisible()).toBe(true)
    await wrapper.get('form.catalog-editor').trigger('submit'); await flushPromises()
    const sent = vi.mocked(api).mock.calls.find(([path, init]) => path === '/products/p1' && init?.method === 'PATCH')!
    expect(JSON.parse(sent[1]!.body as string)).toMatchObject({ expectedRevision: 4, descriptionHtml: '<h2>新图文说明</h2><p>商品特点</p>' })
  })
  it('releases creation busy state after success so another product can be opened', async () => {
    const previous = vi.mocked(api).getMockImplementation()!
    let complete!: (value: unknown) => void
    vi.mocked(api).mockImplementation((path, init) => path === '/products'
      ? new Promise(resolve => { complete = resolve }) : previous(path, init))
    const wrapper = await setup()
    await button(wrapper, '新建商品').trigger('click')
    await wrapper.get('input[placeholder="例如 PROD-001"]').setValue('NEW-1')
    await wrapper.get('input[placeholder="请输入商品名称"]').setValue('新建商品')
    await wrapper.get('form.catalog-editor select').setValue('leaf')
    await button(wrapper, '生成 SKU 组合').trigger('click')
    await wrapper.get('input[placeholder="例如 SKU-001"]').setValue('NEW-SKU')
    await wrapper.get('input[placeholder="例如 199.00"]').setValue('8.88')
    await wrapper.get('form.catalog-editor').trigger('submit')
    await flushPromises()
    expect((button(wrapper, '返回商品列表').element as HTMLButtonElement).disabled).toBe(true)
    complete(product)
    await flushPromises()
    expect((button(wrapper, '新建商品').element as HTMLButtonElement).disabled).toBe(false)
    await button(wrapper, '编辑商品').trigger('click')
    await flushPromises()
    expect(wrapper.get('[aria-label="商品编辑工作区"]').isVisible()).toBe(true)
  })
  it('opens existing edit in a dedicated workspace while preserving list state', async () => {
    const wrapper = await setup()
    await wrapper.get('input[placeholder="名称或编号"]').setValue('测试')
    await button(wrapper, '编辑商品').trigger('click')
    await flushPromises()
    expect(wrapper.get('[aria-label="商品编辑工作区"]').isVisible()).toBe(true)
    expect(wrapper.get('form[role=search]').isVisible()).toBe(false)
    await button(wrapper, '返回商品列表').trigger('click')
    expect(wrapper.get('form[role=search]').isVisible()).toBe(true)
    expect((wrapper.get('input[placeholder="名称或编号"]').element as HTMLInputElement).value).toBe('测试')
  })
  it('keeps unsaved product edits when returning to the list is declined', async () => {
    const wrapper = await setup()
    await button(wrapper, '编辑商品').trigger('click')
    await flushPromises()
    await wrapper.get('input[maxlength="120"]').setValue('保留修改')
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await button(wrapper, '返回商品列表').trigger('click')
    expect((wrapper.get('input[maxlength="120"]').element as HTMLInputElement).value).toBe('保留修改')
    expect(wrapper.get('form[role=search]').isVisible()).toBe(false)
  })
  it('saves basic data with its revision without submitting specifications', async () => {
    const wrapper = await setup()
    await button(wrapper, '编辑商品').trigger('click')
    await flushPromises()
    await wrapper.get('input[maxlength="120"]').setValue('新的商品名')
    await wrapper.get('form.catalog-editor').trigger('submit')
    await flushPromises()
    const writes = vi.mocked(api).mock.calls.filter(([, init]) => init?.method)
    expect(writes).toHaveLength(1)
    expect(writes[0]![0]).toBe('/products/p1')
    const body = JSON.parse(String(writes[0]![1]!.body))
    expect(body).toMatchObject({ name: '新的商品名', expectedRevision: 4 })
    expect(body).not.toHaveProperty('specAxes')
    expect(wrapper.get('[aria-label="商品编辑工作区"]').text()).toContain('修订 5')
  })
})
