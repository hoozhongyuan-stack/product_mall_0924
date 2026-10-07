<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { confirmAction } from '../../shared/confirm'
import { combinationKey, type EditableAxis, type EditableSku } from './spec-editor'
import { yuanToFen, type MemberGrade } from './types'
import './sku-table.css'

const props = defineProps<{ skus: EditableSku[]; axes: EditableAxis[]; grades: MemberGrade[]; disabled: boolean; showErrors: boolean }>()
const emit = defineEmits<{ 'update:skus': [skus: EditableSku[]] }>()
const selected = ref<string[]>([])
const field = ref('list')
const amount = ref('')
const error = ref('')
const confirming = ref(false)
const grades = computed(() => props.grades.filter(grade => grade.enabled))
const keys = computed(() => props.skus.map(sku => combinationKey(sku.optionKeys)))
const allSelected = computed(() => keys.value.length > 0 && keys.value.every(key => selected.value.includes(key)))
watch(keys, values => { selected.value = selected.value.filter(key => values.includes(key)) })
function select(key: string, checked: boolean) {
  selected.value = checked ? [...new Set([...selected.value, key])] : selected.value.filter(value => value !== key)
}
function optionValue(axis: EditableAxis, sku: EditableSku) {
  return axis.options.find(option => sku.optionKeys.includes(option.clientKey))?.value || '—'
}
function label(sku: EditableSku) { return sku.skuCode || sku.label || '默认规格' }
function update(index: number, patch: Partial<EditableSku>) {
  if (props.disabled || confirming.value) return
  emit('update:skus', props.skus.map((sku, position) => position === index ? { ...sku, ...patch } : sku))
}
function codeInvalid(index: number) {
  const code = props.skus[index]!.skuCode.trim()
  return !/^[A-Za-z0-9_-]{1,64}$/.test(code) || props.skus.some((sku, position) => position !== index && sku.skuCode.trim().toUpperCase() === code.toUpperCase())
}
async function batchPrice() {
  if (props.disabled || confirming.value || !selected.value.length) return
  error.value = ''
  if (yuanToFen(amount.value) === null) { error.value = '价格须为非负金额，最多两位小数。'; return }
  const selectedKeys = [...selected.value]
  const targetField = field.value
  const value = amount.value.trim()
  const snapshot = props.skus
  const selectedRows = snapshot.filter(sku => selectedKeys.includes(combinationKey(sku.optionKeys)))
  const overwritten = selectedRows.filter(sku => (targetField === 'list' ? sku.priceYuan : sku.gradePrices[targetField] || '').trim())
  confirming.value = true
  try {
    if (overwritten.length && !await confirmAction(`将为已选 ${selectedRows.length} 个 SKU 设置${targetField === 'list' ? '日常价' : `${grades.value.find(grade => grade.id === targetField)?.name || ''}价`} ¥${value}，覆盖其中 ${overwritten.length} 个已有值。确认应用吗？`)) return
    if (props.disabled || props.skus !== snapshot || (targetField !== 'list' && !grades.value.some(grade => grade.id === targetField))) return
    emit('update:skus', snapshot.map(sku => !selectedKeys.includes(combinationKey(sku.optionKeys)) ? sku : targetField === 'list'
      ? { ...sku, priceYuan: value } : { ...sku, gradePrices: { ...sku.gradePrices, [targetField]: value } }))
  } finally { confirming.value = false }
}
</script>

