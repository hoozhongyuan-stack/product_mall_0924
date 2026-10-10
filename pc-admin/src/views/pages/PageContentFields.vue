<script setup lang="ts">
import AssetPicker from '../../shared/AssetPicker.vue'
import PageLinkEditor from './PageLinkEditor.vue'
import type { NavigationItem, PageComponent } from './types'
const props = defineProps<{ component: PageComponent; disabled?: boolean; categories: { id: string; name: string }[]; products: { productId: string; name: string }[]; pages: { pageId: string; name: string }[] }>()
const emit = defineEmits<{ change: [value: PageComponent] }>()
function update(values: Partial<PageComponent['props']>) {
  if (!props.disabled) emit('change', { ...props.component, props: { ...props.component.props, ...values } })
}
function updateItem(index: number, values: Partial<NavigationItem>) {
  update({ items: (props.component.props.items || []).map((item, position) => position === index ? { ...item, ...values } : item) })
}
function moveItem(index: number, offset: number) {
  const items = props.component.props.items || []
  const other = index + offset
  if (other < 0 || other >= items.length) return
  update({ items: items.map((item, position) => position === index ? items[other]! : position === other ? items[index]! : item) })
}
</script>
<template>
  <fieldset :disabled="disabled" class="page-content-fields">
    <template v-if="component.type === 'TITLE'">
      <label>标题<input :value="component.props.text || ''" maxlength="100" @input="update({ text: ($event.target as HTMLInputElement).value })"></label>
      <label>副标题<input :value="component.props.subtitle || ''" maxlength="200" @input="update({ subtitle: ($event.target as HTMLInputElement).value })"></label>
      <label>对齐<select :value="component.props.align" @change="update({ align: ($event.target as HTMLSelectElement).value as 'LEFT' | 'CENTER' })"><option value="LEFT">左对齐</option><option value="CENTER">居中</option></select></label>
      <label>字号<select :value="component.props.size" @change="update({ size: Number(($event.target as HTMLSelectElement).value) as 16 | 20 | 24 })"><option :value="16">16</option><option :value="20">20</option><option :value="24">24</option></select></label>
    </template>
    <template v-if="component.type === 'IMAGE'">
      <label>图片比例<select :value="component.props.ratio" @change="update({ ratio: ($event.target as HTMLSelectElement).value as 'AUTO' | '1:1' | '16:9' })"><option value="AUTO">原图比例</option><option value="1:1">正方形</option><option value="16:9">16:9</option></select></label>
      <PageLinkEditor :model-value="component.props.link" optional :list-id="`${component.componentId}-image`" :categories="categories" :products="products" :pages="pages" @update:model-value="update({ link: $event })" />
    </template>
    <template v-if="component.type === 'NAVIGATION'">
      <label>每行数量<select :value="component.props.columns" @change="update({ columns: Number(($event.target as HTMLSelectElement).value) as 2 | 3 | 4 })"><option :value="2">2</option><option :value="3">3</option><option :value="4">4</option></select></label>
      <div v-for="(item, index) in component.props.items || []" :key="index" class="home-subitem">
        <div class="home-subitem-head"><strong>导航 {{ index + 1 }}</strong><button type="button" @click="moveItem(index, -1)">上移</button><button type="button" @click="moveItem(index, 1)">下移</button><button type="button" @click="update({ items: (component.props.items || []).filter((_, position) => position !== index) })">移除</button></div>
        <label>名称<input :value="item.title" maxlength="40" @input="updateItem(index, { title: ($event.target as HTMLInputElement).value })"></label>
        <AssetPicker kind="IMAGE" :disabled="disabled" :target-key="JSON.stringify([component.componentId, index, component.props.items])" @select="updateItem(index, { assetId: $event.assetId })" />
        <label>图片素材 ID（可选）<input :value="item.assetId || ''" @input="updateItem(index, { assetId: ($event.target as HTMLInputElement).value.trim() })"></label>
        <PageLinkEditor :model-value="item.link" :list-id="`${component.componentId}-nav-${index}`" :categories="categories" :products="products" :pages="pages" @update:model-value="updateItem(index, { link: $event || { type: 'FUNCTION', targetId: 'CATALOG' } })" />
      </div>
      <button type="button" :disabled="(component.props.items?.length || 0) >= 20" @click="update({ items: [...(component.props.items || []), { title: '', link: { type: 'FUNCTION', targetId: 'CATALOG' } }] })">添加导航</button>
    </template>
    <template v-if="component.type === 'PRODUCT_LIST'">
      <label>商品来源<select :value="component.props.source" @change="update({ source: ($event.target as HTMLSelectElement).value as 'MANUAL' | 'CATEGORY' })"><option value="MANUAL">指定商品</option><option value="CATEGORY">商品分类</option></select></label>
      <template v-if="component.props.source === 'MANUAL'">
        <label>选择商品<select multiple :value="component.props.productIds || []" @change="update({ productIds: Array.from(($event.target as HTMLSelectElement).selectedOptions, option => option.value) })"><option v-for="product in products" :key="product.productId" :value="product.productId">{{ product.name }}</option></select></label>
        <label>商品 ID（每行一个，按行排序）<textarea :value="(component.props.productIds || []).join('\n')" rows="4" @change="update({ productIds: ($event.target as HTMLTextAreaElement).value.split(/\s+/).filter(Boolean) })" /></label>
      </template>
      <label v-else>分类<select :value="component.props.categoryId || ''" @change="update({ categoryId: ($event.target as HTMLSelectElement).value })"><option value="">请选择</option><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option></select></label>
      <label>展示数量<input type="number" min="1" max="20" :value="component.props.limit" @change="update({ limit: Number(($event.target as HTMLInputElement).value) })"></label>
      <label>商品布局<select :value="component.props.layout" @change="update({ layout: ($event.target as HTMLSelectElement).value as 'GRID' | 'LIST' | 'SCROLL' })"><option value="GRID">双列</option><option value="LIST">列表</option><option value="SCROLL">横向滑动</option></select></label>
      <label>分类商品排序<select :value="component.props.sort" @change="update({ sort: ($event.target as HTMLSelectElement).value as 'NEWEST' | 'PRICE_ASC' })"><option value="NEWEST">创建时间最新</option><option value="PRICE_ASC">价格升序</option></select></label>
      <p class="help-text">价格与可购买状态由商品系统实时提供。指定商品按所选 ID 顺序展示。</p>
    </template>
  </fieldset>
</template>
<style scoped>.page-content-fields { border: 0; margin: 0; padding: 0; min-width: 0; }</style>
