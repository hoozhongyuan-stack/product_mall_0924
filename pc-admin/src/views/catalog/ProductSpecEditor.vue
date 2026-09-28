<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError, api } from '../../api'
import { yuanToFen, type MemberGrade, type ProductDetail } from './types'
import { axesMatchDetail, combinationKey, generateCombinations, initialAxes, initialSkus, toSpecPayload, validateAxes,
  type EditableAxis, type EditableSku } from './spec-editor'

interface PreviewItem { skuId?: string; skuCode: string; gradePriceCount?: number; unitVersionCount?: number }
interface Preview { previewToken: string; retained: PreviewItem[]; added: PreviewItem[]; removed: PreviewItem[] }
const props = defineProps<{ product: ProductDetail; grades: MemberGrade[]; canEdit: boolean; basicDirty: boolean }>()
const emit = defineEmits<{ saved: []; dirtyChange: [dirty: boolean] }>()
const axes = ref<EditableAxis[]>(initialAxes(props.product))
const skus = ref<EditableSku[]>(initialSkus(props.product))
const generated = ref(true)
const preview = ref<Preview | null>(null)
const confirmedRemoval = ref(false)
const error = ref('')
const busy = ref(false)
const showFieldErrors = ref(false)
const editorElement = ref<HTMLElement | null>(null)
const enabledGrades = computed(() => props.grades.filter((grade) => grade.enabled))
const dirty = computed(() => !axesMatchDetail(axes.value, props.product.specAxes)
  || JSON.stringify(skus.value.map(({ label: _label, ...sku }) => sku)) !==
    JSON.stringify(initialSkus(props.product).map(({ label: _label, ...sku }) => sku)))
watch(dirty, (value) => emit('dirtyChange', value), { immediate: true })
watch(() => props.product, (value) => { preview.value = null; confirmedRemoval.value = false
  if (dirty.value) return
  axes.value = initialAxes(value); skus.value = initialSkus(value)
  generated.value = true; error.value = '' })
watch(() => props.basicDirty, (value) => { if (value) { preview.value = null; confirmedRemoval.value = false } })
const removedDrafts = computed(() => props.product.skus.filter((sku) =>
  !skus.value.some((row) => row.id === sku.skuId)))
function invalidate(regenerate = false) {
  preview.value = null
  confirmedRemoval.value = false
  error.value = ''
  if (regenerate) generated.value = false
}
function codeInvalid(index: number): boolean {
  const code = skus.value[index].skuCode.trim()
  return !/^[A-Za-z0-9_-]{1,64}$/.test(code) ||
    skus.value.some((sku, position) => position !== index && sku.skuCode.trim().toUpperCase() === code.toUpperCase())
}
async function reportError(message: string) {
  error.value = message
  showFieldErrors.value = true
  await nextTick()
  editorElement.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
}
function nextKey(prefix: string) { return `${prefix}-${crypto.randomUUID()}` }
function addAxis() {
  if (axes.value.length >= 2) return
  axes.value = [...axes.value, { clientKey: nextKey('axis'), name: '', options: [{ clientKey: nextKey('option'), value: '' }] }]
  invalidate(true)
}
function removeAxis(index: number) { axes.value = axes.value.filter((_, position) => position !== index); invalidate(true) }
function changeAxis(index: number, name: string) {
  axes.value = axes.value.map((axis, position) => position === index ? { ...axis, name } : axis)
  invalidate(true)
}
function addOption(index: number) {
  axes.value = axes.value.map((axis, position) => position === index
    ? { ...axis, options: [...axis.options, { clientKey: nextKey('option'), value: '' }] } : axis)
  invalidate(true)
}
function changeOption(axisIndex: number, optionIndex: number, value: string) {
  axes.value = axes.value.map((axis, position) => position === axisIndex ? { ...axis,
    options: axis.options.map((option, itemIndex) => itemIndex === optionIndex ? { ...option, value } : option) } : axis)
  invalidate(true)
}
function removeOption(axisIndex: number, optionIndex: number) {
  axes.value = axes.value.map((axis, position) => position === axisIndex
    ? { ...axis, options: axis.options.filter((_, itemIndex) => itemIndex !== optionIndex) } : axis)
  invalidate(true)
}
function updateSku(index: number, patch: Partial<EditableSku>) {
  skus.value = skus.value.map((sku, position) => position === index ? { ...sku, ...patch } : sku)
  invalidate()
}
function updateGrade(index: number, gradeId: string, value: string) {
  const sku = skus.value[index]
  updateSku(index, { gradePrices: { ...sku.gradePrices, [gradeId]: value } })
}
function regenerate() {
  const invalid = validateAxes(axes.value)
  if (invalid) { void reportError(invalid); return }
  const next = generateCombinations(axes.value, skus.value)
  const lostNew = skus.value.filter((sku) => !sku.id && !next.some((item) => combinationKey(item.optionKeys) === combinationKey(sku.optionKeys))
    && (sku.skuCode || sku.priceYuan))
  if (lostNew.length && !window.confirm(`${lostNew.length} 个尚未保存的 SKU 已填写资料无法对应新组合，继续会丢失这些草稿。`)) return
  skus.value = next
  generated.value = true
  invalidate()
}
async function requestPreview() {
  if (!props.canEdit) return
  if (props.basicDirty) { void reportError('商品基础资料尚未保存，请先保存基础资料再核对规格。'); return }
  if (!generated.value) { void reportError('规格项或规格值已修改，请先更新 SKU 组合。'); return }
  let payload
  try { payload = toSpecPayload(axes.value, skus.value, props.grades, props.product.productRevision) }
  catch (reason) { void reportError(reason instanceof Error ? reason.message : '请检查规格和 SKU 字段。'); return }
  busy.value = true; error.value = ''; preview.value = null
  try {
    preview.value = await api<Preview>(`/products/${props.product.productId}/specs/preview`,
      { method: 'POST', body: JSON.stringify(payload) })
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '影响范围核对失败。' }
  finally { busy.value = false }
}
async function save() {
  if (!preview.value || busy.value || (preview.value.removed.length && !confirmedRemoval.value)) return
  if (props.basicDirty) { void reportError('商品基础资料尚未保存，请先保存基础资料再核对规格。'); preview.value = null; return }
  let payload
  try { payload = toSpecPayload(axes.value, skus.value, props.grades, props.product.productRevision) }
  catch (reason) { void reportError(reason instanceof Error ? reason.message : '请检查规格和 SKU 字段。'); return }
  busy.value = true; error.value = ''
  try {
    await api(`/products/${props.product.productId}/specs`, { method: 'PUT',
      body: JSON.stringify({ ...payload, previewToken: preview.value.previewToken }) })
    emit('saved')
  } catch (reason) {
    error.value = reason instanceof ApiError && reason.code === 'REVISION_CONFLICT'
      ? '商品或 SKU 已被其他人修改。已保留当前输入，请重新核对最新数据。'
      : reason instanceof Error ? reason.message : '规格保存失败。'
    preview.value = null
  } finally { busy.value = false }
}
</script>

