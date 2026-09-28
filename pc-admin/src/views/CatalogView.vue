<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import type { Account } from '../api'
import CategoryManagement from './catalog/CategoryManagement.vue'
import ProductManagement from './catalog/ProductManagement.vue'

defineProps<{ account: Account }>()
const tab = ref<'products' | 'categories'>('products')
const dirtyProducts = ref(false)
const workspaceOpen = ref(false)
function confirmLeave() {
  return !dirtyProducts.value || window.confirm('当前商品资料尚未保存，离开后已填写的内容会丢失。确定继续吗？')
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirtyProducts.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => confirmLeave())
async function switchTab(value: 'products' | 'categories') {
  if (value === tab.value || (tab.value === 'products' && !confirmLeave())) return
  dirtyProducts.value = false
  tab.value = value
  await nextTick()
  document.getElementById(`${value}-tab`)?.focus()
}
</script>

<template>
  <section class="page-content catalog-page" :class="{ 'catalog-editor-page': workspaceOpen }">
    <div v-show="!workspaceOpen" class="catalog-tabs" role="tablist" aria-label="商品管理内容">
      <button id="products-tab" type="button" role="tab" :aria-selected="tab === 'products'" :tabindex="tab === 'products' ? 0 : -1" aria-controls="products-panel" :class="{ active: tab === 'products' }" @click="switchTab('products')" @keydown.right.prevent="switchTab('categories')" @keydown.left.prevent="switchTab('categories')">商品管理</button>
      <button id="categories-tab" type="button" role="tab" :aria-selected="tab === 'categories'" :tabindex="tab === 'categories' ? 0 : -1" aria-controls="categories-panel" :class="{ active: tab === 'categories' }" @click="switchTab('categories')" @keydown.left.prevent="switchTab('products')" @keydown.right.prevent="switchTab('products')">商品分类</button>
    </div>
    <div v-if="tab === 'products'" id="products-panel" role="tabpanel" aria-label="商品管理"><ProductManagement :account="account" @dirty-change="dirtyProducts = $event" @workspace-change="workspaceOpen = $event" /></div>
    <div v-else id="categories-panel" role="tabpanel" aria-labelledby="categories-tab"><CategoryManagement :account="account" /></div>
  </section>
</template>
