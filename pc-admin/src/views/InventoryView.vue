<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave } from 'vue-router'
import { api, ApiError, type Account } from '../api'
import StockDocumentForm, { type StockDraftPayload } from './inventory/StockDocumentForm.vue'
import StocktakePanel from './inventory/StocktakePanel.vue'
import { csvTable } from './inventory/csv.mjs'
import type { InboundDetail, InboundSummary, InventoryBalance, InventoryLedger, OutboundDetail, OutboundSummary, Page, Warehouse, WarehouseList } from './inventory/types'
import './inventory/inventory.css'

const props = defineProps<{ account: Account }>()
type Tab = 'balances' | 'warehouses' | 'inbounds' | 'outbounds' | 'stocktakes' | 'ledgers'
const tab = ref<Tab>('balances')
const canManage = computed(() => props.account.permissionCodes.includes('inventory.manage'))
const canReview = computed(() => props.account.permissionCodes.includes('inventory.review'))
const warehouses = ref<Warehouse[]>([])
const warehouseLoading = ref(false)
const warehouseError = ref('')
const balances = ref<Page<InventoryBalance>>({ items: [], page: 1, pageSize: 20, total: 0 })
const balanceLoading = ref(false)
const balanceError = ref('')
const balanceKeyword = ref('')
const balanceWarehouseId = ref('')
const inbounds = ref<Page<InboundSummary>>({ items: [], page: 1, pageSize: 20, total: 0 })
const inboundLoading = ref(false)
const inboundError = ref('')
const selectedInbound = ref<InboundDetail | null>(null)
const detailLoading = ref(false)
const outbounds = ref<Page<OutboundSummary>>({ items: [], page: 1, pageSize: 20, total: 0 })
const outboundLoading = ref(false)
const outboundError = ref('')
const selectedOutbound = ref<OutboundDetail | null>(null)
const outboundDetailLoading = ref(false)
const ledgers = ref<Page<InventoryLedger>>({ items: [], page: 1, pageSize: 20, total: 0 })
const ledgerLoading = ref(false)
const ledgerError = ref('')
const selectedLedger = ref<InventoryLedger | null>(null)
const ledgerDetailLoading = ref(false)
const ledgerKeyword = ref('')
const ledgerWarehouseId = ref('')
const ledgerMovementType = ref('')
const createWarehouseOpen = ref(false)
const warehouseCode = ref('')
const warehouseName = ref('')
const warehouseSaving = ref(false)
const warehouseFormError = ref('')
const inboundOpen = ref(false)
const inboundDirty = ref(false)
const formError = ref('')
const draftSaving = ref(false)
const confirmSaving = ref(false)
const outboundOpen = ref(false)
const outboundDirty = ref(false)
const outboundFormError = ref('')
const outboundDraftSaving = ref(false)
const outboundConfirmSaving = ref(false)
const stocktakeDirty = ref(false)
let balanceLoadSequence = 0
let inboundLoadSequence = 0
let outboundLoadSequence = 0
let ledgerLoadSequence = 0
const confirmationKeys = new Map<string, string>()

const canCreateDraft = computed(() => canManage.value && warehouses.value.some((row) => row.enabled))
const dirty = computed(() => (createWarehouseOpen.value && !!(warehouseCode.value.trim() || warehouseName.value.trim()))
  || (inboundOpen.value && inboundDirty.value) || (outboundOpen.value && outboundDirty.value) || stocktakeDirty.value)
const pageTotals = computed(() => balances.value.items.reduce((result, row) => ({
  onHand: result.onHand + row.onHandBaseUnits,
  reserved: result.reserved + row.reservedBaseUnits,
  available: result.available + row.availableBaseUnits,
}), { onHand: 0, reserved: 0, available: 0 }))

