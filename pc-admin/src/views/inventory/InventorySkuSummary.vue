<script setup lang="ts">
import { ref, watch } from 'vue'
import type { InventorySku } from './types'
import { listMediaUrl } from '../../shared/media-library'
const props = defineProps<{ sku: InventorySku }>()
const broken = ref(false)
watch(() => props.sku.mainImage?.thumbnailUrl, () => { broken.value = false })
</script>
<template>
  <div class="inventory-sku-summary">
    <img v-if="listMediaUrl(sku.mainImage) && !broken" :src="listMediaUrl(sku.mainImage)" :alt="`${sku.productName}商品主图`" loading="lazy" decoding="async" @error="broken = true" />
    <span v-else class="inventory-sku-placeholder">{{sku.mainImage?'图片暂不可用':'暂无图片'}}</span>
    <div class="inventory-sku-identity">
      <strong>{{ sku.productName }}</strong>
      <span>{{ sku.specs?.length ? sku.specs.map(spec => `${spec.name}：${spec.value}`).join(' · ') : '默认规格' }}</span>
      <small>SKU：{{ sku.skuCode }}<template v-if="sku.productNo"> · 商品：{{ sku.productNo }}</template></small>
    </div>
  </div>
</template>
