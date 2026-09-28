<script setup lang="ts">
import { ref } from 'vue'
import AssetPicker from '../../shared/AssetPicker.vue'
import { api } from '../../api'
import type { Asset } from '../../shared/media'
import PageLinkEditor from './PageLinkEditor.vue'
import type { HotzoneArea, PageComponent, PageLink, Slide, UploadedAssetBinding } from './types'

const props = defineProps<{
  disabled?: boolean
  component: PageComponent
  canUpload: boolean
  categories: { id: string; name: string }[]
  products: { productId: string; name: string }[]
  pages: { pageId: string; name: string }[]
}>()
const emit = defineEmits<{ change: [value: PageComponent]; uploaded: [value: UploadedAssetBinding] }>()
const uploading = ref('')
const error = ref('')

function updateProps(values: Partial<PageComponent['props']>) {
  emit('change', { ...props.component, props: { ...props.component.props, ...values } })
}
function updateSlides(slides: Slide[]) { updateProps({ slides }) }
function updateAreas(areas: HotzoneArea[]) { updateProps({ areas }) }
function updateSlide(index: number, change: Partial<Slide>) {
  updateSlides((props.component.props.slides || []).map((slide, position) => position === index ? { ...slide, ...change } : slide))
}
function updateArea(index: number, change: Partial<HotzoneArea>) {
  updateAreas((props.component.props.areas || []).map((area, position) => position === index ? { ...area, ...change } : area))
}
function assetUrl(assetId: string) { return assetId ? `/api/v1/admin/assets/${encodeURIComponent(assetId)}/file` : '' }

async function uploadImage(event: Event, slot: 'hotzone' | number) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || uploading.value) return
  const target = props.component
  const componentId = target.componentId
  const binding: Omit<UploadedAssetBinding, 'assetId'> = {
    componentId,
    type: target.type === 'CAROUSEL' ? 'CAROUSEL' : 'IMAGE_HOTZONE',
    slot,
    expectedSlides: slot === 'hotzone' ? undefined : JSON.stringify(target.props.slides || []),
    expectedAssetId: slot === 'hotzone' ? target.props.assetId || '' : undefined,
  }
  if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 10 * 1024 * 1024 || !file.size) {
    error.value = '请选择不超过 10 MiB 的 JPG 或 PNG 图片。'
    return
  }
  uploading.value = String(slot)
  error.value = ''
  try {
    const body = new FormData()
    body.append('file', file)
    body.append('kind', 'IMAGE')
    const result = await api<Asset>('/assets', { method: 'POST', body })
    emit('uploaded', { ...binding, assetId: result.assetId })
  } catch (reason) {
    if (props.component.componentId === componentId) {
      error.value = reason instanceof Error ? reason.message : '图片上传失败，请重试。'
    }
  } finally { uploading.value = '' }
}
function chooseAsset(asset: Asset, slot: 'hotzone' | number) {
  if (props.disabled) return
  const target = props.component
  emit('uploaded', { componentId: target.componentId, type: target.type === 'CAROUSEL' ? 'CAROUSEL' : 'IMAGE_HOTZONE', slot, expectedSlides: slot === 'hotzone' ? undefined : JSON.stringify(target.props.slides || []), expectedAssetId: slot === 'hotzone' ? target.props.assetId || '' : undefined, assetId: asset.assetId })
}
function addArea() {
  updateAreas([...(props.component.props.areas || []), {
    x: 0, y: 0, width: 1, height: 1, link: { type: 'FUNCTION', targetId: 'CATALOG' },
  }])
}
function setAreaNumber(index: number, key: 'x' | 'y' | 'width' | 'height', raw: string) {
  const parsed = Number(raw)
  if (Number.isFinite(parsed)) updateArea(index, { [key]: parsed })
}
function setLink(value: PageLink | undefined) { updateProps({ link: value }) }
</script>

