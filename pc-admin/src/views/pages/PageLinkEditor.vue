<script setup lang="ts">
import type { PageLink } from './types'

const props = defineProps<{
  modelValue?: PageLink
  optional?: boolean
  listId: string
  categories: { id: string; name: string }[]
  products: { productId: string; name: string }[]
  pages: { pageId: string; name: string }[]
}>()
const emit = defineEmits<{ 'update:modelValue': [value: PageLink | undefined] }>()

function changeType(value: string) {
  if (!value) { emit('update:modelValue', undefined); return }
  emit('update:modelValue', { type: value as PageLink['type'], targetId: value === 'FUNCTION' ? 'CATALOG' : '' })
}
function changeTarget(value: string) {
  if (props.modelValue) emit('update:modelValue', { ...props.modelValue, targetId: value })
}
</script>

<template>
  <div class="home-link-fields">
    <label>链接类型
      <select :value="modelValue?.type || ''" @change="changeType(($event.target as HTMLSelectElement).value)">
        <option v-if="optional" value="">不跳转</option>
        <option v-else value="" disabled>请选择</option>
        <option value="PRODUCT">商品详情</option>
        <option value="CATEGORY">商品分类</option>
        <option value="PAGE">独立微页面</option>
        <option value="FUNCTION">站内功能</option>
      </select>
    </label>
    <label v-if="modelValue?.type === 'FUNCTION'">跳转目标
      <select :value="modelValue.targetId" @change="changeTarget(($event.target as HTMLSelectElement).value)">
        <option value="CATALOG">商品分类</option>
        <option value="SEARCH">搜索商品</option>
      </select>
    </label>
    <label v-else-if="modelValue?.type === 'CATEGORY'">分类目标
      <input :value="modelValue.targetId" :list="`${listId}-categories`" placeholder="选择分类或输入分类 ID" @input="changeTarget(($event.target as HTMLInputElement).value.trim())">
      <datalist :id="`${listId}-categories`"><option v-for="item in categories" :key="item.id" :value="item.id" :label="item.name" /></datalist>
    </label>
    <label v-else-if="modelValue?.type === 'PRODUCT'">商品目标
      <input :value="modelValue.targetId" :list="`${listId}-products`" placeholder="选择商品或输入商品 ID" @input="changeTarget(($event.target as HTMLInputElement).value.trim())">
      <datalist :id="`${listId}-products`"><option v-for="item in products" :key="item.productId" :value="item.productId" :label="item.name" /></datalist>
    </label>
    <label v-else-if="modelValue?.type === 'PAGE'">已发布微页面
      <input :value="modelValue.targetId" :list="`${listId}-pages`" placeholder="选择已发布页面或输入页面 ID" @input="changeTarget(($event.target as HTMLInputElement).value.trim())">
      <datalist :id="`${listId}-pages`"><option v-for="item in pages" :key="item.pageId" :value="item.pageId" :label="item.name" /></datalist>
      <small>目标须已发布；发布前服务端会检查自引用与页面循环。</small>
    </label>
  </div>
</template>
