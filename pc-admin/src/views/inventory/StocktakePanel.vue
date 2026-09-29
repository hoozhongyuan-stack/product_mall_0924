<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, ApiError } from '../../api'
import { buildStocktakeSubmission } from './stocktake.mjs'
import type { InventorySku, Page, StocktakeDetail, StocktakeStatus, StocktakeSummary, Warehouse } from './types'

type CountInput = { count: string; reason: string }
const props = defineProps<{ warehouses: Warehouse[]; canManage: boolean; canReview: boolean }>()
const emit = defineEmits<{ dirty: [value: boolean]; busy: [value: boolean]; approved: [] }>()
const tasks = ref<Page<StocktakeSummary>>({ items: [], page: 1, pageSize: 20, total: 0 })
const listLoading = ref(false)
const listError = ref('')
const filterWarehouse = ref('')
const filterStatus = ref('')
const selected = ref<StocktakeDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const creating = ref(false)
const createWarehouse = ref('')
const createSkus = ref<string[]>([])
const skuChoices = ref<InventorySku[]>([])
const chosenSkus = ref<InventorySku[]>([])
const skuLoading = ref(false)
const createSaving = ref(false)
const createError = ref('')
const createKey = ref(crypto.randomUUID())
const createTouched = ref(false)
const counts = ref<Record<string, CountInput>>({})
const countTouched = ref(false)
const submitKey = ref(crypto.randomUUID())
const actionKeys = new Map<string, string>()
const actionSaving = ref(false)
const confirmationPending = ref(false)
let listSequence = 0
let detailSequence = 0
let skuSequence = 0

const hasDirtyCreate = computed(() => creating.value && createTouched.value)
const hasDirtyCount = computed(() => selected.value?.status === 'COUNTING' && countTouched.value)
const hasBookChanged = computed(() => selected.value?.status === 'PENDING_REVIEW'
  && selected.value.items.some((item) => item.bookChanged))
watch(() => createSaving.value || actionSaving.value || confirmationPending.value, value => emit('busy', value), { immediate: true })
watch(() => hasDirtyCreate.value || hasDirtyCount.value, (value) => emit('dirty', value), { immediate: true })
watch(() => props.warehouses, (rows) => {
  if (!createWarehouse.value) createWarehouse.value = rows.find((row) => row.enabled && row.isDefault)?.warehouseId
    || rows.find((row) => row.enabled)?.warehouseId || ''
}, { immediate: true })