<template>
  <div class="sku-table-editor">
    <div v-if="!disabled" class="sku-batch-toolbar" aria-label="批量设置 SKU 价格">
      <span role="status">已选 {{ selected.length }} / {{ skus.length }} 个 SKU</span>
      <label>设置价格<select v-model="field" aria-label="批量价格字段" :disabled="confirming"><option value="list">日常价</option><option v-for="grade in grades" :key="grade.id" :value="grade.id">{{ grade.name }}价</option></select></label>
      <label>金额（元）<input v-model="amount" aria-label="批量价格（元）" inputmode="decimal" placeholder="例如 199.00" :disabled="confirming" /></label>
      <button class="secondary-button" data-action="batch-price" type="button" :disabled="!selected.length || confirming" @click="batchPrice">{{ confirming ? '确认中…' : '应用到所选 SKU' }}</button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p class="help-text">会员价留空沿用日常价。表格可横向滚动；单位及换算关系逐行设置。</p>
    <div class="sku-table-scroll" tabindex="0" aria-label="SKU 明细表，可横向滚动">
      <table class="sku-edit-table" aria-label="SKU 明细">
        <thead><tr><th class="sku-select-cell"><input v-if="!disabled" type="checkbox" aria-label="选择全部 SKU" :checked="allSelected" :indeterminate="selected.length > 0 && !allSelected" :disabled="confirming" @change="selected = ($event.target as HTMLInputElement).checked ? [...keys] : []" /><span v-else>序号</span></th>
          <th class="sku-identity-cell">SKU 编码</th><th v-for="axis in axes" :key="axis.clientKey">{{ axis.name || '规格' }}</th><th v-if="!axes.length">规格</th>
          <th>日常价（元）</th><th v-for="grade in grades" :key="grade.id">{{ grade.name }}价（元）</th><th>基本单位</th><th>销售单位</th><th>换算比／关系</th><th>销售状态</th></tr></thead>
        <tbody><tr v-for="(sku, index) in skus" :key="combinationKey(sku.optionKeys)">
          <td class="sku-select-cell"><input v-if="!disabled" type="checkbox" :aria-label="`选择 SKU ${label(sku)}`" :checked="selected.includes(combinationKey(sku.optionKeys))" :disabled="confirming" @change="select(combinationKey(sku.optionKeys), ($event.target as HTMLInputElement).checked)" /><span v-else>{{ index + 1 }}</span></td>
          <td class="sku-identity-cell"><input :value="sku.skuCode" :aria-label="`SKU 编码 ${sku.label || '默认规格'}`" maxlength="64" required :disabled="disabled || confirming" :aria-invalid="showErrors && codeInvalid(index)" @input="update(index, { skuCode: ($event.target as HTMLInputElement).value })" /><small>{{ sku.id ? '保留现有 SKU' : '新增 SKU' }}</small></td>
          <td v-for="axis in axes" :key="axis.clientKey" class="sku-option-cell">{{ optionValue(axis, sku) }}</td><td v-if="!axes.length">默认规格</td>
          <td><input :value="sku.priceYuan" :aria-label="`日常价 ${label(sku)}`" class="sku-price-input" inputmode="decimal" required :disabled="disabled || confirming" :aria-invalid="showErrors && yuanToFen(sku.priceYuan) === null" @input="update(index, { priceYuan: ($event.target as HTMLInputElement).value })" /></td>
          <td v-for="grade in grades" :key="grade.id"><input :value="sku.gradePrices[grade.id] || ''" :aria-label="`${grade.name}价 ${label(sku)}`" class="sku-price-input" inputmode="decimal" placeholder="沿用日常价" :disabled="disabled || confirming" :aria-invalid="showErrors && !!sku.gradePrices[grade.id]?.trim() && yuanToFen(sku.gradePrices[grade.id]) === null" @input="update(index, { gradePrices: { ...sku.gradePrices, [grade.id]: ($event.target as HTMLInputElement).value } })" /></td>
          <td><input :value="sku.baseUnit" :aria-label="`基本单位 ${label(sku)}`" maxlength="20" required :disabled="disabled || confirming" :aria-invalid="showErrors && !sku.baseUnit.trim()" @input="update(index, { baseUnit: ($event.target as HTMLInputElement).value })" /></td>
          <td><input :value="sku.saleUnit" :aria-label="`销售单位 ${label(sku)}`" maxlength="20" required :disabled="disabled || confirming" :aria-invalid="showErrors && !sku.saleUnit.trim()" @input="update(index, { saleUnit: ($event.target as HTMLInputElement).value })" /></td>
          <td class="sku-conversion-cell"><input :value="sku.ratio" :aria-label="`换算比 ${label(sku)}`" type="number" min="1" step="1" required :disabled="disabled || confirming" :aria-invalid="showErrors && (!Number.isInteger(sku.ratio) || sku.ratio < 1)" @input="update(index, { ratio: Number(($event.target as HTMLInputElement).value) })" /><small>1 {{ sku.saleUnit || '销售单位' }}＝{{ sku.ratio }} {{ sku.baseUnit || '基本单位' }}</small></td>
          <td><span class="sku-sale-state">{{ sku.saleStatus === 'ON_SALE' ? '已上架' : '已下架' }}</span></td>
        </tr></tbody>
      </table>
    </div>
  </div>
</template>
