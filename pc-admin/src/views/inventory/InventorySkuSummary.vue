<script setup lang="ts">
import { ref, watch } from 'vue'
import type { InventorySku } from './types'
const props = defineProps<{ sku: InventorySku }>()
const broken = ref(false)
watch(() => props.sku.mainImage?.adminUrl, () => { broken.value = false })
</script>
<template>
  <div class="inventory-sku-summary">
    <img v-if="sku.mainImage && !broken" :src="sku.mainImage.adminUrl" :alt="`${sku.productName}商品主图`" loading="lazy" @error="broken = true" />
    <span v-else class="inventory-sku-placeholder">暂无图片</span>
    <div class="inventory-sku-identity">
      <strong>{{ sku.productName }}</strong>
      <span>{{ sku.specs?.length ? sku.specs.map(spec => `${spec.name}：${spec.value}`).join(' · ') : '默认规格' }}</span>
      <small>SKU：{{ sku.skuCode }}<template v-if="sku.productNo"> · 商品：{{ sku.productNo }}</template></small>
    </div>
  </div>
</template>