function failure(reason: unknown) { return reason instanceof Error ? reason.message : '请求失败，请稍后重试。' }
function date(value: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function signed(value: number | null) { return value === null ? '—' : `${value > 0 ? '+' : ''}${value}` }
function statusName(status: StocktakeStatus) {
  return ({ COUNTING: '盘点中', PENDING_REVIEW: '待审核', APPROVED: '已通过' } as const)[status]
}
function statusBadge(status: StocktakeStatus) {
  return status === 'APPROVED' ? 'badge badge-good' : status === 'PENDING_REVIEW' ? 'badge badge-warning' : 'badge badge-muted'
}
function changeCreate() { createTouched.value = true; createKey.value = crypto.randomUUID(); createError.value = '' }
function changeCount(skuId: string, field: keyof CountInput, value: string) {
  counts.value = { ...counts.value, [skuId]: { ...counts.value[skuId], [field]: value } }
  countTouched.value = true
  submitKey.value = crypto.randomUUID()
  detailError.value = ''
}
function resetCreate() {
  creating.value = false
  createSkus.value = []
  chosenSkus.value = []
  createError.value = ''
  createTouched.value = false
  createKey.value = crypto.randomUUID()
}
function toggleCreate() {
  if (creating.value && hasDirtyCreate.value && !window.confirm('放弃尚未保存的盘点范围？')) return
  if (creating.value) resetCreate()
  else { creating.value = true; void searchSkus('') }
}
async function searchSkus(keyword: string) {
  const sequence = ++skuSequence
  skuLoading.value = true
  try {
    const query = new URLSearchParams({ page: '1', pageSize: '30' })
    if (keyword.trim()) query.set('keyword', keyword.trim())
    const result = await api<Page<InventorySku>>(`/inventory/skus?${query}`)
    if (sequence === skuSequence) skuChoices.value = result.items
  } catch (reason) { if (sequence === skuSequence) createError.value = failure(reason) }
  finally { if (sequence === skuSequence) skuLoading.value = false }
}
const skuOptions = computed(() => [...chosenSkus.value, ...skuChoices.value.filter((item) =>
  !chosenSkus.value.some((chosen) => chosen.skuId === item.skuId))])
function setCreateSkus(ids: string[]) {
  if (ids.length > 50) { createError.value = '每张盘点单最多选择 50 个 SKU。'; return }
  createSkus.value = ids
  chosenSkus.value = ids.map((id) => skuOptions.value.find((sku) => sku.skuId === id)).filter((item): item is InventorySku => !!item)
  changeCreate()
}
async function loadList(page = 1) {
  const sequence = ++listSequence
  listLoading.value = true
  listError.value = ''
  const query = new URLSearchParams({ page: String(page), pageSize: '20' })
  if (filterWarehouse.value) query.set('warehouseId', filterWarehouse.value)
  if (filterStatus.value) query.set('status', filterStatus.value)
  try {
    const result = await api<Page<StocktakeSummary>>(`/inventory/stocktakes?${query}`)
    if (sequence === listSequence) tasks.value = result
  } catch (reason) { if (sequence === listSequence) listError.value = failure(reason) }
  finally { if (sequence === listSequence) listLoading.value = false }
}
async function loadDetail(id: string, force = false) {
  if (hasDirtyCount.value && !force && !window.confirm('放弃当前未提交的实盘录入？')) return
  const sequence = ++detailSequence
  detailLoading.value = true
  detailError.value = ''
  try {
    const detail = await api<StocktakeDetail>(`/inventory/stocktakes/${encodeURIComponent(id)}`)
    if (sequence !== detailSequence) return
    selected.value = detail
    counts.value = Object.fromEntries(detail.items.map((item) => [item.skuId, {
      count: item.countedBaseUnits === null ? '' : String(item.countedBaseUnits), reason: item.reason || '',
    }]))
    countTouched.value = false
    submitKey.value = crypto.randomUUID()
  } catch (reason) { if (sequence === detailSequence) detailError.value = failure(reason) }
  finally { if (sequence === detailSequence) detailLoading.value = false }
}
async function createTask() {
  if (!props.canManage || createSaving.value) return
  if (!props.warehouses.some((warehouse) => warehouse.warehouseId === createWarehouse.value && warehouse.enabled)) {
    createError.value = '请选择启用中的仓库。'; return
  }
  if (!createSkus.value.length || createSkus.value.length > 50) { createError.value = '请选择 1 至 50 个 SKU。'; return }
  createSaving.value = true
  createError.value = ''
  try {
    const detail = await api<StocktakeDetail>('/inventory/stocktakes', {
      method: 'POST', headers: { 'Idempotency-Key': createKey.value },
      body: JSON.stringify({ warehouseId: createWarehouse.value, skuIds: createSkus.value }),
    })
    resetCreate()
    selected.value = detail
    counts.value = Object.fromEntries(detail.items.map((item) => [item.skuId, { count: '', reason: '' }]))
    countTouched.value = false
    ElMessage.success('盘点任务已创建，可录入实盘数量')
    await loadList(1)
  } catch (reason) { createError.value = failure(reason) }
  finally { createSaving.value = false }
}
async function submitCount() {
  const detail = selected.value
  if (!props.canManage || !detail || detail.status !== 'COUNTING' || actionSaving.value || confirmationPending.value) return
  let items: ReturnType<typeof buildStocktakeSubmission>
  try { items = buildStocktakeSubmission(detail.items, counts.value) }
  catch (reason) { detailError.value = failure(reason); return }
  confirmationPending.value = true
  try {
    await ElMessageBox.confirm('提交后服务端将重新读取最新账面并计算差异，盘点单进入审核，不能继续编辑。', '提交实盘', {
      confirmButtonText: '提交审核', cancelButtonText: '返回核对', type: 'warning',
    })
  } catch { return }
  finally { confirmationPending.value = false }
  actionSaving.value = true
  detailError.value = ''
  try {
    selected.value = await api<StocktakeDetail>(`/inventory/stocktakes/${encodeURIComponent(detail.stocktakeId)}/submit`, {
      method: 'POST', headers: { 'Idempotency-Key': submitKey.value },
      body: JSON.stringify({ expectedRevision: detail.revision, items }),
    })
    countTouched.value = false
    submitKey.value = crypto.randomUUID()
    ElMessage.success('实盘已提交，请复核最新账面与差异')
    await loadList(tasks.value.page)
  } catch (reason) { await handleActionFailure(reason, detail.stocktakeId) }
  finally { actionSaving.value = false }
}
async function handleActionFailure(reason: unknown, id: string) {
  detailError.value = failure(reason)
  if (reason instanceof ApiError && ['REVISION_CONFLICT', 'BOOK_CHANGED', 'UNIT_VERSION_CHANGED'].includes(reason.code)) {
    await loadDetail(id, true)
    detailError.value = `${failure(reason)} 已重新读取盘点单，请复核后操作。`
    await loadList(tasks.value.page)
  }
}
async function approve() {
  const detail = selected.value
  if (!props.canReview || !detail || detail.status !== 'PENDING_REVIEW' || actionSaving.value || confirmationPending.value || hasBookChanged.value) return
  confirmationPending.value = true
  try {
    await ElMessageBox.confirm(`审核 ${detail.documentNo}？通过后将按所列差异改变账面并生成不可改写的调整流水。`, '审核盘点差异', {
      confirmButtonText: '审核并生成调整流水', cancelButtonText: '返回核对', type: 'warning',
    })
  } catch { return }
  finally { confirmationPending.value = false }
  actionSaving.value = true
  detailError.value = ''
  const actionId = `approve:${detail.stocktakeId}:${detail.revision}`
  const key = actionKeys.get(actionId) || crypto.randomUUID()
  actionKeys.set(actionId, key)
  try {
    selected.value = await api<StocktakeDetail>(`/inventory/stocktakes/${encodeURIComponent(detail.stocktakeId)}/approve`, {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ expectedRevision: detail.revision }),
    })
    actionKeys.delete(actionId)
    ElMessage.success('审核已通过，调整流水已生成')
    await loadList(tasks.value.page)
    emit('approved')
  } catch (reason) { await handleActionFailure(reason, detail.stocktakeId) }
  finally { actionSaving.value = false }
}
async function returnForCorrection() {
  const detail = selected.value
  if (!props.canReview || !detail || detail.status !== 'PENDING_REVIEW' || actionSaving.value || confirmationPending.value) return
  let reason: string
  confirmationPending.value = true
  try {
    const answer = await ElMessageBox.prompt('请填写退回原因。退回后盘点人可补充实盘数量并重新提交。', '退回补充', {
      confirmButtonText: '确认退回', cancelButtonText: '取消', inputType: 'textarea',
      inputValidator: (value: string) => value.trim().length > 0 && value.trim().length <= 200 ? true : '请输入 1 至 200 字退回原因。',
    })
    reason = answer.value.trim()
  } catch { return }
  finally { confirmationPending.value = false }
  actionSaving.value = true
  detailError.value = ''
  const actionId = `return:${detail.stocktakeId}:${detail.revision}:${reason}`
  const key = actionKeys.get(actionId) || crypto.randomUUID()
  actionKeys.set(actionId, key)
  try {
    selected.value = await api<StocktakeDetail>(`/inventory/stocktakes/${encodeURIComponent(detail.stocktakeId)}/return`, {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ expectedRevision: detail.revision, reason }),
    })
    actionKeys.delete(actionId)
    ElMessage.success('已退回补充')
    await Promise.all([loadList(tasks.value.page), loadDetail(detail.stocktakeId, true)])
  } catch (error) { await handleActionFailure(error, detail.stocktakeId) }
  finally { actionSaving.value = false }
}

