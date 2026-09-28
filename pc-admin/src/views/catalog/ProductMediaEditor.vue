<script setup lang="ts">
import { computed, onUnmounted, ref, useId, watch } from 'vue'
import AssetPicker from '../../shared/AssetPicker.vue'
import { api } from '../../api'
import { validateMediaFile, type Asset } from './types'

type Role = 'main' | 'gallery' | 'video'
const props = defineProps<{
  mainImage: Asset | null
  galleryImages: Asset[]
  video: Asset | null
  targetKey: string
  canUpload: boolean
  disabled?: boolean
}>()
const emit = defineEmits<{
  'update:mainImage': [value: Asset | null]
  'update:galleryImages': [value: Asset[]]
  'update:video': [value: Asset | null]
  busyChange: [value: boolean]
}>()
const titleId = useId()
const uploadingSlot = ref('')
const mediaError = ref('')
const blocked = computed(() => !!props.disabled || !!uploadingSlot.value)
let sequence = 0
function setBusy(slot: string) { uploadingSlot.value = slot; emit('busyChange', !!slot) }
function invalidate() { sequence++; mediaError.value = ''; setBusy('') }
watch(() => [props.targetKey, props.canUpload, props.disabled], invalidate, { flush: 'sync' })
onUnmounted(invalidate)
function slotKey(role: Role) {
  return JSON.stringify([props.targetKey, role, role === 'main' ? props.mainImage?.assetId
    : role === 'video' ? props.video?.assetId : props.galleryImages.map(item => item.assetId)])
}
function applyAsset(asset: Asset, role: Role, index?: number) {
  if (props.disabled) return
  mediaError.value = ''
  if (asset.kind !== (role === 'video' ? 'VIDEO' : 'IMAGE')) { mediaError.value = '素材类型不匹配，请重新选择。'; return }
  if (role === 'main' && (!asset.width || asset.width !== asset.height)) { mediaError.value = '主图需要宽高相等的正方形图片。'; return }
  const duplicate = role === 'main' ? props.galleryImages.some(item => item.assetId === asset.assetId)
    : role === 'gallery' && (props.mainImage?.assetId === asset.assetId || props.galleryImages.some((item, position) => position !== index && item.assetId === asset.assetId))
  if (duplicate) { mediaError.value = '此图片已用于主图或其他附图，请选择不同图片。'; return }
  if (role === 'main') emit('update:mainImage', { ...asset })
  else if (role === 'video') emit('update:video', { ...asset })
  else if (index === undefined) {
    if (props.galleryImages.length >= 8) { mediaError.value = '附图最多 8 张，请先移除一张再添加。'; return }
    emit('update:galleryImages', [...props.galleryImages, { ...asset }])
  } else if (props.galleryImages[index]) {
    emit('update:galleryImages', props.galleryImages.map((item, position) => position === index ? { ...asset } : item))
  }
}
function selectAsset(asset: Asset, role: Role, index?: number) {
  if (!blocked.value) applyAsset(asset, role, index)
}
async function selectMedia(event: Event, role: Role, index?: number) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || blocked.value || !props.canUpload) return
  const current = ++sequence
  const expected = slotKey(role)
  const isCurrent = () => current === sequence && expected === slotKey(role) && !props.disabled && props.canUpload
  setBusy(role === 'gallery' ? `gallery-${index ?? 'new'}` : role)
  mediaError.value = ''
  try {
    const invalid = await validateMediaFile(file, role)
    if (!isCurrent()) return
    if (invalid) { mediaError.value = invalid; return }
    const body = new FormData()
    body.append('file', file)
    body.append('kind', role === 'video' ? 'VIDEO' : 'IMAGE')
    const asset = await api<Asset>('/assets', { method: 'POST', body })
    if (!isCurrent()) return
    applyAsset(asset, role, index)
  } catch (reason) {
    if (isCurrent()) mediaError.value = reason instanceof Error ? reason.message : '素材上传失败，请重试。'
  } finally { if (current === sequence) setBusy('') }
}
function removeMedia(role: Role, index?: number) {
  if (blocked.value) return
  if (role === 'main') emit('update:mainImage', null)
  else if (role === 'video') emit('update:video', null)
  else if (index !== undefined) emit('update:galleryImages', props.galleryImages.filter((_, position) => position !== index))
  mediaError.value = ''
}
</script>

