import { onUnmounted, ref, watch, type Ref } from 'vue'
import { api } from '../../api'
import type { ComponentData, PageComponent, ProductCard } from './types'
function validProducts(value: unknown): value is ProductCard[] {
  return Array.isArray(value) && value.length <= 20 && value.every(item => item && typeof item.productId === 'string' && typeof item.name === 'string' && Number.isSafeInteger(item.priceFen) && item.priceFen >= 0 && typeof item.imageUrl === 'string' && typeof item.purchasable === 'boolean')
}
export function useProductPreview(components: Ref<PageComponent[]>, enabled: Ref<boolean>) {
  const data = ref<ComponentData>({})
  const error = ref('')
  const loading = ref(false)
  let sequence = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  async function load(current: number, items: PageComponent[]) {
    const results = await Promise.allSettled(items.map(async item => {
      const result = await api<{ products: ProductCard[] }>('/pages/product-preview', { method: 'POST', body: JSON.stringify({ props: item.props }) })
      if (!validProducts(result.products)) throw new Error('商品预览格式不正确。')
      return [item.componentId, result.products] as const
    }))
    if (sequence !== current) return
    data.value = Object.fromEntries(results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []))
    error.value = results.some(result => result.status === 'rejected') ? '部分商品无法预览，请检查商品来源后重试。' : ''
    loading.value = false
  }
  watch(() => JSON.stringify([enabled.value, components.value.filter(item => item.type === 'PRODUCT_LIST').map(item => [item.componentId, item.visible, item.props])]), () => {
    const current = ++sequence
    if (timer) clearTimeout(timer)
    data.value = {}; error.value = ''; loading.value = false
    const items = components.value.filter(item => item.visible && item.type === 'PRODUCT_LIST')
    if (!enabled.value || !items.length) return
    loading.value = true
    timer = setTimeout(() => { void load(current, items) }, 250)
  }, { immediate: true })
  onUnmounted(() => { sequence++; if (timer) clearTimeout(timer) })
  return { data, error, loading }
}