function readableError(reason: unknown) {
  if (reason instanceof ApiError && reason.status === 409) return reason.message
  return reason instanceof Error ? reason.message : '请求失败，请稍后重试。'
}
function formatTime(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function warehouseLabel(id: string) { return warehouses.value.find((item) => item.warehouseId === id)?.name || id }

async function loadWarehouses() {
  warehouseLoading.value = true
  warehouseError.value = ''
  try {
    const result = await api<WarehouseList>('/warehouses')
    warehouses.value = result.items
  } catch (reason) { warehouseError.value = readableError(reason) }
  finally { warehouseLoading.value = false }
}
async function loadBalances(page = 1) {
  const sequence = ++balanceLoadSequence
  balanceLoading.value = true
  balanceError.value = ''
  const query = new URLSearchParams({ page: String(page), pageSize: '20' })
  if (balanceKeyword.value.trim()) query.set('keyword', balanceKeyword.value.trim())
  if (balanceWarehouseId.value) query.set('warehouseId', balanceWarehouseId.value)
  try {
    const result = await api<Page<InventoryBalance>>(`/inventory/balances?${query}`)
    if (sequence === balanceLoadSequence) balances.value = result
  } catch (reason) { if (sequence === balanceLoadSequence) balanceError.value = readableError(reason) }
  finally { if (sequence === balanceLoadSequence) balanceLoading.value = false }
}
async function loadInbounds(page = 1) {
  const sequence = ++inboundLoadSequence
  inboundLoading.value = true
  inboundError.value = ''
  try {
    const result = await api<Page<InboundSummary>>(`/inventory/inbounds?page=${page}&pageSize=20`)
    if (sequence === inboundLoadSequence) inbounds.value = result
  } catch (reason) { if (sequence === inboundLoadSequence) inboundError.value = readableError(reason) }
  finally { if (sequence === inboundLoadSequence) inboundLoading.value = false }
}
async function loadOutbounds(page = 1) {
  const sequence = ++outboundLoadSequence
  outboundLoading.value = true
  outboundError.value = ''
  try {
    const result = await api<Page<OutboundSummary>>(`/inventory/outbounds?page=${page}&pageSize=20`)
    if (sequence === outboundLoadSequence) outbounds.value = result
  } catch (reason) { if (sequence === outboundLoadSequence) outboundError.value = readableError(reason) }
  finally { if (sequence === outboundLoadSequence) outboundLoading.value = false }
}
async function loadLedgers(page = 1) {
  const sequence = ++ledgerLoadSequence
  ledgerLoading.value = true
  ledgerError.value = ''
  selectedLedger.value = null
  const query = new URLSearchParams({ page: String(page), pageSize: '20' })
  if (ledgerKeyword.value.trim()) query.set('keyword', ledgerKeyword.value.trim())
  if (ledgerWarehouseId.value) query.set('warehouseId', ledgerWarehouseId.value)
  if (ledgerMovementType.value) query.set('movementType', ledgerMovementType.value)
  try {
    const result = await api<Page<InventoryLedger>>(`/inventory/ledgers?${query}`)
    if (sequence === ledgerLoadSequence) ledgers.value = result
  } catch (reason) { if (sequence === ledgerLoadSequence) ledgerError.value = readableError(reason) }
  finally { if (sequence === ledgerLoadSequence) ledgerLoading.value = false }
}
function openInbound() {
  if (!canCreateDraft.value) return
  selectedInbound.value = null
  inboundOpen.value = true
  formError.value = ''
  inboundDirty.value = false
}
function openOutbound() {
  if (!canCreateDraft.value) return
  selectedOutbound.value = null
  outboundOpen.value = true
  outboundFormError.value = ''
  outboundDirty.value = false
}
async function createWarehouse() {
  if (!canManage.value || warehouseSaving.value) return
  const code = warehouseCode.value.trim().toUpperCase()
  const name = warehouseName.value.trim()
  if (!code || !name) { warehouseFormError.value = '请填写仓库编号和名称。'; return }
  warehouseSaving.value = true
  warehouseFormError.value = ''
  try {
    await api<Warehouse>('/warehouses', { method: 'POST', body: JSON.stringify({ code, name, isDefault: warehouses.value.length === 0 }) })
    createWarehouseOpen.value = false
    warehouseCode.value = ''
    warehouseName.value = ''
    ElMessage.success('仓库已创建')
    await Promise.all([loadWarehouses(), loadBalances(1)])
  } catch (reason) { warehouseFormError.value = readableError(reason) }
  finally { warehouseSaving.value = false }
}
async function createInbound(payload: StockDraftPayload, key: string) {
  if (!canManage.value || draftSaving.value) return
  draftSaving.value = true
  formError.value = ''
  try {
    const detail = await api<InboundDetail>('/inventory/inbounds', {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(payload),
    })
    selectedInbound.value = detail
    inboundOpen.value = false
    inboundDirty.value = false
    ElMessage.success('入库草稿已保存，库存尚未增加')
    await loadInbounds(1)
  } catch (reason) { formError.value = readableError(reason) }
  finally { draftSaving.value = false }
}
async function createOutbound(payload: StockDraftPayload, key: string) {
  if (!canManage.value || outboundDraftSaving.value) return
  outboundDraftSaving.value = true
  outboundFormError.value = ''
  try {
    const detail = await api<OutboundDetail>('/inventory/outbounds', {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(payload),
    })
    selectedOutbound.value = detail
    outboundOpen.value = false
    outboundDirty.value = false
    ElMessage.success('出库草稿已保存，库存尚未扣减')
    await loadOutbounds(1)
  } catch (reason) { outboundFormError.value = readableError(reason) }
  finally { outboundDraftSaving.value = false }
}
async function openInboundDetail(id: string) {
  detailLoading.value = true
  inboundError.value = ''
  selectedInbound.value = null
  try { selectedInbound.value = await api<InboundDetail>(`/inventory/inbounds/${encodeURIComponent(id)}`) }
  catch (reason) { inboundError.value = readableError(reason) }
  finally { detailLoading.value = false }
}
async function confirmInbound() {
  const detail = selectedInbound.value
  if (!canManage.value || !detail || detail.status !== 'DRAFT' || confirmSaving.value) return
  try {
    await ElMessageBox.confirm(`确认 ${detail.documentNo} 入库？将增加账面库存并生成不可直接改写的流水。`, '确认入库', {
      confirmButtonText: '确认入库', cancelButtonText: '返回核对', type: 'warning',
    })
  } catch { return }
  confirmSaving.value = true
  inboundError.value = ''
  const key = confirmationKeys.get(detail.inboundId) || crypto.randomUUID()
  confirmationKeys.set(detail.inboundId, key)
  try {
    const confirmed = await api<InboundDetail>(`/inventory/inbounds/${encodeURIComponent(detail.inboundId)}/confirm`, {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ expectedRevision: detail.revision }),
    })
    confirmationKeys.delete(detail.inboundId)
    selectedInbound.value = confirmed
    ElMessage.success('入库已确认，库存已更新')
    await Promise.all([loadInbounds(1), loadBalances(1)])
  } catch (reason) { inboundError.value = readableError(reason) }
  finally { confirmSaving.value = false }
}
async function openOutboundDetail(id: string) {
  outboundDetailLoading.value = true
  outboundError.value = ''
  selectedOutbound.value = null
  try { selectedOutbound.value = await api<OutboundDetail>(`/inventory/outbounds/${encodeURIComponent(id)}`) }
  catch (reason) { outboundError.value = readableError(reason) }
  finally { outboundDetailLoading.value = false }
}
async function confirmOutbound() {
  const detail = selectedOutbound.value
  if (!canManage.value || !detail || detail.status !== 'DRAFT' || outboundConfirmSaving.value) return
  try {
    await ElMessageBox.confirm(`确认 ${detail.documentNo} 人工出库？服务端将核对最新可售库存，确认后扣减库存并生成不可改写的流水。`, '确认出库', {
      confirmButtonText: '确认出库', cancelButtonText: '返回核对', type: 'warning',
    })
  } catch { return }
  outboundConfirmSaving.value = true
  outboundError.value = ''
  const key = confirmationKeys.get(detail.outboundId) || crypto.randomUUID()
  confirmationKeys.set(detail.outboundId, key)
  try {
    const confirmed = await api<OutboundDetail>(`/inventory/outbounds/${encodeURIComponent(detail.outboundId)}/confirm`, {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ expectedRevision: detail.revision }),
    })
    confirmationKeys.delete(detail.outboundId)
    selectedOutbound.value = confirmed
    ElMessage.success('出库已确认，库存已更新')
    await Promise.all([loadOutbounds(1), loadBalances(1)])
  } catch (reason) { outboundError.value = readableError(reason) }
  finally { outboundConfirmSaving.value = false }
}
async function openLedgerDetail(id: string) {
  ledgerDetailLoading.value = true
  ledgerError.value = ''
  selectedLedger.value = null
  try { selectedLedger.value = await api<InventoryLedger>(`/inventory/ledgers/${encodeURIComponent(id)}`) }
  catch (reason) { ledgerError.value = readableError(reason) }
  finally { ledgerDetailLoading.value = false }
}
function reasonLabel(reason: string) {
  return ({ DAMAGE: '报损', SAMPLE: '样品领用', INTERNAL: '内部使用', OTHER: '其他' } as Record<string, string>)[reason] || reason
}
function movementLabel(movement: InventoryLedger['movementType']) {
  return ({ INBOUND: '入库', OUTBOUND: '人工出库', ADJUSTMENT: '盘点调整', SALE: '订单销售' } as const)[movement]
}
function signedQuantity(value: number) { return `${value > 0 ? '+' : ''}${value}` }
function exportCurrentLedgerPage() {
  if (!ledgers.value.items.length) return
  const rows = [
    ['时间', '单据', '商品', 'SKU', '仓库', '类型', '变动基础数量', '基础单位', '变动前', '变动后', '原因', '说明', '操作人'],
    ...ledgers.value.items.map((row) => [formatTime(row.occurredAt), row.documentNo, row.productName,
      row.skuCode, row.warehouseName, movementLabel(row.movementType),
      signedQuantity(row.deltaBaseUnits), row.baseUnit || '', row.balanceBefore, row.balanceAfter,
      reasonLabel(row.reason), row.note, row.actorName]),
  ]
  const url = URL.createObjectURL(new Blob([csvTable(rows)], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `库存流水-当前页-${ledgers.value.page}.csv`
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
function switchTab(next: Tab) {
  if (next === tab.value || draftSaving.value || outboundDraftSaving.value || !confirmDiscard()) return
  discardForms()
  tab.value = next
  if (next === 'inbounds') void loadInbounds(1)
  if (next === 'outbounds') void loadOutbounds(1)
  if (next === 'ledgers') void loadLedgers(1)
}
function confirmDiscard() { return !dirty.value || window.confirm('当前表单尚未保存，离开后已填写的内容会丢失。确定继续吗？') }
function discardForms() {
  stocktakeDirty.value = false
  createWarehouseOpen.value = false
  warehouseCode.value = ''
  warehouseName.value = ''
  warehouseFormError.value = ''
  inboundOpen.value = false
  inboundDirty.value = false
  formError.value = ''
  outboundOpen.value = false
  outboundDirty.value = false
  outboundFormError.value = ''
}
function closeWarehouse() { if (confirmDiscard()) discardForms() }
function closeInbound() { if (!draftSaving.value && confirmDiscard()) discardForms() }
function closeOutbound() { if (!outboundDraftSaving.value && confirmDiscard()) discardForms() }
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(() => !draftSaving.value && !outboundDraftSaving.value && confirmDiscard())
onMounted(() => { void Promise.all([loadWarehouses(), loadBalances(1)]); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <section class="page-content inventory-page">
    <div class="page-heading"><div><h1>库存管理</h1><p>按 SKU 与仓库记录库存；入库、人工出库和审核通过的盘点差异均通过流水改变账面。</p></div></div>
    <div class="inventory-tabs" role="tablist" aria-label="库存管理内容">
      <button v-for="item in ([['balances', '库存查询'], ['warehouses', '仓库管理'], ['inbounds', '入库单'], ['outbounds', '出库单'], ['stocktakes', '盘点单'], ['ledgers', '库存流水']] as const)" :key="item[0]" type="button" role="tab" :aria-selected="tab === item[0]" :class="{ active: tab === item[0] }" @click="switchTab(item[0])">{{ item[1] }}</button>
    </div>

    <div v-if="tab === 'balances'" role="tabpanel" class="inventory-section">
      <div class="page-heading"><div><h2>库存查询</h2><p>账面 − 锁定 = 可售；以下汇总仅统计当前页。</p></div></div>
      <form class="inventory-filters" @submit.prevent="loadBalances(1)">
        <label>SKU 编码或商品名称<el-input v-model="balanceKeyword" clearable placeholder="输入关键词" /></label>
        <label>仓库<el-select v-model="balanceWarehouseId" placeholder="全部仓库"><el-option label="全部仓库" value="" /><el-option v-for="row in warehouses" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label>
        <el-button type="primary" native-type="submit" :loading="balanceLoading">查询</el-button>
        <el-button @click="balanceKeyword = ''; balanceWarehouseId = ''; loadBalances(1)">重置</el-button>
      </form>
      <div class="inventory-totals" aria-label="当前页库存汇总"><span>账面 <strong>{{ pageTotals.onHand }}</strong></span><span>锁定 <strong>{{ pageTotals.reserved }}</strong></span><span>可售 <strong>{{ pageTotals.available }}</strong></span></div>
      <p v-if="balanceError" class="notice" role="alert">{{ balanceError }} <el-button link type="primary" @click="loadBalances(balances.page)">重试</el-button></p>
      <div class="panel table-wrap" v-loading="balanceLoading">
        <table><thead><tr><th>商品 / SKU</th><th>仓库</th><th>账面</th><th>锁定</th><th>可售</th></tr></thead>
          <tbody><tr v-for="row in balances.items" :key="`${row.warehouseId}:${row.skuId}`"><td><strong>{{ row.productName }}</strong><small class="inventory-subline code">{{ row.skuCode }}</small></td><td>{{ row.warehouseName }}</td><td class="inventory-number">{{ row.onHandBaseUnits }} {{ row.baseUnit }}</td><td class="inventory-number">{{ row.reservedBaseUnits }} {{ row.baseUnit }}</td><td class="inventory-number inventory-available">{{ row.availableBaseUnits }} {{ row.baseUnit }}</td></tr></tbody>
        </table>
        <p v-if="!balanceLoading && !balanceError && !balances.items.length" class="inventory-empty">暂无匹配库存。可先创建仓库，再保存并确认一张入库单。</p>
      </div>
      <div v-if="balances.total > balances.pageSize" class="inventory-pagination"><span>共 {{ balances.total }} 条</span><el-pagination :current-page="balances.page" :page-size="balances.pageSize" :total="balances.total" layout="prev, pager, next" @current-change="loadBalances" /></div>
    </div>

    <div v-else-if="tab === 'warehouses'" role="tabpanel" class="inventory-section">
      <div class="page-heading"><div><h2>仓库管理</h2><p>默认仓用于后续新订单，已形成的库存按原仓库独立记录。</p></div><el-button v-if="canManage" type="primary" @click="createWarehouseOpen ? closeWarehouse() : createWarehouseOpen = true">{{ createWarehouseOpen ? '关闭表单' : '新建仓库' }}</el-button></div>
      <p v-if="warehouseError" class="notice" role="alert">{{ warehouseError }} <el-button link type="primary" @click="loadWarehouses">重试</el-button></p>
      <div class="panel table-wrap" v-loading="warehouseLoading"><table><thead><tr><th>仓库名称</th><th>仓库编号</th><th>默认发货仓</th><th>状态</th></tr></thead><tbody><tr v-for="row in warehouses" :key="row.warehouseId"><td><strong>{{ row.name }}</strong></td><td class="code">{{ row.code }}</td><td>{{ row.isDefault ? '默认仓' : '—' }}</td><td><span :class="row.enabled ? 'badge badge-good' : 'badge badge-muted'">{{ row.enabled ? '启用' : '停用' }}</span></td></tr></tbody></table><p v-if="!warehouseLoading && !warehouseError && !warehouses.length" class="inventory-empty">尚无仓库。请先创建一个默认仓库。</p></div>
      <form v-if="createWarehouseOpen && canManage" class="panel inventory-form" @submit.prevent="createWarehouse"><h3>新建仓库</h3><div class="inventory-form-grid"><label>仓库编号 <span class="required">*</span><el-input v-model="warehouseCode" maxlength="32" placeholder="例如 WH-001" /></label><label>仓库名称 <span class="required">*</span><el-input v-model="warehouseName" maxlength="100" placeholder="例如 上海中心仓" /></label></div><p class="help-text">{{ warehouses.length ? '当前默认仓保持不变；此仓库创建为非默认仓。默认仓切换将在订单锁库规则接入时单独实现。' : '首个仓库将自动设为默认发货仓。' }}</p><p v-if="warehouseFormError" class="error" role="alert">{{ warehouseFormError }}</p><div class="inventory-actions"><el-button @click="closeWarehouse">取消</el-button><el-button type="primary" native-type="submit" :loading="warehouseSaving">保存仓库</el-button></div></form>
    </div>

    <div v-else-if="tab === 'inbounds'" role="tabpanel" class="inventory-section">
      <div class="page-heading"><div><h2>入库单</h2><p>先保存草稿，核对 SKU、单位换算及数量，再确认入库。</p></div><el-button v-if="canManage" type="primary" :disabled="!canCreateDraft" @click="inboundOpen ? closeInbound() : openInbound()">{{ inboundOpen ? '关闭表单' : '创建入库单' }}</el-button></div>
      <p v-if="!canCreateDraft && canManage" class="hint">请先创建并启用仓库，才能创建入库单。</p>
      <p v-if="inboundError" class="notice" role="alert">{{ inboundError }} <el-button link type="primary" @click="loadInbounds(inbounds.page)">刷新列表</el-button></p>
      <div class="panel table-wrap" v-loading="inboundLoading"><table><thead><tr><th>入库单号</th><th>仓库</th><th>SKU 数</th><th>基础数量</th><th>来源 / 原因</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in inbounds.items" :key="row.inboundId"><td class="code">{{ row.documentNo }}</td><td>{{ row.warehouseName || warehouseLabel(row.warehouseId) }}</td><td>{{ row.itemCount }}</td><td class="inventory-number">{{ row.totalBaseUnits }}</td><td>{{ row.reason }}</td><td><span :class="row.status === 'CONFIRMED' ? 'badge badge-good' : 'badge badge-muted'">{{ row.status === 'CONFIRMED' ? '已确认' : '草稿' }}</span></td><td>{{ formatTime(row.createdAt) }}</td><td><el-button link type="primary" :loading="detailLoading" @click="openInboundDetail(row.inboundId)">查看详情</el-button></td></tr></tbody></table><p v-if="!inboundLoading && !inboundError && !inbounds.items.length" class="inventory-empty">暂无入库单。创建草稿后可在这里重新打开并确认。</p></div>
      <div v-if="inbounds.total > inbounds.pageSize" class="inventory-pagination"><span>共 {{ inbounds.total }} 条</span><el-pagination :current-page="inbounds.page" :page-size="inbounds.pageSize" :total="inbounds.total" layout="prev, pager, next" @current-change="loadInbounds" /></div>

      <StockDocumentForm v-if="inboundOpen && canManage" kind="inbound" :warehouses="warehouses" :saving="draftSaving" :error="formError" @submit="createInbound" @close="closeInbound" @dirty="inboundDirty = $event" @change="formError = ''" />

      <p v-if="detailLoading" class="hint" role="status">正在读取入库单明细…</p>
      <div v-if="selectedInbound" class="panel inventory-detail" aria-live="polite"><div class="page-heading"><div><h3>入库单明细 · {{ selectedInbound.documentNo }}</h3><p>{{ selectedInbound.status === 'DRAFT' ? '草稿尚未改变账面库存。' : '已确认；库存流水已生成。' }}</p></div><span :class="selectedInbound.status === 'CONFIRMED' ? 'badge badge-good' : 'badge badge-muted'">{{ selectedInbound.status === 'CONFIRMED' ? '已确认' : '草稿' }}</span></div><p>仓库：{{ selectedInbound.warehouseName || warehouseLabel(selectedInbound.warehouseId) }}　·　原因：{{ selectedInbound.reason }}</p><div class="table-wrap"><table><thead><tr><th>商品 / SKU</th><th>操作单位</th><th>录入数量</th><th>换算关系</th><th>基础数量</th></tr></thead><tbody><tr v-for="row in selectedInbound.items" :key="row.skuId"><td><strong>{{ row.productName }}</strong><small class="inventory-subline code">{{ row.skuCode }}</small></td><td>{{ row.operationUnit }}</td><td class="inventory-number">{{ row.quantity }}</td><td>1 {{ row.operationUnit }} = {{ row.ratio }} {{ row.baseUnit }}</td><td class="inventory-number">{{ row.baseQuantity }} {{ row.baseUnit }}</td></tr></tbody></table></div><div v-if="selectedInbound.status === 'DRAFT' && canManage" class="inventory-actions"><el-button type="primary" :loading="confirmSaving" @click="confirmInbound">确认入库</el-button></div></div>
    </div>

    <div v-else-if="tab === 'outbounds'" role="tabpanel" class="inventory-section">
      <div class="page-heading"><div><h2>人工出库</h2><p>登记报损、样品领用等非销售用途；确认时按最新可售量校验，不允许负库存。</p></div><el-button v-if="canManage" type="primary" :disabled="!canCreateDraft" @click="outboundOpen ? closeOutbound() : openOutbound()">{{ outboundOpen ? '关闭表单' : '＋ 新建出库单' }}</el-button></div>
      <p v-if="!canCreateDraft && canManage" class="hint">请先创建并启用仓库，才能创建出库单。</p>
      <p v-if="outboundError" class="notice" role="alert">{{ outboundError }} <el-button link type="primary" @click="loadOutbounds(outbounds.page)">刷新列表</el-button></p>
      <div class="panel table-wrap" v-loading="outboundLoading"><table><thead><tr><th>出库单号</th><th>仓库</th><th>SKU 数</th><th>基础数量</th><th>出库原因</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in outbounds.items" :key="row.outboundId"><td class="code">{{ row.documentNo }}</td><td>{{ row.warehouseName || warehouseLabel(row.warehouseId) }}</td><td>{{ row.itemCount }}</td><td class="inventory-number">{{ row.totalBaseUnits }}</td><td>{{ reasonLabel(row.reason) }}</td><td><span :class="row.status === 'CONFIRMED' ? 'badge badge-good' : 'badge badge-muted'">{{ row.status === 'CONFIRMED' ? '已确认' : '草稿' }}</span></td><td>{{ formatTime(row.createdAt) }}</td><td><el-button link type="primary" :loading="outboundDetailLoading" @click="openOutboundDetail(row.outboundId)">查看详情</el-button></td></tr></tbody></table><p v-if="!outboundLoading && !outboundError && !outbounds.items.length" class="inventory-empty">暂无人工出库单。可先保存草稿，核对后再确认。</p></div>
      <div v-if="outbounds.total > outbounds.pageSize" class="inventory-pagination"><span>共 {{ outbounds.total }} 条</span><el-pagination :current-page="outbounds.page" :page-size="outbounds.pageSize" :total="outbounds.total" layout="prev, pager, next" @current-change="loadOutbounds" /></div>
      <StockDocumentForm v-if="outboundOpen && canManage" kind="outbound" :warehouses="warehouses" :saving="outboundDraftSaving" :error="outboundFormError" @submit="createOutbound" @close="closeOutbound" @dirty="outboundDirty = $event" @change="outboundFormError = ''" />
      <p v-if="outboundDetailLoading" class="hint" role="status">正在读取出库单明细…</p>
      <div v-if="selectedOutbound" class="panel inventory-detail" aria-live="polite">
        <div class="page-heading"><div><h3>出库单明细 · {{ selectedOutbound.documentNo }}</h3><p>{{ selectedOutbound.status === 'DRAFT' ? '草稿尚未扣减库存；确认时按最新可售量核对。' : '已确认；扣减库存和流水已生成。' }}</p></div><span :class="selectedOutbound.status === 'CONFIRMED' ? 'badge badge-good' : 'badge badge-muted'">{{ selectedOutbound.status === 'CONFIRMED' ? '已确认' : '草稿' }}</span></div>
        <p>仓库：{{ selectedOutbound.warehouseName || warehouseLabel(selectedOutbound.warehouseId) }}　·　原因：{{ reasonLabel(selectedOutbound.reason) }}<template v-if="selectedOutbound.note">　·　说明：{{ selectedOutbound.note }}</template></p>
        <div class="table-wrap"><table><thead><tr><th>商品 / SKU</th><th>操作单位</th><th>录入数量</th><th>换算关系</th><th>基础数量</th></tr></thead><tbody><tr v-for="row in selectedOutbound.items" :key="row.skuId"><td><strong>{{ row.productName }}</strong><small class="inventory-subline code">{{ row.skuCode }}</small></td><td>{{ row.operationUnit }}</td><td class="inventory-number">{{ row.quantity }}</td><td>1 {{ row.operationUnit }} = {{ row.ratio }} {{ row.baseUnit }}</td><td class="inventory-number">{{ row.baseQuantity }} {{ row.baseUnit }}</td></tr></tbody></table></div>
        <p class="inventory-conversion">基础数量合计：<strong>{{ selectedOutbound.totalBaseUnits }}</strong>。不同 SKU 的基础单位可能不同，请逐行核对。</p>
        <div v-if="selectedOutbound.status === 'DRAFT' && canManage" class="inventory-actions"><el-button type="primary" :loading="outboundConfirmSaving" @click="confirmOutbound">确认出库并生成流水</el-button></div>
      </div>
    </div>

    <StocktakePanel v-else-if="tab === 'stocktakes'" role="tabpanel" :warehouses="warehouses" :can-manage="canManage" :can-review="canReview" @dirty="stocktakeDirty = $event" @approved="loadBalances(1); loadLedgers(1)" />

    <div v-else role="tabpanel" class="inventory-section">
      <div class="page-heading"><div><h2>库存流水</h2><p>按仓库、SKU 和单据追溯每一次已确认的库存变动。</p></div><el-button :disabled="!ledgers.items.length" @click="exportCurrentLedgerPage">导出当前页 CSV</el-button></div>
      <form class="inventory-filters" @submit.prevent="loadLedgers(1)"><label>商品、SKU 或单据号<el-input v-model="ledgerKeyword" maxlength="120" clearable placeholder="输入商品名称 / SKU 编码 / 单据号" /></label><label>仓库<el-select v-model="ledgerWarehouseId" placeholder="全部仓库"><el-option label="全部仓库" value="" /><el-option v-for="row in warehouses" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label><label>变动类型<el-select v-model="ledgerMovementType" placeholder="全部类型"><el-option label="全部类型" value="" /><el-option label="入库" value="INBOUND" /><el-option label="人工出库" value="OUTBOUND" /><el-option label="盘点调整" value="ADJUSTMENT" /><el-option label="订单销售" value="SALE" /></el-select></label><el-button type="primary" native-type="submit" :loading="ledgerLoading">查询</el-button><el-button @click="ledgerKeyword = ''; ledgerWarehouseId = ''; ledgerMovementType = ''; loadLedgers(1)">重置</el-button></form>
      <p v-if="ledgerError" class="notice" role="alert">{{ ledgerError }} <el-button link type="primary" @click="loadLedgers(ledgers.page)">重试</el-button></p>
      <div class="panel table-wrap" v-loading="ledgerLoading"><table><thead><tr><th>时间</th><th>单据 / 关联对象</th><th>商品 / SKU</th><th>仓库</th><th>变动类型</th><th>数量（基础单位）</th><th>操作人</th><th>操作</th></tr></thead><tbody><tr v-for="row in ledgers.items" :key="row.ledgerId"><td>{{ formatTime(row.occurredAt) }}</td><td class="code">{{ row.documentNo }}</td><td><strong>{{ row.productName }}</strong><small class="inventory-subline code">{{ row.skuCode }}</small></td><td>{{ row.warehouseName }}</td><td>{{ movementLabel(row.movementType) }}</td><td class="inventory-number" :class="{ 'inventory-available': row.deltaBaseUnits > 0 }">{{ signedQuantity(row.deltaBaseUnits) }} {{ row.baseUnit }}</td><td>{{ row.actorName }}</td><td><el-button link type="primary" :loading="ledgerDetailLoading" @click="openLedgerDetail(row.ledgerId)">详情</el-button></td></tr></tbody></table><p v-if="!ledgerLoading && !ledgerError && !ledgers.items.length" class="inventory-empty">暂无匹配流水。入库、人工出库、盘点调整及订单销售会在此追溯。</p></div>
      <div v-if="ledgers.total > ledgers.pageSize" class="inventory-pagination"><span>共 {{ ledgers.total }} 条；CSV 仅导出当前页 {{ ledgers.items.length }} 条</span><el-pagination :current-page="ledgers.page" :page-size="ledgers.pageSize" :total="ledgers.total" layout="prev, pager, next" @current-change="loadLedgers" /></div>
      <p v-if="ledgerDetailLoading" class="hint" role="status">正在读取流水明细…</p>
      <div v-if="selectedLedger" class="panel inventory-detail" aria-live="polite"><h3>流水详情 · {{ selectedLedger.documentNo }}</h3><dl class="inventory-key-values"><dt>商品 / SKU</dt><dd>{{ selectedLedger.productName }} · {{ selectedLedger.skuCode }}</dd><dt>仓库</dt><dd>{{ selectedLedger.warehouseName }}</dd><dt>变动类型 / 时间</dt><dd>{{ movementLabel(selectedLedger.movementType) }} · {{ formatTime(selectedLedger.occurredAt) }}</dd><dt>变动前 / 后</dt><dd>{{ selectedLedger.balanceBefore }} {{ selectedLedger.baseUnit }} → {{ selectedLedger.balanceAfter }} {{ selectedLedger.baseUnit }}</dd><dt>操作数量</dt><dd>{{ selectedLedger.operationQuantity }} {{ selectedLedger.operationUnit }}</dd><dt>基本单位变动</dt><dd>{{ selectedLedger.deltaBaseUnits > 0 ? '+' : '' }}{{ selectedLedger.deltaBaseUnits }} {{ selectedLedger.baseUnit }}</dd><dt>单位换算快照</dt><dd>1 {{ selectedLedger.operationUnit }} = {{ selectedLedger.ratio }} {{ selectedLedger.baseUnit }}</dd><dt>变动原因 / 说明</dt><dd>{{ reasonLabel(selectedLedger.reason) }}<template v-if="selectedLedger.note"> · {{ selectedLedger.note }}</template></dd><dt>操作人</dt><dd>{{ selectedLedger.actorName }}</dd></dl><p class="inventory-conversion">已确认流水不可直接改写；错误通过有依据的后续调整单纠正，并保留原记录。</p></div>
    </div>
  </section>
</template>