<template>
    <section class="catalog-media-section" :aria-labelledby="titleId">
      <h4 :id="titleId">商品图片与视频</h4>
      <p class="help-text">草稿可暂不上传主图；上架前须有正方形主图。图片支持 JPG、PNG，每张不超过 10 MB；建议宽高至少 800 像素。视频支持 MP4，不超过 50 MB。</p>
      <p v-if="mediaError" class="error notice" role="alert">{{ mediaError }}</p>
      <div class="catalog-media-group"><h5>主图 · 1:1</h5>
        <div class="catalog-media-grid"><div class="catalog-media-slot">
          <img v-if="mainImage" :src="mainImage.adminUrl" alt="当前商品主图" />
          <div v-else class="catalog-media-placeholder">尚无主图</div>
          <div class="catalog-media-controls"><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === 'main' ? '上传中…' : mainImage ? '替换主图' : '上传主图' }}<input class="catalog-media-file" type="file" accept="image/jpeg,image/png" :disabled="blocked" @change="selectMedia($event, 'main')" /></label><AssetPicker kind="IMAGE" square :excluded-ids="galleryImages.map(item => item.assetId)" :disabled="blocked" :target-key="slotKey('main')" @select="selectAsset($event, 'main')" />
            <button v-if="mainImage" class="text-button danger" type="button" :disabled="blocked" @click="removeMedia('main')">移除</button></div>
          <small v-if="mainImage">{{ mainImage.width }} × {{ mainImage.height }} 像素</small>
        </div></div>
      </div>
      <div class="catalog-media-group"><h5>附图 · 最多 8 张</h5><div class="catalog-media-grid">
        <div v-for="(asset, index) in galleryImages" :key="asset.assetId" class="catalog-media-slot"><img :src="asset.adminUrl" :alt="`商品附图 ${index + 1}`" />
          <div class="catalog-media-controls"><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === `gallery-${index}` ? '上传中…' : '替换' }}<input class="catalog-media-file" type="file" accept="image/jpeg,image/png" :disabled="blocked" @change="selectMedia($event, 'gallery', index)" /></label>
            <button class="text-button danger" type="button" :disabled="blocked" @click="removeMedia('gallery', index)">移除</button><AssetPicker kind="IMAGE" :disabled="blocked" :excluded-ids="[mainImage?.assetId || '', ...galleryImages.filter((_, position) => position !== index).map(item => item.assetId)]" :target-key="slotKey('gallery')" @select="selectAsset($event, 'gallery', index)" /></div></div>
        <div v-if="galleryImages.length < 8" class="catalog-media-slot catalog-media-add"><div class="catalog-media-placeholder">添加图片</div><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === 'gallery-new' ? '上传中…' : '选择图片' }}<input class="catalog-media-file" type="file" accept="image/jpeg,image/png" :disabled="blocked" @change="selectMedia($event, 'gallery')" /></label><AssetPicker kind="IMAGE" :disabled="blocked" :excluded-ids="[mainImage?.assetId || '', ...galleryImages.map(item => item.assetId)]" :target-key="slotKey('gallery')" @select="selectAsset($event, 'gallery')" /></div>
      </div></div>
      <div class="catalog-media-group"><h5>主图视频 · 可选</h5><div class="catalog-media-grid"><div class="catalog-media-slot catalog-media-video">
        <video v-if="video" :src="video.adminUrl" controls preload="metadata" aria-label="当前商品视频" />
        <div v-else class="catalog-media-placeholder">尚无视频</div>
        <div class="catalog-media-controls"><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === 'video' ? '上传中…' : video ? '替换视频' : '上传视频' }}<input class="catalog-media-file" type="file" accept="video/mp4" :disabled="blocked" @change="selectMedia($event, 'video')" /></label><AssetPicker kind="VIDEO" :disabled="blocked" :target-key="slotKey('video')" @select="selectAsset($event, 'video')" />
          <button v-if="video" class="text-button danger" type="button" :disabled="blocked" @click="removeMedia('video')">移除</button></div>
      </div></div></div>
    </section>
</template>
