<script setup lang="ts">
import { ref } from 'vue'
import { componentNames, type ComponentType } from './types'
defineProps<{ disabled: boolean }>()
defineEmits<{ add: [type: ComponentType] }>()
const groups: { name: string; types: ComponentType[] }[] = [
  { name: '常用', types: ['TITLE', 'IMAGE', 'NAVIGATION', 'NOTICE'] },
  { name: '内容', types: ['TITLE', 'IMAGE', 'NAVIGATION', 'CAROUSEL', 'IMAGE_HOTZONE', 'NOTICE'] },
  { name: '商品营销', types: ['PRODUCT_LIST', 'COUPON_LIST'] },
  { name: '布局辅助', types: ['MOSAIC', 'SPACER', 'DIVIDER', 'SEARCH', 'FILING'] },
]
const active = ref(0)
</script>
<template>
  <div class="component-library-groups" aria-label="组件用途">
    <button v-for="(group, index) in groups" :key="group.name" type="button" :aria-pressed="active === index" @click="active = index">{{ group.name }}</button>
  </div>
  <div class="home-component-library"><button v-for="type in groups[active]!.types" :key="type" type="button" :disabled="disabled" @click="$emit('add', type)"><span>{{ componentNames[type] }}</span><strong aria-hidden="true">＋</strong></button></div>
</template>
<style scoped>
.component-library-groups{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:12px}.component-library-groups button{padding:7px 4px;min-height:36px;border:1px solid var(--mall-color-border);border-radius:6px;background:var(--mall-color-surface);color:var(--mall-color-muted);font-size:12px}.component-library-groups button[aria-pressed=true]{color:var(--mall-color-brand);background:var(--mall-color-brand-soft);border-color:var(--mall-color-brand)}
</style>