<template>
  <div class="home-component-form">
    <template v-if="component.type === 'SEARCH'">
      <label>搜索提示语<input :value="component.props.placeholder || ''" maxlength="40" placeholder="搜索商品" @input="updateProps({ placeholder: ($event.target as HTMLInputElement).value })"></label>
      <p class="help-text">点击后进入站内商品搜索。</p>
    </template>

    <template v-else-if="component.type === 'NOTICE'">
      <label>公告文案<textarea :value="component.props.text || ''" maxlength="120" rows="3" placeholder="输入公告内容" @input="updateProps({ text: ($event.target as HTMLTextAreaElement).value })" /></label>
      <PageLinkEditor :model-value="component.props.link" :optional="true" :list-id="`${component.componentId}-notice`" :categories="categories" :products="products" :pages="pages" @update:model-value="setLink" />
    </template>

    <template v-else-if="component.type === 'CAROUSEL'">
      <p class="help-text">按下方顺序轮播图片。每张可单独设置跳转。</p>
      <div v-for="(slide, index) in component.props.slides || []" :key="index" class="home-subitem">
        <div class="home-subitem-head"><strong>第 {{ index + 1 }} 张</strong><button type="button" class="text-button danger" @click="updateSlides((component.props.slides || []).filter((_, position) => position !== index))">移除</button></div>
        <img v-if="slide.assetId" :src="assetUrl(slide.assetId)" :alt="`轮播图 ${index + 1}`" class="home-asset-thumb">
        <label v-if="canUpload" class="home-upload-button">{{ uploading === String(index) ? '上传中…' : slide.assetId ? '更换图片' : '上传图片' }}<input type="file" accept="image/png,image/jpeg" :disabled="disabled || Boolean(uploading)" @change="uploadImage($event, index)"></label>
        <AssetPicker kind="IMAGE" :disabled="disabled || Boolean(uploading)" :excluded-ids="(component.props.slides || []).filter((_, position) => position !== index).map(item => item.assetId)" :target-key="JSON.stringify([component.componentId, component.props.slides])" @select="chooseAsset($event, index)" />
        <label>素材 ID<input :value="slide.assetId" placeholder="上传图片或粘贴素材 ID" @input="updateSlide(index, { assetId: ($event.target as HTMLInputElement).value.trim() })"></label>
        <PageLinkEditor :model-value="slide.link" :optional="true" :list-id="`${component.componentId}-slide-${index}`" :categories="categories" :products="products" :pages="pages" @update:model-value="updateSlide(index, { link: $event })" />
      </div>
      <button type="button" class="secondary-button home-add-row" @click="updateSlides([...(component.props.slides || []), { assetId: '' }])">添加轮播图</button>
    </template>

    <template v-else-if="component.type === 'IMAGE_HOTZONE'">
      <p class="help-text">图片热区坐标按图片宽高的 0–1 比例填写，可设置多个点击区域。</p>
      <img v-if="component.props.assetId" :src="assetUrl(component.props.assetId)" alt="热区图片" class="home-asset-thumb">
      <label v-if="canUpload" class="home-upload-button">{{ uploading === 'hotzone' ? '上传中…' : '上传或更换图片' }}<input type="file" accept="image/png,image/jpeg" :disabled="disabled || Boolean(uploading)" @change="uploadImage($event, 'hotzone')"></label>
      <AssetPicker kind="IMAGE" :disabled="disabled || Boolean(uploading)" :target-key="JSON.stringify([component.componentId, component.props.assetId])" @select="chooseAsset($event, 'hotzone')" />
      <label>图片素材 ID<input :value="component.props.assetId || ''" placeholder="上传图片或粘贴素材 ID" @input="updateProps({ assetId: ($event.target as HTMLInputElement).value.trim() })"></label>
      <div v-for="(area, index) in component.props.areas || []" :key="index" class="home-subitem">
        <div class="home-subitem-head"><strong>点击区域 {{ index + 1 }}</strong><button type="button" class="text-button danger" @click="updateAreas((component.props.areas || []).filter((_, position) => position !== index))">移除</button></div>
        <div class="home-coordinates">
          <label v-for="key in (['x', 'y', 'width', 'height'] as const)" :key="key">{{ key }}<input type="number" min="0" max="1" step="0.01" :value="area[key]" @change="setAreaNumber(index, key, ($event.target as HTMLInputElement).value)"></label>
        </div>
        <PageLinkEditor :model-value="area.link" :list-id="`${component.componentId}-area-${index}`" :categories="categories" :products="products" :pages="pages" @update:model-value="updateArea(index, { link: $event || { type: 'FUNCTION', targetId: 'CATALOG' } })" />
      </div>
      <button type="button" class="secondary-button home-add-row" @click="addArea">添加点击区域</button>
    </template>

    <template v-else-if="component.type === 'DIVIDER'">
      <label>样式<select :value="component.props.style || 'SOLID'" @change="updateProps({ style: ($event.target as HTMLSelectElement).value as 'SOLID' | 'DASHED' | 'SPACE' })"><option value="SOLID">实线</option><option value="DASHED">虚线</option><option value="SPACE">留白</option></select></label>
    </template>

    <template v-else-if="component.type === 'FILING'">
      <label>备案号<input :value="component.props.recordNo || ''" maxlength="100" placeholder="填写已取得的正式备案号" @input="updateProps({ recordNo: ($event.target as HTMLInputElement).value })"></label>
      <p class="help-text">未取得正式备案号时，可先隐藏此组件，不能填写示例号后发布。</p>
    </template>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </div>
</template>