onMounted(() => { void loadList(1) })
</script>

<template>
  <div class="inventory-section stocktake-section">
    <div class="page-heading"><div><h1>盘点单</h1><p>按仓库与指定 SKU 发起盘点，实盘提交后审核差异；审核通过才调整账面。</p></div><el-button v-if="canManage" type="primary" @click="toggleCreate">{{ creating ? '关闭表单' : '＋ 创建盘点任务' }}</el-button></div>
    <p v-if="!canManage && !canReview" class="hint">当前账号可查看盘点记录。创建和提交需要库存管理权限，审核需要库存审核权限。</p>
    <form class="inventory-filters" @submit.prevent="loadList(1)"><label>仓库<el-select v-model="filterWarehouse" placeholder="全部仓库"><el-option label="全部仓库" value="" /><el-option v-for="row in warehouses" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label><label>状态<el-select v-model="filterStatus" placeholder="全部状态"><el-option label="全部状态" value="" /><el-option label="盘点中" value="COUNTING" /><el-option label="待审核" value="PENDING_REVIEW" /><el-option label="已通过" value="APPROVED" /></el-select></label><el-button type="primary" native-type="submit" :loading="listLoading">查询</el-button><el-button @click="filterWarehouse = ''; filterStatus = ''; loadList(1)">重置</el-button></form>
    <p v-if="listError" class="notice" role="alert">{{ listError }} <el-button link type="primary" @click="loadList(tasks.page)">重试</el-button></p>
    <div class="panel table-wrap" v-loading="listLoading"><table><thead><tr><th>盘点单号</th><th>仓库</th><th>盘点范围</th><th>SKU 数</th><th>创建人 / 时间</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="row in tasks.items" :key="row.stocktakeId"><td class="code">{{ row.documentNo }}</td><td>{{ row.warehouseName }}</td><td>指定 SKU</td><td class="inventory-number">{{ row.itemCount }}</td><td>{{ row.createdBy }}<small class="inventory-subline">{{ date(row.createdAt) }}</small></td><td><span :class="statusBadge(row.status)">{{ statusName(row.status) }}</span></td><td><el-button link type="primary" :loading="detailLoading" @click="loadDetail(row.stocktakeId)">{{ row.status === 'COUNTING' && canManage ? '录入实盘' : row.status === 'PENDING_REVIEW' && canReview ? '审核差异' : '查看详情' }}</el-button></td></tr></tbody></table><p v-if="!listLoading && !listError && !tasks.items.length" class="inventory-empty">暂无匹配盘点单。可选择仓库与 SKU 创建盘点任务。</p></div>
    <div v-if="tasks.total > tasks.pageSize" class="inventory-pagination"><span>共 {{ tasks.total }} 条</span><el-pagination :current-page="tasks.page" :page-size="tasks.pageSize" :total="tasks.total" layout="prev, pager, next" @current-change="loadList" /></div>

    <form v-if="creating && canManage" class="panel inventory-form stocktake-form" @submit.prevent="createTask"><h3>创建盘点任务</h3><div class="inventory-form-grid"><label>盘点仓库 <span class="required">*</span><el-select :model-value="createWarehouse" :disabled="createSaving" placeholder="选择仓库" @update:model-value="(value: string) => { createWarehouse = value; changeCreate() }"><el-option v-for="row in warehouses.filter((warehouse) => warehouse.enabled)" :key="row.warehouseId" :label="row.name" :value="row.warehouseId" /></el-select></label><label>SKU 范围 <span class="required">*</span><el-select :model-value="createSkus" multiple collapse-tags filterable remote reserve-keyword :remote-method="searchSkus" :loading="skuLoading" :disabled="createSaving" placeholder="搜索并选择 1 至 50 个 SKU" @update:model-value="setCreateSkus"><el-option v-for="sku in skuOptions" :key="sku.skuId" :label="`${sku.skuCode} · ${sku.productName}`" :value="sku.skuId" /></el-select></label></div><p class="help-text">已选 {{ createSkus.length }} / 50 个 SKU。任务创建时保存账面快照；盘点期间仓库可继续正常出入库。</p><p v-if="createError" class="error" role="alert">{{ createError }}</p><div class="inventory-actions"><el-button :disabled="createSaving" @click="toggleCreate">取消</el-button><el-button type="primary" native-type="submit" :loading="createSaving">创建任务</el-button></div></form>

    <p v-if="detailLoading" class="hint" role="status">正在读取盘点明细…</p>
    <p v-if="detailError" class="notice" role="alert">{{ detailError }} <el-button v-if="selected" link type="primary" @click="loadDetail(selected.stocktakeId, true)">重新读取</el-button></p>
    <section v-if="selected" class="panel inventory-detail stocktake-detail" aria-live="polite"><div class="page-heading"><div><h3>盘点明细 · {{ selected.documentNo }}</h3><p>仓库：{{ selected.warehouseName }}　·　创建：{{ selected.createdBy }}，{{ date(selected.createdAt) }}</p></div><span :class="statusBadge(selected.status)">{{ statusName(selected.status) }}</span></div>
      <p v-if="selected.reviewNote" class="inventory-conversion">最近审核意见：{{ selected.reviewNote }}</p>
      <p v-if="selected.status === 'COUNTING'" class="help-text">请录入基础单位实盘数。提交后服务端会读取最新账面，计算差异并交由有审核权限的账号处理。</p>
      <p v-else class="help-text">盘点人：{{ selected.submittedBy || '—' }}　·　提交时间：{{ date(selected.submittedAt) }}<template v-if="selected.reviewedBy">　·　审核人：{{ selected.reviewedBy }}，{{ date(selected.reviewedAt) }}</template></p>
      <div class="table-wrap"><table><thead><tr><th>商品 / SKU</th><th>任务账面</th><th>提交时账面</th><th>锁定</th><th>实盘</th><th>差异</th><th>差异原因</th></tr></thead><tbody><tr v-for="item in selected.items" :key="item.skuId"><td><strong>{{ item.productName }}</strong><small class="inventory-subline code">{{ item.skuCode }}</small></td><td class="inventory-number">{{ item.bookAtStartBaseUnits }} {{ item.baseUnit }}</td><td class="inventory-number">{{ item.bookAtSubmitBaseUnits === null ? '待提交' : `${item.bookAtSubmitBaseUnits} ${item.baseUnit}` }}<small v-if="selected.status === 'PENDING_REVIEW'" class="inventory-subline" :class="{ 'stock-shortfall': item.bookChanged }">当前 {{ item.currentBookBaseUnits }} {{ item.baseUnit }}</small></td><td class="inventory-number">{{ item.reservedAtSubmitBaseUnits === null ? '—' : `${item.reservedAtSubmitBaseUnits} ${item.baseUnit}` }}<small v-if="selected.status === 'PENDING_REVIEW'" class="inventory-subline" :class="{ 'stock-shortfall': item.bookChanged }">当前 {{ item.currentReservedBaseUnits }} {{ item.baseUnit }}</small></td><td><el-input v-if="selected.status === 'COUNTING' && canManage" :model-value="counts[item.skuId]?.count || ''" inputmode="numeric" aria-label="实盘数量" placeholder="非负整数" @update:model-value="(value: string) => changeCount(item.skuId, 'count', value)" /><span v-else class="inventory-number">{{ item.countedBaseUnits === null ? '—' : `${item.countedBaseUnits} ${item.baseUnit}` }}</span></td><td class="inventory-number" :class="{ 'stock-shortfall': (item.deltaBaseUnits || 0) < 0, 'inventory-available': (item.deltaBaseUnits || 0) > 0 }">{{ signed(item.deltaBaseUnits) }}<template v-if="item.deltaBaseUnits !== null"> {{ item.baseUnit }}</template></td><td><el-input v-if="selected.status === 'COUNTING' && canManage" :model-value="counts[item.skuId]?.reason || ''" maxlength="200" aria-label="差异原因" placeholder="有差异时填写" @update:model-value="(value: string) => changeCount(item.skuId, 'reason', value)" /><span v-else>{{ item.reason || '—' }}</span></td></tr></tbody></table></div>
      <p v-if="selected.status === 'PENDING_REVIEW'" class="inventory-conversion">任务账面与提交时账面分别展示。审核时服务端会再次核对账面；期间如有变动，需退回并重新提交。审核通过后才生成调整流水。</p>
      <p v-if="hasBookChanged" class="notice" role="alert">提交后账面或锁定量已变化，请退回补充并重新提交；当前差异不可审核入账。</p>
      <p v-if="selected.status === 'APPROVED'" class="inventory-conversion">差异已审核入账。流水保留原账面、调整数量、原因与审核人。</p>
      <div v-if="selected.status === 'COUNTING' && canManage" class="inventory-actions"><el-button type="primary" :loading="actionSaving" @click="submitCount">提交差异审核</el-button></div>
      <div v-if="selected.status === 'PENDING_REVIEW' && canReview" class="inventory-actions"><el-button :disabled="actionSaving" @click="returnForCorrection">退回补充</el-button><el-button type="primary" :loading="actionSaving" :disabled="hasBookChanged" @click="approve">审核并生成调整流水</el-button></div>
      <p v-if="selected.status === 'PENDING_REVIEW' && !canReview" class="hint">当前账号没有库存审核权限，可查看差异但不能审核。</p>
    </section>
  </div>
</template>
