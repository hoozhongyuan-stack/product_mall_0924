<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { api, ApiError, type Account } from '../api'
import StockDocumentForm, { type StockDraftPayload } from './inventory/StockDocumentForm.vue'
import StocktakePanel from './inventory/StocktakePanel.vue'
import InventoryDateRange from './inventory/InventoryDateRange.vue'
import InventoryListFooter from './inventory/InventoryListFooter.vue'
import { csvTable } from './inventory/csv.mjs'
import { summarizeBalances } from './inventory/summary.mjs'
import type { InboundDetail, InboundSummary, InventoryBalance, InventoryLedger, OutboundDetail, OutboundSummary, Page, Warehouse, WarehouseList } from './inventory/types'
import './inventory/inventory.css'

const props = defineProps<{ account: Account }>()
type Tab = 'balances' | 'warehouses' | 'inbounds' | 'outbounds' | 'stocktakes' | 'ledgers'
const route = useRoute()
const tab = computed(() => (route.meta.inventoryTab || 'balances') as Tab)
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
const balanceAvailability = ref('')
const inbounds = ref<Page<InboundSummary>>({ items: [], page: 1, pageSize: 20, total: 0 })
const inboundLoading = ref(false)
const inboundError = ref('')
const inboundDocumentNo = ref('')
const inboundWarehouseId = ref('')
const inboundStatus = ref('')
const inboundDates = ref<string[] | null>([])
const selectedInbound = ref<InboundDetail | null>(null)
const detailLoading = ref(false)
const outbounds = ref<Page<OutboundSummary>>({ items: [], page: 1, pageSize: 20, total: 0 })
const outboundLoading = ref(false)
const outboundError = ref('')
const outboundDocumentNo = ref('')
const outboundWarehouseId = ref('')
const outboundStatus = ref('')
const outboundDates = ref<string[] | null>([])
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
const ledgerDates = ref<string[] | null>([])
const pageSizes = ref<Record<'balances' | 'inbounds' | 'outbounds' | 'ledgers', number>>({ balances: 20, inbounds: 20, outbounds: 20, ledgers: 20 })
const selectedIds = ref<string[]>([])
const createWarehouseOpen = ref(false)
const warehouseCode = ref('')
const warehouseName = ref('')
const warehouseSaving = ref(false)
const warehouseStatusSaving = ref('')
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
const stocktakeBusy = ref(false)
const confirmationPending = ref(false)
let detailGeneration = 0
let balanceLoadSequence = 0
let inboundLoadSequence = 0
let outboundLoadSequence = 0
let ledgerLoadSequence = 0
const confirmationKeys = new Map<string, string>()

const canCreateDraft = computed(() => canManage.value && warehouses.value.some((row) => row.enabled))
const dirty = computed(() => (createWarehouseOpen.value && !!(warehouseCode.value.trim() || warehouseName.value.trim()))
  || (inboundOpen.value && inboundDirty.value) || (outboundOpen.value && outboundDirty.value) || stocktakeDirty.value)
