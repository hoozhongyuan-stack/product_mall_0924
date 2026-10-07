<script setup lang="ts">
import { computed, inject, nextTick, ref, watch, type Ref } from 'vue'
import ProductMediaEditor from './ProductMediaEditor.vue'
import ProductDescriptionEditor from './ProductDescriptionEditor.vue'
import { api, type Account } from '../../api'
import type { Asset, Category, MemberGrade } from './types'
import SkuTable from './SkuTable.vue'
import { confirmAction } from '../../shared/confirm'
import { combinationKey, generateCombinations, hasDraftSkuInput, toSpecPayload, validateAxes, type EditableAxis, type EditableSku } from './spec-editor'

const props = withDefaults(defineProps<{ categories: Category[]; grades?: MemberGrade[] }>(), { grades: () => [] })
const account = inject<Ref<Account | null>>('admin-account')
const canEdit = computed(() => !!account?.value?.permissionCodes.includes('catalog.write'))
const canUpload = computed(() => !!account?.value?.permissionCodes.includes('asset.upload'))
const emit = defineEmits<{ created: []; cancel: []; dirtyChange: [dirty: boolean]; busyChange: [busy: boolean] }>()
const productNo = ref('')
const name = ref('')
const categoryId = ref('')
const fulfillmentKind = ref<'SHIP' | 'REDEEM'>('SHIP')
const redeemValidUntil = ref('')
const description = ref('')
const mainImage = ref<Asset | null>(null)
const galleryImages = ref<Asset[]>([])
const video = ref<Asset | null>(null)
const mediaBusy = ref(false)
const axes = ref<EditableAxis[]>([])
const generatedAxes = ref<EditableAxis[] | null>(null)
const skus = ref<EditableSku[]>([])
const showFieldErrors = ref(false)
const editorElement = ref<HTMLElement | null>(null)
const saving = ref(false)
const error = ref('')
const dirty = computed(() => Boolean(productNo.value || name.value || categoryId.value || description.value
  || fulfillmentKind.value !== 'SHIP' || redeemValidUntil.value || axes.value.length || skus.value.length || mainImage.value
  || galleryImages.value.length || video.value || mediaBusy.value))
watch(dirty, (value) => emit('dirtyChange', value), { immediate: true })
watch([mediaBusy, saving], () => emit('busyChange', mediaBusy.value || saving.value))

async function reportError(message: string) {
  error.value = message
  showFieldErrors.value = true
  await nextTick()
  editorElement.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
}
function nextKey(prefix: string) { return `${prefix}-${crypto.randomUUID()}` }
function addAxis() {
  if (axes.value.length >= 2 || !canEdit.value || saving.value) return
  axes.value = [...axes.value, { clientKey: nextKey('axis'), name: '', options: [{ clientKey: nextKey('option'), value: '' }] }]
  generatedAxes.value = null
}
function updateAxis(index: number, name: string) {
  axes.value = axes.value.map((axis, position) => position === index ? { ...axis, name } : axis)
  generatedAxes.value = null
}
function removeAxis(index: number) {
  axes.value = axes.value.filter((_, position) => position !== index)
  generatedAxes.value = null
}
function addOption(index: number) {
  axes.value = axes.value.map((axis, position) => position === index ? { ...axis,
    options: [...axis.options, { clientKey: nextKey('option'), value: '' }] } : axis)
  generatedAxes.value = null
}
function updateOption(axisIndex: number, optionIndex: number, value: string) {
  axes.value = axes.value.map((axis, position) => position === axisIndex ? { ...axis,
    options: axis.options.map((option, index) => index === optionIndex ? { ...option, value } : option) } : axis)
  generatedAxes.value = null
}
function removeOption(axisIndex: number, optionIndex: number) {
  axes.value = axes.value.map((axis, position) => position === axisIndex ? { ...axis,
    options: axis.options.filter((_, index) => index !== optionIndex) } : axis)
  generatedAxes.value = null
}
function updateSkus(value: EditableSku[]) { if (canEdit.value && !saving.value) skus.value = value }
async function generateSkus() {
  if (!canEdit.value || saving.value) return
  error.value = ''
  const invalid = validateAxes(axes.value)
  if (invalid) { void reportError(invalid); return }
  const snapshot = axes.value
  const previous = skus.value
  const combinations = generateCombinations(snapshot, previous)
  const nextKeys = new Set(combinations.map(item => combinationKey(item.optionKeys)))
  const discarded = previous.filter(item => !nextKeys.has(combinationKey(item.optionKeys)) && hasDraftSkuInput(item))
  if (discarded.length && !await confirmAction(`${discarded.length} 个 SKU 的已填写资料无法对应新规格，重新生成会丢失这些草稿。确定继续吗？`)) return
  if (!canEdit.value || saving.value || axes.value !== snapshot || skus.value !== previous) return
  generatedAxes.value = snapshot
  skus.value = combinations
}

