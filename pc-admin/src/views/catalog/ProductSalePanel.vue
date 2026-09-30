<script setup lang="ts">
import { computed } from 'vue'
import type { ProductRow, SaleStatus } from './types'

export interface ProductSalePreviewItem {
  productId: string
  productNo: string
  productRevision: number
  status: string | null
  skuCount: number
  onSaleSkuCount: number
  skuOptions: { skuId: string; skuCode: string; hasUnit: boolean }[]
  canChange: boolean
  reason?: string
}

const props = defineProps<{
  action: SaleStatus
  products: ProductRow[]
  preview: ProductSalePreviewItem[]
  selectedSkuIds: Record<string, string[]>
  ready: boolean
  busy: boolean
  canStatus: boolean
}>()
const emit = defineEmits<{
  cancel: []
  preview: []
  execute: []
  toggleSku: [productId: string, skuId: string, checked: boolean]
}>()
const byId = computed(() => new Map(props.preview.map((item) => [item.productId, item])))
const readyCount = computed(() => props.preview.filter((item) => item.canChange).length)
</script>

<template>
  <section class="panel action-panel catalog-batch-preview" aria-labelledby="product-sale-title">
    <div class="panel-heading"><div><h3 id="product-sale-title">确认{{ action === 'ON_SALE' ? '上架' : '下架' }}商品</h3>
      <p>已选 {{ products.length }} 个商品。商品下架保留各 SKU 的状态；重新上架只恢复原本在售的 SKU。草稿首次上架须明确选择至少一个 SKU。</p></div>
      <button class="text-button" type="button" :disabled="busy" @click="emit('cancel')">取消</button></div>
    <p v-if="busy" role="status">正在核对或执行…</p>
    <p v-else-if="!ready" class="help-text">请核对影响范围，再确认执行。</p>
    <p v-else class="catalog-impact-summary">服务端核对：{{ readyCount }} 个可执行，{{ preview.length - readyCount }} 个不可执行。</p>
    <ul class="catalog-batch-items"><li v-for="row in products" :key="row.productId"><span>{{ row.name }}（{{ row.productNo }}）</span>
      <small>{{ byId.get(row.productId)?.skuCount ?? row.skuCount }} 个 SKU；当前 SKU 上架 {{ byId.get(row.productId)?.onSaleSkuCount ?? row.onSaleSkuCount }} 个。</small>
      <small v-if="ready" :class="byId.get(row.productId)?.canChange ? 'catalog-batch-ready' : 'catalog-batch-skipped'">{{ byId.get(row.productId)?.reason || '可执行' }}</small>
      <div v-if="action === 'ON_SALE' && row.status === 'DRAFT'" class="catalog-batch-category-row">
        <span>首次上架 SKU：</span><label v-for="sku in byId.get(row.productId)?.skuOptions || []" :key="sku.skuId"><input type="checkbox" :checked="(selectedSkuIds[row.productId] || []).includes(sku.skuId)" :disabled="!sku.hasUnit || !canStatus || busy" @change="emit('toggleSku', row.productId, sku.skuId, ($event.target as HTMLInputElement).checked)" />{{ sku.skuCode }}{{ sku.hasUnit ? '' : '（缺少单位）' }}</label>
        <small v-if="!canStatus">首次上架 SKU 还需要 SKU 状态权限。</small>
      </div></li></ul>
    <button class="secondary-button" type="button" :disabled="busy" @click="emit('preview')">核对影响范围</button>
    <button class="primary-button" type="button" :disabled="busy || !ready || !readyCount" @click="emit('execute')">确认执行</button>
  </section>
</template>
