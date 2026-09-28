import { onUnmounted, ref } from 'vue'
import { api } from '../../api'
import type { MicroPageList, MicroPageSummary } from './types'

type CategoryTarget = { id: string; name: string }
type ProductTarget = { productId: string; name: string }

function targetRows<T>(value: unknown, idKey: string): T[] {
  if (!Array.isArray(value) || value.some(row => !row || typeof row[idKey] !== 'string' || typeof row.name !== 'string')) {
    throw new Error('目标列表格式不正确。')
  }
  return value
}

async function publicData(path: string): Promise<unknown> {
  const response = await fetch(`/api/v1/app${path}`)
  const payload = await response.json()
  if (!response.ok || payload?.success !== true || payload.data === undefined) {
    throw new Error('目标列表暂不可用。')
  }
  return payload.data
}

async function loadPageTargets(): Promise<MicroPageList> {
  const data = await api<MicroPageList>('/pages?page=1&pageSize=50')
  const rows = targetRows<MicroPageSummary>(data?.rows, 'pageId')
  if (!Number.isInteger(data.total) || data.total < rows.length || rows.some(row => row.publishedRevision !== null && !Number.isInteger(row.publishedRevision))) {
    throw new Error('微页面目标列表格式不正确。')
  }
  return { ...data, rows }
}

export function usePageTargets() {
  const categories = ref<CategoryTarget[]>([])
  const products = ref<ProductTarget[]>([])
  const pages = ref<MicroPageSummary[]>([])
  const warning = ref('')
  const loading = ref(false)
  let sequence = 0
  onUnmounted(() => { sequence++ })

  async function load() {
    const current = ++sequence
    loading.value = true
    warning.value = ''
    categories.value = []
    products.value = []
    pages.value = []
    const [category, product, page] = await Promise.allSettled([
      publicData('/categories').then(data => targetRows<CategoryTarget>(data, 'id')),
      publicData('/products?page=1&pageSize=100').then(data => targetRows<ProductTarget>((data as { rows?: unknown } | null)?.rows, 'productId')),
      loadPageTargets(),
    ])
    if (current !== sequence) return
    categories.value = category.status === 'fulfilled' ? category.value : []
    products.value = product.status === 'fulfilled' ? product.value : []
    pages.value = page.status === 'fulfilled' ? page.value.rows : []
    if ([category, product, page].some(result => result.status === 'rejected')) {
      warning.value = '部分目标列表暂不可用，可输入已知的分类、商品或微页面 ID。'
    } else if (page.status === 'fulfilled' && page.value.total > page.value.rows.length) {
      warning.value = '微页面目标列表只显示前 50 项，可输入其他已发布页面 ID。'
    }
    loading.value = false
  }
  return { categories, products, pages, warning, loading, load }
}