async function save() {
  if (saving.value || !canEdit.value) return
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(productNo.value.trim()) || !name.value.trim() || !categoryId.value) {
    error.value = '请填写商品编号、名称和启用的二级分类。编号只允许字母、数字、短横线和下划线。'
    return
  }
  if (!generatedAxes.value || !skus.value.length) { error.value = '请先生成 SKU 组合。'; return }
  let specPayload
  try { specPayload = toSpecPayload(generatedAxes.value, skus.value, props.grades, 0) }
  catch (reason) { void reportError(reason instanceof Error ? reason.message : '请检查规格与 SKU 字段。'); return }
  if (mediaBusy.value) { error.value = '请等待素材上传完成后保存。'; return }
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
      specAxes: specPayload.specAxes,
      skus: specPayload.skus,

    }) })
    emit('created')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '商品保存失败。' }
  finally { saving.value = false }
}
</script>

<template>
  <form ref="editorElement" class="panel action-panel catalog-editor" @submit.prevent="save">
    <div class="panel-heading"><div><h3>新建商品草稿</h3><p>先填写商品资料，再生成并填写 SKU。保存后可分别调整状态、等级价和单位。</p></div><button class="text-button" type="button" :disabled="mediaBusy || saving" @click="emit('cancel')">取消</button></div>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <div class="form-grid">
      <label>商品编号<input v-model="productNo" maxlength="64" required placeholder="例如 PROD-001" /></label>
      <label>商品名称<input v-model="name" maxlength="120" required placeholder="请输入商品名称" /></label>
      <label>二级分类<select v-model="categoryId" required><option value="" disabled>请选择</option><option v-for="item in props.categories.filter((row) => row.parentId && row.status === 'ACTIVE' && props.categories.some((parent) => parent.id === row.parentId && parent.status === 'ACTIVE'))" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label>履约方式<select v-model="fulfillmentKind"><option value="SHIP">发货</option><option value="REDEEM">核销</option></select></label>
      <label v-if="fulfillmentKind === 'REDEEM'">核销截止日期（售卖前必填，截止日当天有效）<input v-model="redeemValidUntil" type="date" /></label>
    </div>
    <ProductMediaEditor v-model:main-image="mainImage" v-model:gallery-images="galleryImages" v-model:video="video"
      :target-key="JSON.stringify([account?.accountId, 'new'])" :can-upload="canUpload" :disabled="saving || !canEdit"
      @busy-change="mediaBusy = $event" />
    <div class="catalog-subheading"><h4>规格项</h4><button class="secondary-button" type="button" :disabled="axes.length >= 2 || saving || !canEdit" @click="addAxis">添加规格项</button></div>
    <p class="help-text">最多 2 项，每项最多 20 个值，共 100 个组合。无规格商品直接生成 1 个 SKU。</p>
    <div v-for="(axis, index) in axes" :key="axis.clientKey" class="catalog-spec-axis">
      <div class="catalog-spec-axis-heading"><label>规格项名称<input :value="axis.name" maxlength="60" :disabled="saving || !canEdit" :aria-invalid="showFieldErrors && !axis.name.trim()" placeholder="例如 容量" @input="updateAxis(index, ($event.target as HTMLInputElement).value)" /></label>
      <button class="text-button danger" type="button" :disabled="saving || !canEdit" @click="removeAxis(index)">移除规格项</button></div>
      <div class="catalog-spec-options"><label v-for="(option, optionIndex) in axis.options" :key="option.clientKey">规格值 {{ optionIndex + 1 }}<span class="catalog-spec-option-input"><input :value="option.value" maxlength="60" :disabled="saving || !canEdit" :aria-invalid="showFieldErrors && !option.value.trim()" placeholder="例如 500ml" @input="updateOption(index, optionIndex, ($event.target as HTMLInputElement).value)" /><button class="text-button danger" type="button" :disabled="saving || !canEdit" :aria-label="`移除规格值 ${option.value || optionIndex + 1}`" @click="removeOption(index, optionIndex)">移除</button></span></label></div>
      <button class="secondary-button" type="button" :disabled="axis.options.length >= 20 || saving || !canEdit" @click="addOption(index)">添加规格值</button>
    </div>
    <button class="secondary-button" type="button" :disabled="saving || !canEdit" @click="generateSkus">{{ skus.length ? '重新生成 SKU 组合' : '生成 SKU 组合' }}</button>
    <p v-if="!generatedAxes && skus.length" class="hint">规格已修改，已填写的 SKU 草稿仍保留。请重新生成组合并核对后保存。</p>
    <div v-if="skus.length" class="catalog-sku-drafts"><h4>SKU（{{ skus.length }} 个组合）</h4>
      <p class="help-text">SKU 编码独立于商品编号和规格值；新 SKU 默认下架。日常价以元输入，服务端按分保存。</p>
      <SkuTable :skus="skus" :axes="generatedAxes || axes" :grades="grades" :disabled="saving || !canEdit" :show-errors="showFieldErrors" @update:skus="updateSkus" />
    </div>
    <ProductDescriptionEditor v-model="description" :target-key="JSON.stringify([account?.accountId, 'new'])" :disabled="saving || !canEdit" />
    <p class="help-text">保存后为下架草稿。库存通过库存单据管理；上架前请核对主图、价格与可售库存。</p>
    <button class="primary-button" type="submit" :disabled="saving || mediaBusy || !canEdit || !skus.length">{{ saving ? '保存中…' : '保存商品草稿' }}</button>
  </form>
</template>
