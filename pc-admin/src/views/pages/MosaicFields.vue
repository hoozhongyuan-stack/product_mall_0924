<script setup lang="ts">
import { ref, watch } from 'vue'
import AssetPicker from '../../shared/AssetPicker.vue'
import PageLinkEditor from './PageLinkEditor.vue'
import type { MosaicItem, MosaicTemplate, PageComponent } from './types'
const props = defineProps<{ component: PageComponent; disabled?: boolean; categories: { id: string; name: string }[]; products: { productId: string; name: string }[]; pages: { pageId: string; name: string }[] }>()
const emit = defineEmits<{ change: [value: PageComponent] }>()
const counts: Record<MosaicTemplate, number> = { TWO: 2, THREE: 3, FOUR: 4, FEATURED: 3 }
const pending = ref<MosaicTemplate | null>(null)
watch(() => JSON.stringify([props.component.componentId, props.component.props.items]), () => { pending.value = null })
function update(change: Partial<PageComponent['props']>) { if (!props.disabled) emit('change', { ...props.component, props: { ...props.component.props, ...change } }) }
function item(index: number, change: Partial<MosaicItem>) { update({ items: (props.component.props.items || []).map((entry, position) => position === index ? { ...entry, ...change } : entry) }) }
function apply(template: MosaicTemplate) {
  update({ template, items: Array.from({ length: counts[template] }, (_, index) => ({ ...(props.component.props.items?.[index] || { assetId: '' }) })) })
  pending.value = null
}
function choose(template: MosaicTemplate) {
  if (props.disabled) return
  if ((props.component.props.items?.length || 0) > counts[template]) pending.value = template
  else apply(template)
}
</script>
<template>
  <fieldset class="mosaic-fields" :disabled="disabled">
    <label>魔方模板<select :value="component.props.template || 'TWO'" @change="choose(($event.target as HTMLSelectElement).value as MosaicTemplate)"><option value="TWO">两列</option><option value="THREE">三列</option><option value="FOUR">四格</option><option value="FEATURED">左侧大图、右侧上下图</option></select></label>
    <div v-if="pending" role="alertdialog" aria-label="确认切换魔方模板"><p>切换后将移除最后 {{ (component.props.items?.length || 0) - counts[pending] }} 个位置的图片和链接。前面位置的配置会保留。</p><button type="button" @click="apply(pending!)">确认切换模板</button><button type="button" @click="pending = null">取消</button></div>
    <label>图片间距<select :value="component.props.gap || 0" @change="update({ gap: Number(($event.target as HTMLSelectElement).value) as 0 | 4 | 8 | 12 | 16 })"><option v-for="gap in [0,4,8,12,16]" :key="gap" :value="gap">{{ gap }} px</option></select></label>
    <div v-for="(entry, index) in component.props.items || []" :key="index" class="home-subitem">
      <strong>图片位置 {{ index + 1 }}</strong>
      <label>图片素材 ID<input :value="entry.assetId || ''" @input="item(index, { assetId: ($event.target as HTMLInputElement).value.trim() })"></label>
      <AssetPicker kind="IMAGE" :disabled="disabled" :target-key="JSON.stringify([component.componentId, component.props.template, component.props.items, index])" @select="item(index, { assetId: $event.assetId })" />
      <label>图片标题（可选）<input :value="entry.title || ''" maxlength="40" @input="item(index, { title: ($event.target as HTMLInputElement).value })"></label>
      <PageLinkEditor :model-value="entry.link" optional :list-id="`${component.componentId}-mosaic-${index}`" :categories="categories" :products="products" :pages="pages" @update:model-value="item(index, { link: $event })" />
    </div>
  </fieldset>
</template>
<style scoped>.mosaic-fields{border:0;padding:0;margin:0;min-width:0}.mosaic-fields>div[role=alertdialog]{padding:12px;background:var(--mall-color-canvas-admin);border:1px solid var(--mall-color-border);border-radius:8px}</style>