const pageTotals = computed(() => summarizeBalances(balances.value.items))
type SelectableRow = InventoryBalance | InboundSummary | OutboundSummary | InventoryLedger
function rowKey(row: SelectableRow): string {
  if ('ledgerId' in row) return row.ledgerId
  if ('inboundId' in row) return row.inboundId
  if ('outboundId' in row) return row.outboundId
  return `${row.warehouseId}:${row.skuId}`
}
const visibleRows = computed<SelectableRow[]>(() => {
  if (tab.value === 'balances') return balances.value.items
  if (tab.value === 'inbounds') return inbounds.value.items
  if (tab.value === 'outbounds') return outbounds.value.items
  if (tab.value === 'ledgers') return ledgers.value.items
  return []
})
const selectedRows = computed(() => visibleRows.value.filter(row => selectedIds.value.includes(rowKey(row))))
const allSelected = computed(() => visibleRows.value.length > 0 && selectedRows.value.length === visibleRows.value.length)
function toggleAll(value: boolean | string | number) {
  selectedIds.value = value ? visibleRows.value.map(rowKey) : []
}
function toggleRow(row: SelectableRow, value: boolean | string | number) {
  const key = rowKey(row)
  selectedIds.value = value ? [...selectedIds.value.filter(id => id !== key), key]
    : selectedIds.value.filter(id => id !== key)
}
function exportRows(headers: string[], rows: (string | number)[][], filename: string) {
  if (!rows.length) return
  const url = URL.createObjectURL(new Blob([csvTable([headers, ...rows])], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
function exportSelected() {
  if (tab.value === 'balances') {
    const rows = selectedRows.value.filter((row): row is InventoryBalance => 'onHandBaseUnits' in row)
    exportRows(['商品', '库存基准 SKU', '关联 SKU', '仓库', '账面', '锁定', '可售', '基础单位'],
      rows.map(row => [row.productName, row.poolAnchorSkuCode, row.poolSkuCodes.join('、'), row.warehouseName,
        row.onHandBaseUnits, row.reservedBaseUnits, row.availableBaseUnits, row.baseUnit]), '库存查询-所选行.csv')
  } else if (tab.value === 'inbounds' || tab.value === 'outbounds') {
    const rows = selectedRows.value.filter((row): row is InboundSummary | OutboundSummary => 'documentNo' in row)
    exportRows(['单号', '仓库', '状态', 'SKU 数', '基础数量', '原因', '创建时间'],
      rows.map(row => [row.documentNo, row.warehouseName, row.status, row.itemCount,
        row.totalBaseUnits, row.reason, formatTime(row.createdAt)]), `${tab.value === 'inbounds' ? '入库单' : '出库单'}-所选行.csv`)
  } else if (tab.value === 'ledgers') {
    const rows = selectedRows.value.filter((row): row is InventoryLedger => 'ledgerId' in row)
    exportRows(['时间', '单据', '商品', 'SKU', '仓库', '类型', '变动基础数量', '单位', '操作人'],
      rows.map(row => [formatTime(row.occurredAt), row.documentNo, row.productName, row.skuCode,
        row.warehouseName, movementLabel(row.movementType), row.deltaBaseUnits, row.baseUnit || '', row.actorName]), '库存流水-所选行.csv')
  }
}
function changePageSize(kind: 'balances' | 'inbounds' | 'outbounds' | 'ledgers', value: number) {
  pageSizes.value = { ...pageSizes.value, [kind]: value }
  if (kind === 'balances') void loadBalances(1)
  if (kind === 'inbounds') void loadInbounds(1)
  if (kind === 'outbounds') void loadOutbounds(1)
  if (kind === 'ledgers') void loadLedgers(1)
}
function addDates(query: URLSearchParams, dates: string[] | null) {
  if (Array.isArray(dates) && dates.length === 2) {
    query.set('dateFrom', dates[0]); query.set('dateTo', dates[1])
  }
}

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
  selectedIds.value = []
  const sequence = ++balanceLoadSequence
  balanceLoading.value = true
  balanceError.value = ''
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSizes.value.balances) })
  if (balanceKeyword.value.trim()) query.set('keyword', balanceKeyword.value.trim())
  if (balanceWarehouseId.value) query.set('warehouseId', balanceWarehouseId.value)
  if (balanceAvailability.value) query.set('availability', balanceAvailability.value)
  try {
    const result = await api<Page<InventoryBalance>>(`/inventory/balances?${query}`)
    if (sequence === balanceLoadSequence) { selectedIds.value = []; balances.value = result }
  } catch (reason) { if (sequence === balanceLoadSequence) balanceError.value = readableError(reason) }
  finally { if (sequence === balanceLoadSequence) balanceLoading.value = false }
}
async function loadInbounds(page = 1) {
  selectedIds.value = []
  const sequence = ++inboundLoadSequence
  inboundLoading.value = true
  inboundError.value = ''
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSizes.value.inbounds) })
  if (inboundDocumentNo.value.trim()) query.set('documentNo', inboundDocumentNo.value.trim())
  if (inboundWarehouseId.value) query.set('warehouseId', inboundWarehouseId.value)
  if (inboundStatus.value) query.set('status', inboundStatus.value)
  addDates(query, inboundDates.value)
  try {
    const result = await api<Page<InboundSummary>>(`/inventory/inbounds?${query}`)
    if (sequence === inboundLoadSequence) { selectedIds.value = []; inbounds.value = result }
  } catch (reason) { if (sequence === inboundLoadSequence) inboundError.value = readableError(reason) }
  finally { if (sequence === inboundLoadSequence) inboundLoading.value = false }
}
async function loadOutbounds(page = 1) {
  selectedIds.value = []
  const sequence = ++outboundLoadSequence
  outboundLoading.value = true
  outboundError.value = ''
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSizes.value.outbounds) })
  if (outboundDocumentNo.value.trim()) query.set('documentNo', outboundDocumentNo.value.trim())
  if (outboundWarehouseId.value) query.set('warehouseId', outboundWarehouseId.value)
  if (outboundStatus.value) query.set('status', outboundStatus.value)
  addDates(query, outboundDates.value)
  try {
    const result = await api<Page<OutboundSummary>>(`/inventory/outbounds?${query}`)
    if (sequence === outboundLoadSequence) { selectedIds.value = []; outbounds.value = result }
  } catch (reason) { if (sequence === outboundLoadSequence) outboundError.value = readableError(reason) }
  finally { if (sequence === outboundLoadSequence) outboundLoading.value = false }
}
async function loadLedgers(page = 1) {
  selectedIds.value = []
  const sequence = ++ledgerLoadSequence
  ledgerLoading.value = true
  ledgerError.value = ''
  selectedLedger.value = null
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSizes.value.ledgers) })
  if (ledgerKeyword.value.trim()) query.set('keyword', ledgerKeyword.value.trim())
  if (ledgerWarehouseId.value) query.set('warehouseId', ledgerWarehouseId.value)
  if (ledgerMovementType.value) query.set('movementType', ledgerMovementType.value)
  addDates(query, ledgerDates.value)
  try {
    const result = await api<Page<InventoryLedger>>(`/inventory/ledgers?${query}`)
    if (sequence === ledgerLoadSequence) { selectedIds.value = []; ledgers.value = result }
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
async function toggleWarehouseStatus(row: Warehouse) {
  if (!canManage.value || warehouseStatusSaving.value) return
  try {
    await ElMessageBox.confirm(`确定${row.enabled ? '停用' : '启用'}仓库「${row.name}」？`, '仓库状态', {
      confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  warehouseStatusSaving.value = row.warehouseId
  warehouseError.value = ''
  try {
    await api<Warehouse>(`/warehouses/${encodeURIComponent(row.warehouseId)}/status`, {
      method: 'PATCH', body: JSON.stringify({ enabled: !row.enabled, expectedRevision: row.revision }),
    })
    ElMessage.success(`仓库已${row.enabled ? '停用' : '启用'}`)
    await loadWarehouses()
  } catch (reason) {
    warehouseError.value = readableError(reason)
    if (reason instanceof ApiError && reason.status === 409) await loadWarehouses()
  } finally { warehouseStatusSaving.value = '' }
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
  const generation = ++detailGeneration
  detailLoading.value = true
  inboundError.value = ''
  selectedInbound.value = null
  try {
    const detail = await api<InboundDetail>(`/inventory/inbounds/${encodeURIComponent(id)}`)
    if (generation === detailGeneration) selectedInbound.value = detail
  } catch (reason) { if (generation === detailGeneration) inboundError.value = readableError(reason) }
  finally { if (generation === detailGeneration) detailLoading.value = false }
}
async function confirmInbound() {
  const detail = selectedInbound.value
  if (!canManage.value || !detail || detail.status !== 'DRAFT' || confirmSaving.value || confirmationPending.value) return
  confirmationPending.value = true
  try {
    await ElMessageBox.confirm(`确认 ${detail.documentNo} 入库？将增加账面库存并生成不可直接改写的流水。`, '确认入库', {
      confirmButtonText: '确认入库', cancelButtonText: '返回核对', type: 'warning',
    })
  } catch { return }
  finally { confirmationPending.value = false }
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
  const generation = ++detailGeneration
  outboundDetailLoading.value = true
  outboundError.value = ''
  selectedOutbound.value = null
  try {
    const detail = await api<OutboundDetail>(`/inventory/outbounds/${encodeURIComponent(id)}`)
    if (generation === detailGeneration) selectedOutbound.value = detail
  } catch (reason) { if (generation === detailGeneration) outboundError.value = readableError(reason) }
  finally { if (generation === detailGeneration) outboundDetailLoading.value = false }
}
async function confirmOutbound() {
  const detail = selectedOutbound.value
  if (!canManage.value || !detail || detail.status !== 'DRAFT' || outboundConfirmSaving.value || confirmationPending.value) return
  confirmationPending.value = true
  try {
    await ElMessageBox.confirm(`确认 ${detail.documentNo} 人工出库？服务端将核对最新可售库存，确认后扣减库存并生成不可改写的流水。`, '确认出库', {
      confirmButtonText: '确认出库', cancelButtonText: '返回核对', type: 'warning',
    })
  } catch { return }
  finally { confirmationPending.value = false }
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
  const generation = ++detailGeneration
  ledgerDetailLoading.value = true
  ledgerError.value = ''
  selectedLedger.value = null
  try {
    const detail = await api<InventoryLedger>(`/inventory/ledgers/${encodeURIComponent(id)}`)
    if (generation === detailGeneration) selectedLedger.value = detail
  } catch (reason) { if (generation === detailGeneration) ledgerError.value = readableError(reason) }
  finally { if (generation === detailGeneration) ledgerDetailLoading.value = false }
}
function reasonLabel(reason: string) {
  return ({ DAMAGE: '报损', SAMPLE: '样品领用', INTERNAL: '内部使用', OTHER: '其他' } as Record<string, string>)[reason] || reason
}
function movementLabel(movement: InventoryLedger['movementType']) {
  return ({ INBOUND: '入库', OUTBOUND: '人工出库', ADJUSTMENT: '盘点调整', SALE: '订单销售',
    REFUND: '未发货退款回库', RETURN: '退货验收回库' } as const)[movement]
}
function signedQuantity(value: number) { return `${value > 0 ? '+' : ''}${value}` }
async function confirmDiscard() { return !dirty.value || await confirmAction('当前表单尚未保存，离开后已填写的内容会丢失。确定继续吗？') }
function discardForms() {
  stocktakeDirty.value = false
  stocktakeBusy.value = false
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
async function closeWarehouse() { if (!warehouseSaving.value && await confirmDiscard() && !writeBusy.value) discardForms() }
async function closeInbound() { if (!draftSaving.value && await confirmDiscard() && !writeBusy.value) discardForms() }
async function closeOutbound() { if (!outboundDraftSaving.value && await confirmDiscard() && !writeBusy.value) discardForms() }
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || writeBusy.value) { event.preventDefault(); event.returnValue = '' } }
const writeBusy = computed(() => warehouseSaving.value || !!warehouseStatusSaving.value || draftSaving.value || outboundDraftSaving.value
  || confirmSaving.value || outboundConfirmSaving.value || stocktakeBusy.value || confirmationPending.value)
async function canNavigate() { return !writeBusy.value && await confirmDiscard() && !writeBusy.value }
onBeforeRouteLeave(canNavigate)
onBeforeRouteUpdate((to, from) => to.path === from.path || canNavigate())
watch(tab, (next) => {
  selectedIds.value = []
  detailGeneration++
  detailLoading.value = false
  outboundDetailLoading.value = false
  ledgerDetailLoading.value = false
  selectedInbound.value = null
  selectedOutbound.value = null
  selectedLedger.value = null
  discardForms()
  if (next === 'balances') void loadBalances(1)
  if (next === 'inbounds') void loadInbounds(1)
  if (next === 'outbounds') void loadOutbounds(1)
  if (next === 'ledgers') void loadLedgers(1)
}, { immediate: true })
onMounted(() => { void loadWarehouses(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => { detailGeneration++; window.removeEventListener('beforeunload', beforeUnload) })
</script>

<template>
  <section class="page-content inventory-page">
    <div v-if="tab === 'balances'" class="inventory-section">
      <div class="page-heading"><div><h1>库存查询</h1><p>账面 − 锁定 = 可售；当前页按基础单位分别汇总，共享库存池只统计一次。</p></div></div>
      <form class="inventory-filters" @submit.prevent="loadBalances(1)">
        <label>SKU 编码或商品名称<el-input v-model="balanceKeyword" clearable placeholder="输入关键词" /></label>
        <label>仓库<el-select v-model="balanceWarehouseId" placeholder="全部仓库"><el-option label="全部仓库" value="" /><el-option v-for="row in warehouses" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label><label>可售库存<el-select v-model="balanceAvailability"><el-option label="全部" value="" /><el-option label="有可售" value="AVAILABLE" /><el-option label="无可售" value="UNAVAILABLE" /><el-option label="有锁定" value="RESERVED" /></el-select></label>
        <el-button type="primary" native-type="submit" :loading="balanceLoading">查询</el-button>
        <el-button @click="balanceKeyword = ''; balanceWarehouseId = ''; balanceAvailability = ''; loadBalances(1)">重置</el-button>
      </form>
      <div v-if="!balanceLoading && !balanceError && pageTotals.length" aria-label="当前页库存汇总"><div v-for="total in pageTotals" :key="total.baseUnit" class="inventory-totals"><span>基础单位：<strong>{{ total.baseUnit || '单位未配置' }}</strong></span><span>账面 <strong>{{ total.onHand }} {{ total.baseUnit }}</strong></span><span>锁定 <strong>{{ total.reserved }} {{ total.baseUnit }}</strong></span><span>可售 <strong>{{ total.available }} {{ total.baseUnit }}</strong></span></div></div>
      <p v-if="balanceError" class="notice" role="alert">{{ balanceError }} <el-button link type="primary" @click="loadBalances(balances.page)">重试</el-button></p>
      <div class="panel table-wrap" v-loading="balanceLoading">
        <table><thead><tr><th><el-checkbox :model-value="allSelected" :disabled="balanceLoading" aria-label="全选当前页库存" @change="toggleAll" /></th><th>商品 / 规格</th><th>仓库</th><th>账面</th><th>锁定</th><th>可售</th></tr></thead>
          <tbody><tr v-for="row in balances.items" :key="`${row.warehouseId}:${row.skuId}`"><td><el-checkbox :model-value="selectedIds.includes(rowKey(row))" :disabled="balanceLoading" :aria-label="`选择库存 ${row.poolAnchorSkuCode}`" @change="toggleRow(row, $event)" /></td><td><strong>{{ row.productName }}</strong><small class="inventory-subline code">规格 SKU {{ row.poolSkuCodes.length > 1 ? "（单位换算）" : "" }}： {{ row.poolSkuCodes.join("、") }}</small></td><td>{{ row.warehouseName }}</td><td class="inventory-number">{{ row.onHandBaseUnits }} {{ row.baseUnit }}</td><td class="inventory-number">{{ row.reservedBaseUnits }} {{ row.baseUnit }}</td><td class="inventory-number inventory-available">{{ row.availableBaseUnits }} {{ row.baseUnit }}</td></tr></tbody>
        </table>
        <p v-if="!balanceLoading && !balanceError && !balances.items.length" class="inventory-empty">暂无匹配库存。可先创建仓库，再保存并确认一张入库单。</p>
      </div>
      <InventoryListFooter :page="balances.page" :page-size="balances.pageSize" :total="balances.total" :selected-count="selectedIds.length" :loading="balanceLoading" @page="loadBalances" @size="changePageSize('balances', $event)" @export="exportSelected" />
    </div>

    <div v-else-if="tab === 'warehouses'" class="inventory-section">
      <div class="page-heading"><div><h1>仓库管理</h1><p>默认仓用于后续新订单，已形成的库存按原仓库独立记录。</p></div><el-button v-if="canManage" type="primary" :disabled="warehouseSaving" @click="createWarehouseOpen ? closeWarehouse() : createWarehouseOpen = true">{{ createWarehouseOpen ? '关闭表单' : '新建仓库' }}</el-button></div>
      <p v-if="warehouseError" class="notice" role="alert">{{ warehouseError }} <el-button link type="primary" @click="loadWarehouses">重试</el-button></p>
      <div class="panel table-wrap" v-loading="warehouseLoading"><table><thead><tr><th>仓库名称</th><th>仓库编号</th><th>默认发货仓</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="row in warehouses" :key="row.warehouseId"><td><strong>{{ row.name }}</strong></td><td class="code">{{ row.code }}</td><td>{{ row.isDefault ? '默认仓' : '—' }}</td><td><span :class="row.enabled ? 'badge badge-good' : 'badge badge-muted'">{{ row.enabled ? '启用' : '停用' }}</span></td><td><el-button v-if="canManage" link type="primary" :disabled="row.isDefault && row.enabled" :loading="warehouseStatusSaving === row.warehouseId" @click="toggleWarehouseStatus(row)">{{ row.enabled ? '停用' : '启用' }}</el-button></td></tr></tbody></table><p v-if="!warehouseLoading && !warehouseError && !warehouses.length" class="inventory-empty">尚无仓库。请先创建一个默认仓库。</p></div>
      <form v-if="createWarehouseOpen && canManage" class="panel inventory-form" @submit.prevent="createWarehouse"><h3>新建仓库</h3><div class="inventory-form-grid"><label>仓库编号 <span class="required">*</span><el-input :disabled="warehouseSaving" v-model="warehouseCode" maxlength="32" placeholder="例如 WH-001" /></label><label>仓库名称 <span class="required">*</span><el-input :disabled="warehouseSaving" v-model="warehouseName" maxlength="100" placeholder="例如 上海中心仓" /></label></div><p class="help-text">{{ warehouses.length ? '当前默认仓保持不变；此仓库创建为非默认仓。默认仓切换将在订单锁库规则接入时单独实现。' : '首个仓库将自动设为默认发货仓。' }}</p><p v-if="warehouseFormError" class="error" role="alert">{{ warehouseFormError }}</p><div class="inventory-actions"><el-button :disabled="warehouseSaving" @click="closeWarehouse">取消</el-button><el-button type="primary" native-type="submit" :loading="warehouseSaving">保存仓库</el-button></div></form>
    </div>

    <div v-else-if="tab === 'inbounds'" class="inventory-section">
      <div class="page-heading"><div><h1>入库单</h1><p>先保存草稿，核对 SKU、单位换算及数量，再确认入库。</p></div><el-button v-if="canManage" type="primary" :disabled="!canCreateDraft" @click="inboundOpen ? closeInbound() : openInbound()">{{ inboundOpen ? '关闭表单' : '创建入库单' }}</el-button></div>
      <p v-if="!canCreateDraft && canManage" class="hint">请先创建并启用仓库，才能创建入库单。</p>
      <form class="inventory-filters" @submit.prevent="loadInbounds(1)"><label>单号<el-input v-model="inboundDocumentNo" clearable maxlength="40" placeholder="输入入库单号" /></label><label>仓库<el-select v-model="inboundWarehouseId"><el-option label="全部仓库" value="" /><el-option v-for="row in warehouses" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label><label>状态<el-select v-model="inboundStatus"><el-option label="全部" value="" /><el-option label="草稿" value="DRAFT" /><el-option label="已确认" value="CONFIRMED" /></el-select></label><label>创建时间<InventoryDateRange v-model="inboundDates" /></label><el-button type="primary" native-type="submit" :loading="inboundLoading">查询</el-button><el-button @click="inboundDocumentNo = ''; inboundWarehouseId = ''; inboundStatus = ''; inboundDates = []; loadInbounds(1)">重置</el-button></form>
      <p v-if="inboundError" class="notice" role="alert">{{ inboundError }} <el-button link type="primary" @click="loadInbounds(inbounds.page)">刷新列表</el-button></p>
      <div class="panel table-wrap" v-loading="inboundLoading"><table><thead><tr><th><el-checkbox :model-value="allSelected" :disabled="inboundLoading" aria-label="全选当前页入库单" @change="toggleAll" /></th><th>入库单号</th><th>仓库</th><th>SKU 数</th><th>基础数量</th><th>来源 / 原因</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in inbounds.items" :key="row.inboundId"><td><el-checkbox :model-value="selectedIds.includes(rowKey(row))" :disabled="inboundLoading" :aria-label="`选择入库单 ${row.documentNo}`" @change="toggleRow(row, $event)" /></td><td class="code">{{ row.documentNo }}</td><td>{{ row.warehouseName || warehouseLabel(row.warehouseId) }}</td><td>{{ row.itemCount }}</td><td class="inventory-number">{{ row.totalBaseUnits }}</td><td>{{ row.reason }}</td><td><span :class="row.status === 'CONFIRMED' ? 'badge badge-good' : 'badge badge-muted'">{{ row.status === 'CONFIRMED' ? '已确认' : '草稿' }}</span></td><td>{{ formatTime(row.createdAt) }}</td><td><el-button link type="primary" :loading="detailLoading" @click="openInboundDetail(row.inboundId)">查看详情</el-button></td></tr></tbody></table><p v-if="!inboundLoading && !inboundError && !inbounds.items.length" class="inventory-empty">暂无入库单。创建草稿后可在这里重新打开并确认。</p></div>
      <InventoryListFooter :page="inbounds.page" :page-size="inbounds.pageSize" :total="inbounds.total" :selected-count="selectedIds.length" :loading="inboundLoading" @page="loadInbounds" @size="changePageSize('inbounds', $event)" @export="exportSelected" />

      <StockDocumentForm v-if="inboundOpen && canManage" kind="inbound" :warehouses="warehouses" :saving="draftSaving" :error="formError" @submit="createInbound" @close="closeInbound" @dirty="inboundDirty = $event" @change="formError = ''" />

      <p v-if="detailLoading" class="hint" role="status">正在读取入库单明细…</p>
      <div v-if="selectedInbound" class="panel inventory-detail" aria-live="polite"><div class="page-heading"><div><h3>入库单明细 · {{ selectedInbound.documentNo }}</h3><p>{{ selectedInbound.status === 'DRAFT' ? '草稿尚未改变账面库存。' : '已确认；库存流水已生成。' }}</p></div><span :class="selectedInbound.status === 'CONFIRMED' ? 'badge badge-good' : 'badge badge-muted'">{{ selectedInbound.status === 'CONFIRMED' ? '已确认' : '草稿' }}</span></div><p>仓库：{{ selectedInbound.warehouseName || warehouseLabel(selectedInbound.warehouseId) }}　·　原因：{{ selectedInbound.reason }}</p><div class="table-wrap"><table><thead><tr><th>商品 / SKU</th><th>操作单位</th><th>录入数量</th><th>换算关系</th><th>基础数量</th></tr></thead><tbody><tr v-for="row in selectedInbound.items" :key="row.skuId"><td><strong>{{ row.productName }}</strong><small class="inventory-subline code">{{ row.skuCode }}</small></td><td>{{ row.operationUnit }}</td><td class="inventory-number">{{ row.quantity }}</td><td>1 {{ row.operationUnit }} = {{ row.ratio }} {{ row.baseUnit }}</td><td class="inventory-number">{{ row.baseQuantity }} {{ row.baseUnit }}</td></tr></tbody></table></div><div v-if="selectedInbound.status === 'DRAFT' && canManage" class="inventory-actions"><el-button type="primary" :loading="confirmSaving" @click="confirmInbound">确认入库</el-button></div></div>
    </div>

    <div v-else-if="tab === 'outbounds'" class="inventory-section">
      <div class="page-heading"><div><h1>人工出库</h1><p>登记报损、样品领用等非销售用途；确认时按最新可售量校验，不允许负库存。</p></div><el-button v-if="canManage" type="primary" :disabled="!canCreateDraft" @click="outboundOpen ? closeOutbound() : openOutbound()">{{ outboundOpen ? '关闭表单' : '＋ 新建出库单' }}</el-button></div>
      <p v-if="!canCreateDraft && canManage" class="hint">请先创建并启用仓库，才能创建出库单。</p>
      <form class="inventory-filters" @submit.prevent="loadOutbounds(1)"><label>单号<el-input v-model="outboundDocumentNo" clearable maxlength="40" placeholder="输入出库单号" /></label><label>仓库<el-select v-model="outboundWarehouseId"><el-option label="全部仓库" value="" /><el-option v-for="row in warehouses" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label><label>状态<el-select v-model="outboundStatus"><el-option label="全部" value="" /><el-option label="草稿" value="DRAFT" /><el-option label="已确认" value="CONFIRMED" /></el-select></label><label>创建时间<InventoryDateRange v-model="outboundDates" /></label><el-button type="primary" native-type="submit" :loading="outboundLoading">查询</el-button><el-button @click="outboundDocumentNo = ''; outboundWarehouseId = ''; outboundStatus = ''; outboundDates = []; loadOutbounds(1)">重置</el-button></form>
      <p v-if="outboundError" class="notice" role="alert">{{ outboundError }} <el-button link type="primary" @click="loadOutbounds(outbounds.page)">刷新列表</el-button></p>
      <div class="panel table-wrap" v-loading="outboundLoading"><table><thead><tr><th><el-checkbox :model-value="allSelected" :disabled="outboundLoading" aria-label="全选当前页出库单" @change="toggleAll" /></th><th>出库单号</th><th>仓库</th><th>SKU 数</th><th>基础数量</th><th>出库原因</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in outbounds.items" :key="row.outboundId"><td><el-checkbox :model-value="selectedIds.includes(rowKey(row))" :disabled="outboundLoading" :aria-label="`选择出库单 ${row.documentNo}`" @change="toggleRow(row, $event)" /></td><td class="code">{{ row.documentNo }}</td><td>{{ row.warehouseName || warehouseLabel(row.warehouseId) }}</td><td>{{ row.itemCount }}</td><td class="inventory-number">{{ row.totalBaseUnits }}</td><td>{{ reasonLabel(row.reason) }}</td><td><span :class="row.status === 'CONFIRMED' ? 'badge badge-good' : 'badge badge-muted'">{{ row.status === 'CONFIRMED' ? '已确认' : '草稿' }}</span></td><td>{{ formatTime(row.createdAt) }}</td><td><el-button link type="primary" :loading="outboundDetailLoading" @click="openOutboundDetail(row.outboundId)">查看详情</el-button></td></tr></tbody></table><p v-if="!outboundLoading && !outboundError && !outbounds.items.length" class="inventory-empty">暂无人工出库单。可先保存草稿，核对后再确认。</p></div>
      <InventoryListFooter :page="outbounds.page" :page-size="outbounds.pageSize" :total="outbounds.total" :selected-count="selectedIds.length" :loading="outboundLoading" @page="loadOutbounds" @size="changePageSize('outbounds', $event)" @export="exportSelected" />
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

    <StocktakePanel v-else-if="tab === 'stocktakes'" :warehouses="warehouses" :can-manage="canManage" :can-review="canReview" @dirty="stocktakeDirty = $event" @busy="stocktakeBusy = $event" @approved="loadBalances(1); loadLedgers(1)" />

    <div v-else class="inventory-section">
      <div class="page-heading"><div><h1>库存流水</h1><p>按仓库、SKU 和单据追溯每一次已确认的库存变动。</p></div></div>
      <form class="inventory-filters" @submit.prevent="loadLedgers(1)"><label>商品、SKU 或单据号<el-input v-model="ledgerKeyword" maxlength="120" clearable placeholder="输入商品名称 / SKU 编码 / 单据号" /></label><label>仓库<el-select v-model="ledgerWarehouseId" placeholder="全部仓库"><el-option label="全部仓库" value="" /><el-option v-for="row in warehouses" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label><label>变动类型<el-select v-model="ledgerMovementType" placeholder="全部类型"><el-option label="全部类型" value="" /><el-option label="入库" value="INBOUND" /><el-option label="人工出库" value="OUTBOUND" /><el-option label="盘点调整" value="ADJUSTMENT" /><el-option label="订单销售" value="SALE" /><el-option label="未发货退款回库" value="REFUND" /><el-option label="退货验收回库" value="RETURN" /></el-select></label><label>发生时间<InventoryDateRange v-model="ledgerDates" /></label><el-button type="primary" native-type="submit" :loading="ledgerLoading">查询</el-button><el-button @click="ledgerKeyword = ''; ledgerWarehouseId = ''; ledgerMovementType = ''; ledgerDates = []; loadLedgers(1)">重置</el-button></form>
      <p v-if="ledgerError" class="notice" role="alert">{{ ledgerError }} <el-button link type="primary" @click="loadLedgers(ledgers.page)">重试</el-button></p>
      <div class="panel table-wrap" v-loading="ledgerLoading"><table><thead><tr><th><el-checkbox :model-value="allSelected" :disabled="ledgerLoading" aria-label="全选当前页流水" @change="toggleAll" /></th><th>时间</th><th>单据 / 关联对象</th><th>商品 / SKU</th><th>仓库</th><th>变动类型</th><th>数量（基础单位）</th><th>操作人</th><th>操作</th></tr></thead><tbody><tr v-for="row in ledgers.items" :key="row.ledgerId"><td><el-checkbox :model-value="selectedIds.includes(rowKey(row))" :disabled="ledgerLoading" :aria-label="`选择流水 ${row.documentNo}`" @change="toggleRow(row, $event)" /></td><td>{{ formatTime(row.occurredAt) }}</td><td class="code">{{ row.documentNo }}</td><td><strong>{{ row.productName }}</strong><small class="inventory-subline code">{{ row.skuCode }}</small></td><td>{{ row.warehouseName }}</td><td>{{ movementLabel(row.movementType) }}</td><td class="inventory-number" :class="{ 'inventory-available': row.deltaBaseUnits > 0 }">{{ signedQuantity(row.deltaBaseUnits) }} {{ row.baseUnit }}</td><td>{{ row.actorName }}</td><td><el-button link type="primary" :loading="ledgerDetailLoading" @click="openLedgerDetail(row.ledgerId)">详情</el-button></td></tr></tbody></table><p v-if="!ledgerLoading && !ledgerError && !ledgers.items.length" class="inventory-empty">暂无匹配流水。入库、人工出库、盘点调整及订单销售会在此追溯。</p></div>
      <InventoryListFooter :page="ledgers.page" :page-size="ledgers.pageSize" :total="ledgers.total" :selected-count="selectedIds.length" :loading="ledgerLoading" @page="loadLedgers" @size="changePageSize('ledgers', $event)" @export="exportSelected" />
      <p v-if="ledgerDetailLoading" class="hint" role="status">正在读取流水明细…</p>
      <div v-if="selectedLedger" class="panel inventory-detail" aria-live="polite"><h3>流水详情 · {{ selectedLedger.documentNo }}</h3><dl class="inventory-key-values"><dt>商品 / SKU</dt><dd>{{ selectedLedger.productName }} · {{ selectedLedger.skuCode }}</dd><dt>仓库</dt><dd>{{ selectedLedger.warehouseName }}</dd><dt>变动类型 / 时间</dt><dd>{{ movementLabel(selectedLedger.movementType) }} · {{ formatTime(selectedLedger.occurredAt) }}</dd><dt>变动前 / 后</dt><dd>{{ selectedLedger.balanceBefore }} {{ selectedLedger.baseUnit }} → {{ selectedLedger.balanceAfter }} {{ selectedLedger.baseUnit }}</dd><dt>操作数量</dt><dd>{{ selectedLedger.operationQuantity }} {{ selectedLedger.operationUnit }}</dd><dt>基本单位变动</dt><dd>{{ selectedLedger.deltaBaseUnits > 0 ? '+' : '' }}{{ selectedLedger.deltaBaseUnits }} {{ selectedLedger.baseUnit }}</dd><dt>单位换算快照</dt><dd>1 {{ selectedLedger.operationUnit }} = {{ selectedLedger.ratio }} {{ selectedLedger.baseUnit }}</dd><dt>变动原因 / 说明</dt><dd>{{ reasonLabel(selectedLedger.reason) }}<template v-if="selectedLedger.note"> · {{ selectedLedger.note }}</template></dd><dt>操作人</dt><dd>{{ selectedLedger.actorName }}</dd></dl><p class="inventory-conversion">已确认流水不可直接改写；错误通过有依据的后续调整单纠正，并保留原记录。</p></div>
    </div>
  </section>
</template>
