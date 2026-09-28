<script setup lang="ts">
import { computed, inject, ref, watch, type Ref } from 'vue'
import AssetPicker from '../../shared/AssetPicker.vue'
import { api, type Account } from '../../api'
import type { Asset, Category, SpecAxis } from './types'
import { validateMediaFile, yuanToFen } from './types'

const props = defineProps<{ categories: Category[] }>()
const account = inject<Ref<Account | null>>('admin-account')
const canUpload = computed(() => !!account?.value?.permissionCodes.includes('asset.upload'))
const emit = defineEmits<{ created: []; cancel: []; dirtyChange: [dirty: boolean] }>()
interface AxisInput { name: string; values: string }
interface DraftSku { label: string; optionKeys: string[]; skuCode: string; priceYuan: string; baseUnit: string; saleUnit: string; ratio: number }
const productNo = ref('')
const name = ref('')
const categoryId = ref('')
const fulfillmentKind = ref<'SHIP' | 'REDEEM'>('SHIP')
const redeemValidUntil = ref('')
const description = ref('')
const mainImage = ref<Asset | null>(null)
const galleryImages = ref<Asset[]>([])
const video = ref<Asset | null>(null)
const uploadingSlot = ref('')
const mediaError = ref('')
const axes = ref<AxisInput[]>([])
const generatedAxes = ref<SpecAxis[] | null>(null)
const skus = ref<DraftSku[]>([])
const saving = ref(false)
const error = ref('')
const dirty = computed(() => Boolean(productNo.value || name.value || categoryId.value || description.value
  || fulfillmentKind.value !== 'SHIP' || redeemValidUntil.value || axes.value.length || skus.value.length || mainImage.value
  || galleryImages.value.length || video.value || uploadingSlot.value))
watch(dirty, (value) => emit('dirtyChange', value), { immediate: true })

async function selectMedia(event: Event, role: 'main' | 'gallery' | 'video', index?: number) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || uploadingSlot.value || !canUpload.value) return
  if (role === 'gallery' && index === undefined && galleryImages.value.length >= 8) {
    mediaError.value = '附图最多 8 张，请先移除一张再添加。'
    return
  }
  const slot = role === 'gallery' ? `gallery-${index ?? 'new'}` : role
  uploadingSlot.value = slot
  mediaError.value = ''
  try {
    const invalid = await validateMediaFile(file, role)
    if (invalid) { mediaError.value = invalid; return }
    const body = new FormData()
    body.append('file', file)
    body.append('kind', role === 'video' ? 'VIDEO' : 'IMAGE')
    const asset = await api<Asset>('/assets', { method: 'POST', body })
    if (role === 'main') mainImage.value = asset
    else if (role === 'video') video.value = asset
    else if (index === undefined) galleryImages.value = [...galleryImages.value, asset]
    else galleryImages.value = galleryImages.value.map((item, position) => position === index ? asset : item)
  } catch (reason) { mediaError.value = reason instanceof Error ? reason.message : '素材上传失败，请重试。' }
  finally { uploadingSlot.value = '' }
}

function removeMedia(role: 'main' | 'gallery' | 'video', index?: number) {
  if (role === 'main') mainImage.value = null
  else if (role === 'video') video.value = null
  else if (index !== undefined) galleryImages.value = galleryImages.value.filter((_, position) => position !== index)
  mediaError.value = ''
}

function addAxis() {
  if (axes.value.length < 2) axes.value = [...axes.value, { name: '', values: '' }]
  generatedAxes.value = null
}
function updateAxis(index: number, key: keyof AxisInput, value: string) {
  axes.value = axes.value.map((axis, position) => position === index ? { ...axis, [key]: value } : axis)
  generatedAxes.value = null
}
function removeAxis(index: number) {
  axes.value = axes.value.filter((_, position) => position !== index)
  generatedAxes.value = null
}
function updateSku(index: number, key: keyof DraftSku, value: string | number) {
  skus.value = skus.value.map((sku, position) => position === index ? { ...sku, [key]: value } : sku)
}

