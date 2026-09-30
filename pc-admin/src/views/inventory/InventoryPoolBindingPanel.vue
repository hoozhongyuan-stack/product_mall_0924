<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'

interface ProductOption { productId: string; productNo: string; name: string; skuCount: number }

interface Binding {
  skuId: string
  skuCode: string
  skuRevision: number
  baseUnit: string | null
  poolId: string | null
  anchorSkuId: string
  poolBaseUnit: string | null
  shared: boolean
  poolAnchorSkuCode: string | null
}

const keyword = ref('')
const products = ref<ProductOption[]>([])
const product = ref<ProductOption | null>(null)
const bindings = ref<Binding[]>([])
const sourceSkuId = ref('')
const anchorSkuId = ref('')
const searching = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const source = computed(() => bindings.value.find((item) => item.skuId === sourceSkuId.value) || null)
const anchor = computed(() => bindings.value.find((item) => item.skuId === anchorSkuId.value) || null)
const anchors = computed(() => bindings.value.filter((item) => item.skuId !== sourceSkuId.value
  && item.anchorSkuId === item.skuId && item.baseUnit && item.baseUnit === source.value?.baseUnit))
const canBind = computed(() => Boolean(source.value && anchor.value && !source.value.shared
  && source.value.anchorSkuId === source.value.skuId && source.value.baseUnit
  && anchor.value.baseUnit === source.value.baseUnit))

function message(reason: unknown) { return reason instanceof Error ? reason.message : '请求失败，请稍后重试。' }
async function searchProducts() {
  if (loading.value || saving.value) return
  searching.value = true
  error.value = ''
  products.value = []
  product.value = null
  bindings.value = []
  sourceSkuId.value = ''
  anchorSkuId.value = ''
  try {
    const query = new URLSearchParams({ keyword: keyword.value.trim() })
    const page = await api<{ items: ProductOption[] }>(`/inventory/pool-product-options?${query}`)
    products.value = page.items
  } catch (reason) { error.value = message(reason) }
  finally { searching.value = false }
}
async function selectProduct(row: ProductOption) {
  product.value = row
  sourceSkuId.value = ''
  anchorSkuId.value = ''
  loading.value = true
  error.value = ''
  try {
    const data = await api<{ items: Binding[] }>(`/inventory/pool-bindings?productId=${encodeURIComponent(row.productId)}`)
    bindings.value = data.items
  } catch (reason) { bindings.value = []; error.value = message(reason) }
  finally { loading.value = false }
}
function chooseSource(id: string) { sourceSkuId.value = id; anchorSkuId.value = '' }
async function bindPool() {
  const currentSource = source.value
  const currentAnchor = anchor.value
  if (!canBind.value || !currentSource || !currentAnchor || saving.value) return
  try {
    await ElMessageBox.confirm(
      `将 ${currentSource.skuCode} 绑定到 ${currentAnchor.skuCode} 的实物库存池。仅允许无历史库存和单据的 SKU；系统不会合并余额。确认继续？`,
      '核对库存池绑定', { confirmButtonText: '确认绑定', cancelButtonText: '返回核对', type: 'warning' },
    )
  } catch { return }
  saving.value = true
  error.value = ''
  try {
    const target = currentAnchor.poolId ? { poolId: currentAnchor.poolId } : { anchorSkuId: currentAnchor.skuId }
    await api('/inventory/pool-bindings', { method: 'POST',
      body: JSON.stringify({ skuId: currentSource.skuId, ...target, expectedSkuRevision: currentSource.skuRevision }) })
    ElMessage.success('库存池绑定已保存')
    if (product.value) await selectProduct(product.value)
  } catch (reason) { error.value = message(reason) }
  finally { saving.value = false }
}
</script>

<template>
  <section class="panel inventory-pool-panel" aria-label="库存池绑定">
    <div class="page-heading"><div><h2>库存池绑定</h2><p>选择商品后，将尚无历史库存和单据的 SKU 绑定到同商品、同基础单位的锚 SKU。绑定不会自动合并任何余额。</p></div></div>
    <form class="inventory-pool-search" @submit.prevent="searchProducts"><label>查找商品（名称、商品或 SKU 编码）<input v-model="keyword" maxlength="120" placeholder="输入商品名称或编码" /></label><button class="secondary-button" type="submit" :disabled="searching || loading || saving">{{ searching ? '查询中…' : '查询商品' }}</button></form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div v-if="products.length" class="inventory-pool-products"><p>选择商品；最多显示前 20 条匹配结果。</p><button v-for="row in products" :key="row.productId" type="button" :class="product?.productId === row.productId ? 'primary-button' : 'secondary-button'" :disabled="loading || saving" @click="selectProduct(row)">{{ row.name }} · {{ row.productNo }} · {{ row.skuCount }} 个 SKU</button></div>
    <p v-else-if="!searching && keyword.trim() && !product" class="help-text">如未找到商品，请调整搜索词后重试。</p>
    <div v-if="product" class="inventory-pool-bindings"><h3>{{ product.name }}（{{ product.productNo }}）的库存池</h3><p v-if="loading" role="status">正在读取 SKU 与库存池…</p><p v-else-if="!bindings.length" class="inventory-empty">该商品尚无 SKU，或库存池资料未能读取。</p>
      <div v-else class="table-wrap"><table><thead><tr><th>SKU</th><th>基础单位</th><th>当前库存池</th><th>关系</th></tr></thead><tbody><tr v-for="item in bindings" :key="item.skuId"><td class="code">{{ item.skuCode }}</td><td>{{ item.baseUnit || '未配置' }}</td><td class="code">{{ item.poolId || '尚未建立' }}</td><td>{{ item.shared ? `与 ${item.poolAnchorSkuCode || item.anchorSkuId} 共享` : '独立' }}</td></tr></tbody></table></div>
      <form v-if="bindings.length" class="inventory-pool-form" @submit.prevent="bindPool"><label>待绑定 SKU<select :value="sourceSkuId" :disabled="saving" @change="chooseSource(($event.target as HTMLSelectElement).value)"><option value="">请选择</option><option v-for="item in bindings.filter((entry) => !entry.shared && entry.anchorSkuId === entry.skuId)" :key="item.skuId" :value="item.skuId">{{ item.skuCode }}（{{ item.baseUnit || '未配置单位' }}）</option></select></label><label>目标锚 SKU<select v-model="anchorSkuId" :disabled="saving || !source"><option value="">请选择</option><option v-for="item in anchors" :key="item.skuId" :value="item.skuId">{{ item.skuCode }}（{{ item.baseUnit }}{{ item.poolId ? ' · 已建池' : ' · 待建池' }}）</option></select></label><p class="help-text">仅支持同商品、同基础单位。服务端会再次核对历史库存、单据、锁定量及修订号；不允许直接修改余额。</p><p v-if="source && anchor" class="inventory-pool-preview">确认对象：{{ source.skuCode }} → {{ anchor.skuCode }}；目标池 {{ anchor.poolId || '首次创建' }}。</p><button class="primary-button" type="submit" :disabled="!canBind || saving">{{ saving ? '绑定中…' : '核对并绑定' }}</button></form>
    </div>
  </section>
</template>
