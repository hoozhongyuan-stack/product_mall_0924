<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import InventorySkuPicker from './InventorySkuPicker.vue'
import { api } from '../../api'
import { parsePositiveQuantity } from './quantity.mjs'
import type { InventoryBalance, InventorySku, OutboundReason, Page, Warehouse } from './types'

type Kind = 'inbound' | 'outbound'
type Line = {
  key: string
  skuId: string
  sku: InventorySku | null
  quantity: string
  unit: 'BASE' | 'SALE'
  onHand: number | null
  reserved: number | null
  available: number | null
  balanceLoading: boolean
  balanceError: string
}
export type StockDraftPayload = {
  warehouseId: string
  reason: string
  note?: string
  items: { skuId: string; quantity: number; unit: 'BASE' | 'SALE' }[]
}

const props = defineProps<{ kind: Kind; warehouses: Warehouse[]; saving: boolean; error: string }>()
const emit = defineEmits<{
  submit: [payload: StockDraftPayload, key: string]
  close: []
  dirty: [value: boolean]
  change: []
}>()
const warehouseId = ref('')
const inboundReason = ref('')
const outboundReason = ref<OutboundReason>('DAMAGE')
const note = ref('')
const lines = ref<Line[]>([newLine()])
const localError = ref('')
const requestKey = ref(crypto.randomUUID())
const touched = ref(false)
const balanceLoadTokens = new Map<string, number>()

function newLine(): Line {
  return { key: crypto.randomUUID(), skuId: '', sku: null, quantity: '', unit: 'BASE',
    onHand: null, reserved: null, available: null, balanceLoading: false, balanceError: '' }
}
const dirty = computed(() => touched.value)
watch(dirty, (value) => emit('dirty', value), { immediate: true })
watch(() => props.warehouses, (rows) => {
  if (!warehouseId.value) warehouseId.value = rows.find((row) => row.enabled && row.isDefault)?.warehouseId
    || rows.find((row) => row.enabled)?.warehouseId || ''
}, { immediate: true })
function changed() { touched.value = true; requestKey.value = crypto.randomUUID(); localError.value = ''; emit('change') }
function setWarehouse(value: string) {
  if (props.saving) return
  warehouseId.value = value
  changed()
  lines.value.forEach((line) => { if (line.skuId) void loadAvailable(line.key, line.skuId, value) })
}
function setLine(key: string, patch: Partial<Line>) {
  if (props.saving) return
  lines.value = lines.value.map((line) => line.key === key ? { ...line, ...patch } : line)
  changed()
}
function addLine() {
  if (props.saving || lines.value.length >= 50) return
  lines.value = [...lines.value, newLine()]
  changed()
}
function removeLine(key: string) {
  if (props.saving || lines.value.length === 1) return
  balanceLoadTokens.delete(key)
  lines.value = lines.value.filter((line) => line.key !== key)
  changed()
}
function chooseSku(line: Line, sku: InventorySku) {
  if (props.saving) return
  setLine(line.key, { skuId: sku.skuId, sku, unit: 'BASE', available: null, balanceError: '' })
  if (warehouseId.value) void loadAvailable(line.key, sku.skuId, warehouseId.value)
}
async function loadAvailable(key: string, skuId: string, selectedWarehouseId: string) {
  const token = (balanceLoadTokens.get(key) || 0) + 1
  balanceLoadTokens.set(key, token)
  lines.value = lines.value.map((line) => line.key === key ? { ...line, onHand: null, reserved: null, available: null, balanceLoading: true, balanceError: '' } : line)
  try {
    const query = new URLSearchParams({ page: '1', pageSize: '1', warehouseId: selectedWarehouseId, skuId })
    const page = await api<Page<InventoryBalance>>(`/inventory/balances?${query}`)
    const balance = page.items[0]
    const available = balance?.availableBaseUnits ?? 0
    if (balanceLoadTokens.get(key) === token && warehouseId.value === selectedWarehouseId) {
      lines.value = lines.value.map((line) => line.key === key && line.skuId === skuId
        ? { ...line, available, onHand: balance?.onHandBaseUnits ?? 0, reserved: balance?.reservedBaseUnits ?? 0, balanceLoading: false } : line)
    }
  } catch (reason) {
    if (balanceLoadTokens.get(key) === token) {
      lines.value = lines.value.map((line) => warehouseId.value === selectedWarehouseId && line.key === key && line.skuId === skuId
        ? { ...line, balanceLoading: false, balanceError: reason instanceof Error ? reason.message : '可售量读取失败。' } : line)
    }
  }
}
function baseQuantity(line: Line) {
  const quantity = parsePositiveQuantity(line.quantity)
  const ratio = line.unit === 'SALE' ? line.sku?.ratio || 0 : 1
  const total = quantity && quantity * ratio
  return total && Number.isSafeInteger(total) ? total : null
}
function insufficient(line: Line) {
  const quantity = baseQuantity(line)
  return line.available !== null && quantity !== null && quantity > line.available
}
function submit() {
  if (props.saving) return
  if (!warehouseId.value || !props.warehouses.some((row) => row.warehouseId === warehouseId.value && row.enabled)) {
    localError.value = '请选择启用中的仓库。'; return
  }
  const reason = props.kind === 'inbound' ? inboundReason.value.trim() : outboundReason.value
  if (!reason) {
    localError.value = '请填写入库来源或原因。'; return
  }
  if (lines.value.length > 50 || lines.value.some((line) => !line.sku || line.sku.skuId !== line.skuId || !baseQuantity(line))) {
    localError.value = '每行均须选择 SKU 并输入正整数数量；换算后数量不得超出允许范围。'; return
  }
  if (new Set(lines.value.map((line) => line.skuId)).size !== lines.value.length) {
    localError.value = '同一 SKU 只能填写一行，请合并数量。'; return
  }
  emit('submit', { warehouseId: warehouseId.value, reason,
    ...(props.kind === 'outbound' ? { note: note.value.trim() } : {}),
    items: lines.value.map((line) => ({ skuId: line.skuId, quantity: parsePositiveQuantity(line.quantity)!, unit: line.unit })) }, requestKey.value)
}
onBeforeUnmount(() => balanceLoadTokens.clear())
</script>