function generateSkus() {
  error.value = ''
  const names = axes.value.map((axis) => axis.name.trim())
  if (names.some((value) => !value) || new Set(names).size !== names.length) {
    error.value = '规格项名称不能为空或重复。'
    return
  }
  const parsed = axes.value.map((axis, axisIndex) => {
    const values = axis.values.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean)
    return { clientKey: `axis-${axisIndex}`, name: axis.name.trim(), sortOrder: axisIndex,
      options: values.map((value, optionIndex) => ({ clientKey: `option-${axisIndex}-${optionIndex}`, value, sortOrder: optionIndex })) }
  })
  if (parsed.some((axis) => !axis.options.length || axis.options.length > 20 || new Set(axis.options.map((option) => option.value)).size !== axis.options.length)) {
    error.value = '每个规格项须有 1—20 个不重复的值，用逗号分隔。'
    return
  }
  if (parsed.reduce((count, axis) => count * axis.options.length, 1) > 100) {
    error.value = 'SKU 组合最多 100 个，请减少规格值。'
    return
  }
  const combinations = parsed.reduce<{ label: string; optionKeys: string[] }[]>((items, axis) =>
    items.flatMap((item) => axis.options.map((option) => ({ label: [...(item.label ? [item.label] : []), option.value].join(' / '), optionKeys: [...item.optionKeys, option.clientKey || ''] }))),
  [{ label: '', optionKeys: [] }])
  const keyOf = (item: { optionKeys: string[] }) => item.optionKeys.join(':') || 'single'
  const previous = new Map(skus.value.map((item) => [keyOf(item), item]))
  const nextKeys = new Set(combinations.map(keyOf))
  const discarded = skus.value.filter((item) => !nextKeys.has(keyOf(item)) && (item.skuCode || item.priceYuan))
  if (discarded.length && !window.confirm(`${discarded.length} 个 SKU 的已填写资料无法对应新规格，重新生成会丢失这些草稿。确定继续吗？`)) return
  generatedAxes.value = parsed
  skus.value = combinations.map((item) => ({ skuCode: '', priceYuan: '', baseUnit: '件', saleUnit: '件', ratio: 1,
    ...previous.get(keyOf(item)), ...item }))
}

async function save() {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(productNo.value.trim()) || !name.value.trim() || !categoryId.value) {
    error.value = '请填写商品编号、名称和启用的二级分类。编号只允许字母、数字、短横线和下划线。'
    return
  }
  if (!generatedAxes.value || !skus.value.length) { error.value = '请先生成 SKU 组合。'; return }
  const codes = skus.value.map((sku) => sku.skuCode.trim())
  if (codes.some((code) => !/^[A-Za-z0-9_-]{1,64}$/.test(code)) || new Set(codes.map((code) => code.toUpperCase())).size !== codes.length) {
    error.value = '每个 SKU 都需要不重复的编码（字母、数字、短横线或下划线）。'
    return
  }
  const prices = skus.value.map((sku) => yuanToFen(sku.priceYuan))
  if (prices.some((price) => price === null) || skus.value.some((sku) => !sku.baseUnit.trim() || !sku.saleUnit.trim() || !Number.isInteger(sku.ratio) || sku.ratio < 1)) {
    error.value = '请填写有效的 SKU 金额和单位；金额最多两位小数，换算比须为正整数。'
    return
  }
  if (uploadingSlot.value) { error.value = '请等待素材上传完成后保存。'; return }
  saving.value = true
  error.value = ''
  try {
    await api('/products', { method: 'POST', body: JSON.stringify({
      productNo: productNo.value.trim(), name: name.value.trim(), categoryId: categoryId.value,
      fulfillmentKind: fulfillmentKind.value, descriptionHtml: description.value.trim(),
      redeemValidUntil: fulfillmentKind.value === 'REDEEM' ? redeemValidUntil.value || null : null,
      mainImageAssetId: mainImage.value?.assetId ?? null,
      galleryAssetIds: galleryImages.value.map((item) => item.assetId),
      videoAssetId: video.value?.assetId ?? null,
      specAxes: generatedAxes.value,
      skus: skus.value.map((sku, index) => ({ skuCode: sku.skuCode.trim(), specOptionKeys: sku.optionKeys,
        listPriceFen: prices[index], saleStatus: 'OFF_SALE', gradePrices: [],
        unit: { baseUnit: sku.baseUnit.trim(), saleUnit: sku.saleUnit.trim(), ratio: sku.ratio } })),
    }) })
    emit('created')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '商品保存失败。' }
  finally { saving.value = false }
}
</script>

