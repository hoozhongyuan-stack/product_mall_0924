import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { useProductPreview } from '../../src/views/pages/product-preview'
import { createComponent } from '../../src/views/pages/types'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
import { api } from '../../src/api'
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
describe('product preview concurrency', () => {
  it('discards stale responses when the selected product source changes', async () => {
    vi.useFakeTimers()
    const component = createComponent('PRODUCT_LIST', 1)
    const components = ref([component])
    let completeOld!: (value: unknown) => void
    vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { completeOld = resolve }))
      .mockResolvedValueOnce({ products: [{ productId: 'new', name: '新商品', priceFen: 200, imageUrl: '', purchasable: true }] })
    let preview!: ReturnType<typeof useProductPreview>
    const wrapper = mount(defineComponent({ setup() { preview = useProductPreview(components, ref(true)); return () => null } }))
    await vi.advanceTimersByTimeAsync(250)
    components.value = [{ ...component, props: { ...component.props, productIds: ['new'] } }]
    await flushPromises()
    await vi.advanceTimersByTimeAsync(250)
    expect(preview.data.value[component.componentId]?.[0]?.name).toBe('新商品')
    completeOld({ products: [{ productId: 'old', name: '过期商品', priceFen: 100, imageUrl: '', purchasable: true }] })
    await flushPromises()
    expect(preview.data.value[component.componentId]?.[0]?.name).toBe('新商品')
    wrapper.unmount()
  })
  it('does not turn malformed or failed responses into invented product prices', async () => {
    vi.useFakeTimers()
    vi.mocked(api).mockResolvedValue({ products: [{ productId: 'bad', name: '错误', priceFen: -1 }] })
    let preview!: ReturnType<typeof useProductPreview>
    const wrapper = mount(defineComponent({ setup() { preview = useProductPreview(ref([createComponent('PRODUCT_LIST', 1)]), ref(true)); return () => null } }))
    await vi.advanceTimersByTimeAsync(250)
    expect(preview.data.value).toEqual({})
    expect(preview.error.value).toContain('无法预览')
    wrapper.unmount()
  })
})