<template>
  <form class="panel inventory-form stock-document-form" @submit.prevent="submit">
    <h3>新建{{ kind === 'inbound' ? '入库' : '人工出库' }}草稿</h3>
    <div class="inventory-form-grid">
      <label><span>{{ kind === 'inbound' ? '入库' : '出库' }}仓库 <span class="required">*</span></span>
        <el-select :model-value="warehouseId" :disabled="saving" placeholder="选择仓库" @update:model-value="setWarehouse">
          <el-option v-for="row in warehouses.filter((item) => item.enabled)" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" />
        </el-select>
      </label>
      <label v-if="kind === 'inbound'"><span>来源 / 原因 <span class="required">*</span></span>
        <el-input :model-value="inboundReason" :disabled="saving" maxlength="200" placeholder="例如 采购入库" @update:model-value="(value: string) => { inboundReason = value; changed() }" />
      </label>
      <label v-else><span>出库原因 <span class="required">*</span></span>
        <el-select :model-value="outboundReason" :disabled="saving" @update:model-value="(value: OutboundReason) => { outboundReason = value; changed() }">
          <el-option label="报损" value="DAMAGE" /><el-option label="样品领用" value="SAMPLE" />
          <el-option label="内部使用" value="INTERNAL" /><el-option label="其他" value="OTHER" />
        </el-select>
      </label>
      <label v-if="kind === 'outbound'">出库说明
        <el-input :model-value="note" :disabled="saving" maxlength="200" placeholder="用途、关联单号或报损依据（选填）" @update:model-value="(value: string) => { note = value; changed() }" />
      </label>
    </div>
    <div class="stock-lines-heading"><h4>商品 / SKU <span class="required">*</span></h4><span>{{ lines.length }} / 50 行</span></div>
    <div v-for="(line, index) in lines" :key="line.key" class="stock-line">
      <div class="stock-line-title"><strong>商品 {{ index + 1 }}</strong><el-button v-if="lines.length > 1" link type="danger" :disabled="saving" :aria-label="`移除第 ${index + 1} 行`" @click="removeLine(line.key)">移除</el-button></div>
      <div class="stock-line-fields">
        <div class="stock-sku-selection"><span class="stock-field-label">商品 / SKU <span class="required">*</span></span>
          <InventorySkuPicker :model-value="line.sku" :warehouse-id="warehouseId" :disabled="saving" :excluded-ids="lines.filter(other => other.key !== line.key).map(other => other.skuId).filter(Boolean)" @select="sku => chooseSku(line, sku)" />
        </div>
        <label><span>操作单位 <span class="required">*</span></span>
          <el-select :model-value="line.unit" :disabled="saving || !line.sku" @update:model-value="(value: 'BASE' | 'SALE') => setLine(line.key, { unit: value })">
            <el-option :label="line.sku ? `基础单位：${line.sku.baseUnit}` : '基础单位'" value="BASE" />
            <el-option v-if="line.sku && line.sku.saleUnit !== line.sku.baseUnit" :label="`销售单位：${line.sku.saleUnit}`" value="SALE" />
          </el-select>
        </label>
        <label><span>录入数量 <span class="required">*</span></span><el-input :model-value="line.quantity" :disabled="saving" inputmode="numeric" placeholder="正整数" @update:model-value="(value: string) => setLine(line.key, { quantity: value })" /></label>
      </div>
      <p v-if="line.sku" class="inventory-line-stock" aria-live="polite">所选仓库：<span v-if="line.balanceLoading">库存读取中…</span><span v-else-if="line.balanceError">库存暂不可用</span><template v-else>账面 {{ line.onHand ?? '—' }} {{ line.sku.baseUnit }} · 锁定 {{ line.reserved ?? '—' }} {{ line.sku.baseUnit }} · 可售 <strong>{{ line.available ?? '—' }} {{ line.sku.baseUnit }}</strong></template></p>
      <p v-if="line.sku" class="inventory-conversion">1 {{ line.unit === 'SALE' ? line.sku.saleUnit : line.sku.baseUnit }} = {{ line.unit === 'SALE' ? line.sku.ratio : 1 }} {{ line.sku.baseUnit }}；本行基础数量：<strong>{{ baseQuantity(line) ?? '—' }} {{ line.sku.baseUnit }}</strong><template v-if="kind === 'outbound'">；当前可售：<strong>{{ line.balanceLoading ? '读取中' : line.available ?? '—' }} {{ line.sku.baseUnit }}</strong><template v-if="insufficient(line)">；<strong class="stock-shortfall" role="alert">库存不足，请调整数量</strong></template><template v-else-if="line.available !== null && baseQuantity(line) !== null">；出库后可售约 <strong>{{ line.available - baseQuantity(line)! }} {{ line.sku.baseUnit }}</strong></template></template></p>
      <p v-if="line.balanceError" class="error" role="alert">{{ line.balanceError }} <el-button link type="primary" @click="loadAvailable(line.key, line.skuId, warehouseId)">重试</el-button></p>
    </div>
    <el-button :disabled="saving || lines.length >= 50" @click="addLine">＋ 添加商品行</el-button>
    <p v-if="kind === 'outbound'" class="help-text">可售量仅供填写参考。确认时服务端会重新读取最新库存；销售订单发货不在此单重复扣减。</p>
    <p v-if="localError || error" class="error" role="alert">{{ localError || error }}</p>
    <div class="inventory-actions"><el-button :disabled="saving" @click="emit('close')">取消</el-button><el-button type="primary" native-type="submit" :loading="saving">保存草稿</el-button></div>
  </form>
</template>