<template>
  <form class="panel action-panel catalog-editor" @submit.prevent="save">
    <div class="panel-heading"><div><h3>新建商品草稿</h3><p>先填写商品资料，再生成并填写 SKU。保存后可分别调整状态、等级价和单位。</p></div><button class="text-button" type="button" @click="emit('cancel')">取消</button></div>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <div class="form-grid">
      <label>商品编号<input v-model="productNo" maxlength="64" required placeholder="例如 PROD-001" /></label>
      <label>商品名称<input v-model="name" maxlength="120" required placeholder="请输入商品名称" /></label>
      <label>二级分类<select v-model="categoryId" required><option value="" disabled>请选择</option><option v-for="item in props.categories.filter((row) => row.parentId && row.status === 'ACTIVE' && props.categories.some((parent) => parent.id === row.parentId && parent.status === 'ACTIVE'))" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label>履约方式<select v-model="fulfillmentKind"><option value="SHIP">发货</option><option value="REDEEM">核销</option></select></label>
      <label v-if="fulfillmentKind === 'REDEEM'">核销截止日期（售卖前必填，截止日当天有效）<input v-model="redeemValidUntil" type="date" /></label>
    </div>
    <section class="catalog-media-section" aria-labelledby="create-media-title">
      <h4 id="create-media-title">商品图片与视频</h4>
      <p class="help-text">草稿可暂不上传主图；上架前须有正方形主图。图片支持 JPG、PNG，每张不超过 10 MB；建议宽高至少 800 像素。视频支持 MP4，不超过 50 MB。</p>
      <p v-if="mediaError" class="error notice" role="alert">{{ mediaError }}</p>
      <div class="catalog-media-group"><h5>主图 · 1:1</h5>
        <div class="catalog-media-grid"><div class="catalog-media-slot">
          <img v-if="mainImage" :src="mainImage.adminUrl" alt="当前商品主图" />
          <div v-else class="catalog-media-placeholder">尚无主图</div>
          <div class="catalog-media-controls"><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === 'main' ? '上传中…' : mainImage ? '替换主图' : '上传主图' }}<input class="catalog-media-file" type="file" accept="image/jpeg,image/png" :disabled="!!uploadingSlot || saving" @change="selectMedia($event, 'main')" /></label><AssetPicker kind="IMAGE" square :excluded-ids="galleryImages.map(item => item.assetId)" :disabled="!!uploadingSlot || saving" :target-key="JSON.stringify(['', mainImage?.assetId])" @select="mainImage = $event" />
            <button v-if="mainImage" class="text-button danger" type="button" :disabled="!!uploadingSlot || saving" @click="removeMedia('main')">移除</button></div>
          <small v-if="mainImage">{{ mainImage.width }} × {{ mainImage.height }} 像素</small>
        </div></div>
      </div>
      <div class="catalog-media-group"><h5>附图 · 最多 8 张</h5><div class="catalog-media-grid">
        <div v-for="(asset, index) in galleryImages" :key="asset.assetId" class="catalog-media-slot"><img :src="asset.adminUrl" :alt="`商品附图 ${index + 1}`" />
          <div class="catalog-media-controls"><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === `gallery-${index}` ? '上传中…' : '替换' }}<input class="catalog-media-file" type="file" accept="image/jpeg,image/png" :disabled="!!uploadingSlot || saving" @change="selectMedia($event, 'gallery', index)" /></label>
            <button class="text-button danger" type="button" :disabled="!!uploadingSlot || saving" @click="removeMedia('gallery', index)">移除</button><AssetPicker kind="IMAGE" :disabled="!!uploadingSlot || saving" :excluded-ids="[mainImage?.assetId || '', ...galleryImages.filter((_, position) => position !== index).map(item => item.assetId)]" :target-key="JSON.stringify(['', galleryImages.map(item => item.assetId)])" @select="galleryImages = galleryImages.map((item, position) => position === index ? $event : item)" /></div></div>
        <div v-if="galleryImages.length < 8" class="catalog-media-slot catalog-media-add"><div class="catalog-media-placeholder">添加图片</div><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === 'gallery-new' ? '上传中…' : '选择图片' }}<input class="catalog-media-file" type="file" accept="image/jpeg,image/png" :disabled="!!uploadingSlot || saving" @change="selectMedia($event, 'gallery')" /></label><AssetPicker kind="IMAGE" :disabled="!!uploadingSlot || saving" :excluded-ids="[mainImage?.assetId || '', ...galleryImages.map(item => item.assetId)]" :target-key="JSON.stringify(['', galleryImages.map(item => item.assetId)])" @select="galleryImages = [...galleryImages, $event]" /></div>
      </div></div>
      <div class="catalog-media-group"><h5>主图视频 · 可选</h5><div class="catalog-media-grid"><div class="catalog-media-slot catalog-media-video">
        <video v-if="video" :src="video.adminUrl" controls preload="metadata" aria-label="当前商品视频" />
        <div v-else class="catalog-media-placeholder">尚无视频</div>
        <div class="catalog-media-controls"><label v-if="canUpload" class="secondary-button catalog-media-picker">{{ uploadingSlot === 'video' ? '上传中…' : video ? '替换视频' : '上传视频' }}<input class="catalog-media-file" type="file" accept="video/mp4" :disabled="!!uploadingSlot || saving" @change="selectMedia($event, 'video')" /></label><AssetPicker kind="VIDEO" :disabled="!!uploadingSlot || saving" :target-key="JSON.stringify(['', video?.assetId])" @select="video = $event" />
          <button v-if="video" class="text-button danger" type="button" :disabled="!!uploadingSlot || saving" @click="removeMedia('video')">移除</button></div>
      </div></div></div>
    </section>
    <div class="catalog-subheading"><h4>规格项</h4><button class="secondary-button" type="button" :disabled="axes.length >= 2" @click="addAxis">添加规格项</button></div>
    <p class="help-text">最多 2 项，每项最多 20 个值。无规格商品直接生成 1 个 SKU。</p>
    <div v-for="(axis, index) in axes" :key="index" class="spec-axis-row">
      <label>规格项名称<input :value="axis.name" maxlength="60" placeholder="例如 容量" @input="updateAxis(index, 'name', ($event.target as HTMLInputElement).value)" /></label>
      <label>规格值（逗号分隔）<input :value="axis.values" placeholder="例如 500ml, 1000ml" @input="updateAxis(index, 'values', ($event.target as HTMLInputElement).value)" /></label>
      <button class="text-button danger" type="button" @click="removeAxis(index)">移除</button>
    </div>
    <button class="secondary-button" type="button" @click="generateSkus">{{ skus.length ? '重新生成 SKU 组合' : '生成 SKU 组合' }}</button>
    <p v-if="!generatedAxes && skus.length" class="hint">规格已修改，已填写的 SKU 草稿仍保留。请重新生成组合并核对后保存。</p>
    <div v-if="skus.length" class="catalog-sku-drafts"><h4>SKU（{{ skus.length }} 个组合）</h4>
      <p class="help-text">SKU 编码独立于商品编号和规格值；新 SKU 默认下架。日常价以元输入，服务端按分保存。</p>
      <div v-for="(sku, index) in skus" :key="sku.optionKeys.join(':') || 'single'" class="sku-draft-row">
        <strong>{{ sku.label || '默认规格' }}</strong>
        <div class="form-grid">
          <label>SKU 编码<input :value="sku.skuCode" maxlength="64" required placeholder="例如 SKU-001" @input="updateSku(index, 'skuCode', ($event.target as HTMLInputElement).value)" /></label>
          <label>日常价（元）<input :value="sku.priceYuan" inputmode="decimal" required placeholder="例如 199.00" @input="updateSku(index, 'priceYuan', ($event.target as HTMLInputElement).value)" /></label>
          <label>基本单位<input :value="sku.baseUnit" maxlength="20" required @input="updateSku(index, 'baseUnit', ($event.target as HTMLInputElement).value)" /></label>
          <label>销售单位<input :value="sku.saleUnit" maxlength="20" required @input="updateSku(index, 'saleUnit', ($event.target as HTMLInputElement).value)" /></label>
          <label>换算比<input :value="sku.ratio" type="number" min="1" step="1" required @input="updateSku(index, 'ratio', Number(($event.target as HTMLInputElement).value))" /></label>
        </div>
      </div>
    </div>
    <section class="catalog-description-section"><h4>商品详情</h4><label>商品描述（允许基础 HTML，服务端会过滤）<textarea v-model="description" rows="4" placeholder="可留空，后续编辑商品资料" /></label></section>
    <p class="help-text">阶段 A 不开放购买，也不显示虚构库存。</p>
    <button class="primary-button" type="submit" :disabled="saving || !!uploadingSlot || !skus.length">{{ saving ? '保存中…' : '保存商品草稿' }}</button>
  </form>
</template>
