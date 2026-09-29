<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ElTable, ElTableColumn } from 'element-plus'
import 'element-plus/theme-chalk/el-table.css'
import { api } from '../../api'
import InventorySkuSummary from './InventorySkuSummary.vue'
import type { InventorySku, Page } from './types'
const props = withDefaults(defineProps<{
  modelValue: InventorySku | null; warehouseId: string; disabled?: boolean; excludedIds?: string[]
}>(), { disabled: false, excludedIds: () => [] })
const emit = defineEmits<{ select: [sku: InventorySku] }>()
const open = ref(false)
const keyword = ref('')
const appliedKeyword = ref('')
const page = ref(1)
const result = ref<Page<InventorySku> | null>(null)
const loading = ref(false)
const error = ref('')
let generation = 0
async function load(nextPage = 1) {
  const current = ++generation
  page.value = nextPage
  result.value = null
  error.value = ''
  loading.value = true
  const query = new URLSearchParams({ page: String(nextPage), pageSize: '10' })
  if (props.warehouseId) query.set('warehouseId', props.warehouseId)
  if (appliedKeyword.value) query.set('keyword', appliedKeyword.value)
  try {
    const data = await api<Page<InventorySku>>(`/inventory/skus?${query}`)
    if (open.value && current === generation) result.value = data
  } catch (reason) {
    if (open.value && current === generation) error.value = reason instanceof Error ? reason.message : '商品读取失败，请重试。'
  } finally { if (current === generation) loading.value = false }
}
function show() {
  if (props.disabled || !props.warehouseId) return
  open.value = true
  keyword.value = ''
  appliedKeyword.value = ''
  void load(1)
}
function search() { appliedKeyword.value = keyword.value.trim(); void load(1) }
function choose(sku: InventorySku) {
  if (props.disabled || loading.value || error.value || props.excludedIds.includes(sku.skuId)) return
  emit('select', sku)
  open.value = false
}
watch(() => props.warehouseId, () => { if (open.value) void load(1) })
watch(open, value => { if (!value) { generation++; loading.value = false } })
watch(() => props.disabled, value => { if (value) open.value = false })
onBeforeUnmount(() => { generation++ })
</script>
<template>
  <div class="inventory-sku-field">
    <InventorySkuSummary v-if="modelValue" :sku="modelValue" />
    <el-button :disabled="disabled || !warehouseId" @click="show">{{ modelValue ? '更换商品' : '选择商品 / SKU' }}</el-button>
    <small v-if="!warehouseId">请先选择仓库</small>
    <el-dialog v-model="open" title="选择商品 / SKU" width="min(960px, calc(100vw - 32px))" class="inventory-sku-dialog" append-to-body :close-on-click-modal="false">
      <p class="help-text">按规格和 SKU 编码核对商品。库存为所选仓库的当前参考值，确认单据时会重新核验。</p>
      <form class="inventory-sku-search" @submit.prevent="search">
        <el-input v-model="keyword" maxlength="120" clearable aria-label="搜索商品或 SKU" placeholder="商品名称、规格、商品编号或 SKU 编码" />
        <el-button native-type="submit" type="primary" :loading="loading">搜索</el-button>
      </form>
      <p v-if="error" class="error" role="alert">{{ error }} <el-button link type="primary" @click="load(page)">重试</el-button></p>
      <p v-if="loading" role="status">正在读取商品和库存…</p>
      <el-table :data="result?.items || []" row-key="skuId" class="inventory-sku-table" :empty-text="loading ? '正在读取…' : error ? '请重试读取商品' : '没有匹配的商品，请调整搜索词'">
        <el-table-column label="商品 / 规格 / SKU" min-width="300"><template #default="{ row }"><InventorySkuSummary :sku="row as InventorySku" /></template></el-table-column>
        <el-table-column label="单位换算" min-width="140"><template #default="{ row }">1 {{ row.saleUnit }} = {{ row.ratio }} {{ row.baseUnit }}</template></el-table-column>
        <el-table-column label="所选仓库存量" min-width="160"><template #default="{ row }"><div v-if="row.warehouseStock" class="inventory-sku-stock"><strong>可售 {{ row.warehouseStock.availableBaseUnits }} {{ row.baseUnit }}</strong><small>账面 {{ row.warehouseStock.onHandBaseUnits }} · 锁定 {{ row.warehouseStock.reservedBaseUnits }}</small></div><span v-else>未选择仓库</span></template></el-table-column>
        <el-table-column label="操作" width="96" fixed="right"><template #default="{ row }"><el-button link type="primary" :disabled="disabled || loading || excludedIds.includes(row.skuId)" @click="choose(row as InventorySku)">{{ excludedIds.includes(row.skuId) ? '已添加' : '选用' }}</el-button></template></el-table-column>
      </el-table>
      <div class="inventory-sku-pagination"><span>共 {{ result?.total ?? '—' }} 个 SKU</span><el-pagination v-if="result && result.total > 10" :current-page="page" :page-size="10" :total="result.total" layout="prev, pager, next" :disabled="loading" @current-change="load" /></div>
      <template #footer><el-button @click="open = false">取消</el-button></template>
    </el-dialog>
  </div>
</template>
