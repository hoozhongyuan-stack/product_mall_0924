<script setup lang="ts">
import { computed } from 'vue'
import type { EditableAxis } from './spec-editor'
import type { UnitConversion } from './types'
const props = defineProps<{ axes: EditableAxis[]; conversion: UnitConversion | null; disabled: boolean; showErrors: boolean }>()
const emit = defineEmits<{ change: [value: { axes: EditableAxis[]; conversion: UnitConversion | null }] }>()
const unitAxis = computed(() => props.axes.find(axis => axis.clientKey === props.conversion?.axisKey))
function key(prefix: string) { return `${prefix}-${crypto.randomUUID()}` }
function change(axes: EditableAxis[], conversion: UnitConversion | null) {
  if (!props.disabled) emit('change', { axes, conversion })
}
function toggle(enabled: boolean) {
  if (!enabled) { change(props.axes.filter(axis => axis.clientKey !== props.conversion?.axisKey), null); return }
  if (props.axes.length >= 2) return
  const option = { clientKey: key('unit'), value: '' }
  const axis = { clientKey: key('axis'), name: '单位', options: [option] }
  change([...props.axes, axis], { axisKey: axis.clientKey, baseOptionKey: option.clientKey, ratios: [{ optionKey: option.clientKey, ratio: 1 }] })
}
function updateName(optionKey: string, value: string) {
  change(props.axes.map(axis => axis.clientKey === props.conversion?.axisKey ? { ...axis, options: axis.options.map(option => option.clientKey === optionKey ? { ...option, value } : option) } : axis), props.conversion)
}
function updateRatio(optionKey: string, ratio: number) {
  if (props.conversion) change(props.axes, { ...props.conversion, ratios: props.conversion.ratios.map(item => item.optionKey === optionKey ? { ...item, ratio } : item) })
}
function setBase(baseOptionKey: string) {
  if (props.conversion) change(props.axes, { ...props.conversion, baseOptionKey, ratios: props.conversion.ratios.map(item => item.optionKey === baseOptionKey ? { ...item, ratio: 1 } : item) })
}
function addUnit() {
  if (!props.conversion || !unitAxis.value || unitAxis.value.options.length >= 20) return
  const option = { clientKey: key('unit'), value: '' }
  change(props.axes.map(axis => axis.clientKey === props.conversion?.axisKey ? { ...axis, options: [...axis.options, option] } : axis), { ...props.conversion, ratios: [...props.conversion.ratios, { optionKey: option.clientKey, ratio: 1 }] })
}
function removeUnit(optionKey: string) {
  if (!props.conversion || optionKey === props.conversion.baseOptionKey) return
  change(props.axes.map(axis => axis.clientKey === props.conversion?.axisKey ? { ...axis, options: axis.options.filter(option => option.clientKey !== optionKey) } : axis), { ...props.conversion, ratios: props.conversion.ratios.filter(item => item.optionKey !== optionKey) })
}
function ratio(optionKey: string) { return props.conversion?.ratios.find(item => item.optionKey === optionKey)?.ratio ?? 1 }
const baseName = computed(() => unitAxis.value?.options.find(option => option.clientKey === props.conversion?.baseOptionKey)?.value || '基本单位')
</script>
<template>
  <section class="unit-conversion-editor" aria-label="单位换算设置">
    <label class="catalog-spec-confirm"><input type="checkbox" aria-label="开启多单位换算" :checked="!!conversion" :disabled="disabled || (!conversion && axes.length >= 2)" @change="toggle(($event.target as HTMLInputElement).checked)" />开启多单位换算</label>
    <p class="help-text">单位作为一个规格项，每个单位生成对应 SKU；库存统一按基本单位记账。单位在最多 2 个规格项的限制内。</p>
    <p v-if="!conversion && axes.length >= 2" class="hint">已有 2 个规格项，请先移除一个规格项，再开启多单位换算。</p>
    <template v-if="conversion && unitAxis">
      <label>基本单位<select aria-label="基本单位选择" :value="conversion.baseOptionKey" :disabled="disabled" @change="setBase(($event.target as HTMLSelectElement).value)"><option v-for="(option, index) in unitAxis.options" :key="option.clientKey" :value="option.clientKey">{{ option.value || `单位 ${index + 1}` }}</option></select></label>
      <div class="sku-table-scroll"><table class="unit-conversion-table" aria-label="单位换算明细"><thead><tr><th>单位规格值</th><th>换算比</th><th>换算关系</th><th>操作</th></tr></thead><tbody>
        <tr v-for="(option, index) in unitAxis.options" :key="option.clientKey"><td><input :aria-label="`单位名称 ${index + 1}`" :value="option.value" maxlength="30" :disabled="disabled" :aria-invalid="showErrors && (!option.value.trim() || unitAxis.options.some(other => other.clientKey !== option.clientKey && other.value.trim() === option.value.trim()))" placeholder="例如 瓶、箱" @input="updateName(option.clientKey, ($event.target as HTMLInputElement).value)" /></td>
          <td><input :aria-label="`单位换算比 ${index + 1}`" :value="ratio(option.clientKey)" type="number" min="1" max="1000000000" step="1" :disabled="disabled || option.clientKey === conversion.baseOptionKey" :aria-invalid="showErrors && (!Number.isSafeInteger(ratio(option.clientKey)) || ratio(option.clientKey) < 1 || ratio(option.clientKey) > 1000000000)" @input="updateRatio(option.clientKey, Number(($event.target as HTMLInputElement).value))" /></td>
          <td>1 {{ option.value || '单位' }}＝{{ ratio(option.clientKey) }} {{ baseName }}</td><td><span v-if="option.clientKey === conversion.baseOptionKey">基本单位</span><button v-else class="text-button danger" type="button" :disabled="disabled" :aria-label="`移除单位 ${option.value || index + 1}`" @click="removeUnit(option.clientKey)">移除</button></td></tr>
      </tbody></table></div>
      <button class="secondary-button" type="button" :disabled="disabled || unitAxis.options.length >= 20" @click="addUnit">添加单位</button>
    </template>
  </section>
</template>
<style scoped>
.unit-conversion-editor { margin-block: 24px; }
.unit-conversion-editor > label { display: flex; align-items: center; gap: 12px; }
.unit-conversion-editor select { width: min(240px, 100%); }
.unit-conversion-table { width: 100%; min-width: 560px; margin-block: 16px; border-collapse: collapse; }
.unit-conversion-table th, .unit-conversion-table td { padding: 12px; text-align: left; border-bottom: 1px solid var(--mall-color-border); }
.unit-conversion-table th { background: var(--mall-color-canvas-admin); }
.unit-conversion-table td input { width: 100%; min-width: 96px; }
</style>