<template>
  <section ref="editorElement" class="catalog-spec-editor" aria-labelledby="edit-spec-title">
    <div class="catalog-subheading"><h4 id="edit-spec-title">规格 SKU</h4>
      <button v-if="canEdit" class="secondary-button" type="button" :disabled="axes.length >= 2 || busy" @click="addAxis">添加规格项</button></div>
    <p class="help-text">最多 2 个规格项、每项 20 个值，共 100 个组合。规格值与 SKU 编码分别维护；现有组合按规格值 ID 保留 SKU 资料。</p>
    <p v-if="!canEdit" class="hint">规格编辑仅开放给草稿商品，且需要商品编辑、SKU 状态、价格和单位四项权限。</p>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <div v-for="(axis, axisIndex) in axes" :key="axis.clientKey" class="catalog-spec-axis">
      <div class="catalog-spec-axis-heading"><label>规格项 {{ axisIndex + 1 }} 名称<input :value="axis.name" maxlength="60" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && (!axis.name.trim() || axes.some((other, index) => index !== axisIndex && other.name.trim().toUpperCase() === axis.name.trim().toUpperCase()))" placeholder="例如 包装规格" @input="changeAxis(axisIndex, ($event.target as HTMLInputElement).value)" /></label>
        <button v-if="canEdit" class="text-button danger" type="button" :disabled="busy" @click="removeAxis(axisIndex)">移除规格项</button></div>
      <div class="catalog-spec-options"><label v-for="(option, optionIndex) in axis.options" :key="option.clientKey">规格值 {{ optionIndex + 1 }}<span class="catalog-spec-option-input"><input :value="option.value" maxlength="60" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && (!option.value.trim() || axis.options.some((other, index) => index !== optionIndex && other.value.trim().toUpperCase() === option.value.trim().toUpperCase()))" placeholder="例如 单瓶装" @input="changeOption(axisIndex, optionIndex, ($event.target as HTMLInputElement).value)" />
        <button v-if="canEdit" class="text-button danger" type="button" :disabled="busy" :aria-label="`移除规格值 ${option.value || optionIndex + 1}`" @click="removeOption(axisIndex, optionIndex)">移除</button></span></label></div>
      <button v-if="canEdit" class="secondary-button" type="button" :disabled="axis.options.length >= 20 || busy" @click="addOption(axisIndex)">添加规格值</button>
    </div>
    <button v-if="canEdit" class="secondary-button" type="button" :disabled="busy" @click="regenerate">生成 / 更新 SKU 组合</button>
    <p v-if="!generated" class="hint" role="status">规格已改动，请更新组合；现有 SKU 输入会保留到更新时。</p>
    <div v-if="removedDrafts.length" class="catalog-spec-impact" role="status"><strong>当前组合变更将移除 {{ removedDrafts.length }} 个已有 SKU</strong>
      <ul><li v-for="sku in removedDrafts" :key="sku.skuId">{{ sku.skuCode }} · {{ sku.specs.map((item) => `${item.name}：${item.value}`).join(' / ') || '默认规格' }}</li></ul>
      <p>移除后其价格与单位版本也会受影响；请在服务端影响预览中再次核对。</p></div>
    <div v-if="skus.length" class="catalog-sku-drafts"><h4>SKU（{{ skus.length }} 个组合）</h4>
      <p v-if="product.status === 'DRAFT'" class="help-text">草稿中的规格组合均未上架；保存规格后，请从商品列表的 SKU 状态操作上架。</p>
      <p v-else class="help-text">已上架或已下架商品的规格暂不能修改；下列 SKU 销售状态为只读。</p>
      <div v-for="(sku, index) in skus" :key="combinationKey(sku.optionKeys)" class="sku-draft-row"><strong>{{ sku.label || '默认规格' }} <small>{{ sku.id ? '保留现有 SKU' : '新增 SKU' }}</small></strong>
        <div class="form-grid">
          <label>SKU 编码<input :value="sku.skuCode" maxlength="64" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && codeInvalid(index)" required @input="updateSku(index, { skuCode: ($event.target as HTMLInputElement).value })" /></label>
          <label>日常价（元）<input :value="sku.priceYuan" inputmode="decimal" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && yuanToFen(sku.priceYuan) === null" required @input="updateSku(index, { priceYuan: ($event.target as HTMLInputElement).value })" /></label>
          <span class="help-text">销售状态：{{ sku.saleStatus === 'ON_SALE' ? '已上架' : '已下架' }}</span>
          <label v-for="grade in enabledGrades" :key="grade.id">{{ grade.name }}价（元）<input :value="sku.gradePrices[grade.id] || ''" inputmode="decimal" placeholder="留空沿用日常价" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && !!sku.gradePrices[grade.id]?.trim() && yuanToFen(sku.gradePrices[grade.id]) === null" @input="updateGrade(index, grade.id, ($event.target as HTMLInputElement).value)" /></label>
          <label>基本单位<input :value="sku.baseUnit" maxlength="20" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && !sku.baseUnit.trim()" required @input="updateSku(index, { baseUnit: ($event.target as HTMLInputElement).value })" /></label>
          <label>销售单位<input :value="sku.saleUnit" maxlength="20" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && !sku.saleUnit.trim()" required @input="updateSku(index, { saleUnit: ($event.target as HTMLInputElement).value })" /></label>
          <label>换算比<input :value="sku.ratio" type="number" min="1" step="1" :disabled="!canEdit || busy" :aria-invalid="showFieldErrors && (!Number.isInteger(sku.ratio) || sku.ratio < 1)" required @input="updateSku(index, { ratio: Number(($event.target as HTMLInputElement).value) })" /></label>
        </div></div></div>
    <div v-if="canEdit" class="catalog-spec-save"><button class="secondary-button" type="button" :disabled="busy || !dirty || !generated" @click="requestPreview">{{ busy ? '核对中…' : '核对规格变更影响' }}</button>
      <p v-if="basicDirty" class="help-text">先保存商品基础资料，再保存规格与 SKU。</p></div>
    <div v-if="preview" class="catalog-spec-impact" aria-label="服务端变更预览"><strong>服务端核对：保留 {{ preview.retained.length }} 个、新增 {{ preview.added.length }} 个、移除 {{ preview.removed.length }} 个 SKU</strong>
      <ul v-if="preview.removed.length"><li v-for="item in preview.removed" :key="item.skuId">{{ item.skuCode }} · {{ item.gradePriceCount ?? 0 }} 条等级价、{{ item.unitVersionCount ?? 0 }} 条单位版本将被移除</li></ul>
      <label v-if="preview.removed.length" class="catalog-spec-confirm"><input v-model="confirmedRemoval" type="checkbox" />我已核对以上移除影响，确认继续</label>
      <button class="primary-button" type="button" :disabled="busy || (preview.removed.length > 0 && !confirmedRemoval)" @click="save">{{ busy ? '保存中…' : '确认保存规格与 SKU' }}</button></div>
  </section>
</template>
