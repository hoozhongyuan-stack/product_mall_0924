<script setup lang="ts">
import { csvTable } from '../../shared/csv.mjs'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import ProductMediaEditor from './ProductMediaEditor.vue'
import { ApiError, api, type Account } from '../../api'
import ProductCreateForm from './ProductCreateForm.vue'
import ProductSpecEditor from './ProductSpecEditor.vue'
import type { Asset, Category, MemberGrade, ProductDetail, SaleStatus, SkuPage, SkuRow } from './types'
import { fenToYuan, yuanToFen } from './types'

type SkuAction = 'status' | 'price' | 'prices' | 'unit'
type BatchAction = 'ON_SALE' | 'OFF_SALE' | 'CATEGORY'
interface BatchItemResult { id: string; success: boolean; code?: string; message?: string; revision?: number; skipped?: boolean }
interface BatchResult { results: BatchItemResult[]; successCount: number; failedCount: number }
interface CategoryBatchPreview {
  previewToken: string
  productCount: number
  skuCount: number
  otherSkuCount: number
  items: { productId: string; productNo: string; selectedSkuCount: number; totalSkuCount: number;
    productRevision: number; canChange: boolean; reason?: string }[]
}
const props = defineProps<{ account: Account }>()
const emit = defineEmits<{ dirtyChange: [dirty: boolean]; workspaceChange: [open: boolean] }>()
const permissions = computed(() => props.account.permissionCodes)
const canUpload = computed(() => permissions.value.includes('asset.upload'))
const canWrite = computed(() => permissions.value.includes('catalog.write'))
const canStatus = computed(() => permissions.value.includes('sku.status.write'))
const canPrice = computed(() => permissions.value.includes('sku.price.write'))
const canUnit = computed(() => permissions.value.includes('sku.unit.write'))
const canCreate = computed(() => canWrite.value && canStatus.value && canPrice.value && canUnit.value)
const canEditSpecs = computed(() => canCreate.value && editingProduct.value?.status === 'DRAFT')
const categories = ref<Category[]>([])
const grades = ref<MemberGrade[]>([])
const pageData = ref<SkuPage>({ rows: [], page: 1, pageSize: 20, total: 0 })
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const keyword = ref('')
const categoryId = ref('')
const statusFilter = ref('')
const fulfillmentFilter = ref('')
const selectedSkuIds = ref<string[]>([])
const batchAction = ref<BatchAction | null>(null)
const batchCategoryId = ref('')
const categoryPreview = ref<CategoryBatchPreview | null>(null)
const previewLoading = ref(false)
const categoryBatchKey = ref('')
const batchResult = ref<{ action: BatchAction; items: (BatchItemResult & { label: string })[];
  successCount: number; failedCount: number; skippedCount: number } | null>(null)
const inlineGradeDrafts = ref<Record<string, Record<string, string>>>({})
const inlineErrors = ref<Record<string, string>>({})
const savingGradeSku = ref('')
const formOpen = ref(false)
const createDirty = ref(false)
const editingProduct = ref<ProductDetail | null>(null)
const productLoading = ref(false)
const loadingProductId = ref('')
const productLoadError = ref('')
const productEditorError = ref('')
const productFieldErrors = ref<{ name?: string; category?: string }>({})
const productNameInput = ref<HTMLInputElement | null>(null)
const productCategorySelect = ref<HTMLSelectElement | null>(null)
const specDirty = ref(false)
let productLoadSequence = 0
const productName = ref('')
const productCategoryId = ref('')
const productFulfillment = ref<'SHIP' | 'REDEEM'>('SHIP')
const productRedeemValidUntil = ref('')
const productDescription = ref('')
const productMainImage = ref<Asset | null>(null)
const productGalleryImages = ref<Asset[]>([])
const productVideo = ref<Asset | null>(null)
const productMediaBusy = ref(false)
const createBusy = ref(false)
const editingSku = ref<SkuRow | null>(null)
const skuAction = ref<SkuAction>('status')
const skuStatus = ref<SaleStatus>('OFF_SALE')
const dailyPriceYuan = ref('')
const gradeInputs = ref<Record<string, string>>({})
const baseUnit = ref('')
const saleUnit = ref('')
const ratio = ref(1)
const editorOpen = computed(() => Boolean(formOpen.value || editingProduct.value || editingSku.value || productLoading.value || productLoadError.value))
const workspaceHeading = ref<HTMLElement | null>(null)
let editorTrigger: HTMLElement | null = null
watch(editorOpen, async open => {
  emit('workspaceChange', open)
  await nextTick()
  if (open) { workspaceHeading.value?.focus(); workspaceHeading.value?.scrollIntoView?.({ block: 'start' }) }
  else editorTrigger?.focus()
})
const productDirty = computed(() => {
  const original = editingProduct.value
  if (!original) return false
  return productName.value !== original.name || productCategoryId.value !== original.categoryId
    || productFulfillment.value !== original.fulfillmentKind
    || (productFulfillment.value === 'REDEEM' ? productRedeemValidUntil.value : '') !== (original.redeemValidUntil || '')
    || productDescription.value !== (original.descriptionHtml || '')
    || productMainImage.value?.assetId !== (original.mainImage?.assetId ?? undefined)
    || productGalleryImages.value.map((item) => item.assetId).join(',') !== (original.galleryImages || []).map((item) => item.assetId).join(',')
    || productVideo.value?.assetId !== (original.video?.assetId ?? undefined)
    || productMediaBusy.value
})
const skuDirty = computed(() => {
  const original = editingSku.value
  if (!original) return false
  if (skuAction.value === 'status') return skuStatus.value !== original.saleStatus
  if (skuAction.value === 'price') return dailyPriceYuan.value !== fenToYuan(original.listPriceFen)
  if (skuAction.value === 'unit') return baseUnit.value !== original.unit.baseUnit
    || saleUnit.value !== original.unit.saleUnit || ratio.value !== original.unit.ratio
  return grades.value.some((grade) => grade.enabled && (gradeInputs.value[grade.id] || '') !==
    (original.gradePrices.find((price) => price.gradeId === grade.id)
      ? fenToYuan(original.gradePrices.find((price) => price.gradeId === grade.id)!.priceFen) : ''))
})
const hasUnsaved = computed(() => (formOpen.value && createDirty.value) || productDirty.value || specDirty.value || skuDirty.value)
watch(hasUnsaved, (value) => emit('dirtyChange', value), { immediate: true })
const categoryName = (id: string) => categories.value.find((item) => item.id === id)?.name || '已停用或未知分类'
const gradeName = (id: string) => {
  const grade = grades.value.find((item) => item.id === id)
  return grade ? `${grade.name}${grade.enabled ? '' : '（已停用）'}` : id
}
const hasActiveLeaf = computed(() => categories.value.some((item) => item.parentId && item.status === 'ACTIVE'
  && categories.value.some((parent) => parent.id === item.parentId && parent.status === 'ACTIVE')))
const hasNext = computed(() => pageData.value.page * pageData.value.pageSize < pageData.value.total)
let loadSequence = 0
const selectedRows = computed(() => pageData.value.rows.filter((row) => selectedSkuIds.value.includes(row.skuId)))
const selectedProducts = computed(() => [...new Map(selectedRows.value.map((row) => [row.productId, row])).values()])
const allPageSelected = computed(() => pageData.value.rows.length > 0 && selectedRows.value.length === pageData.value.rows.length)
const eligibleRows = computed(() => selectedRows.value.filter((row) => batchAction.value === 'CATEGORY'
  ? true : row.saleStatus !== batchAction.value))
const activeLeafCategories = computed(() => categories.value.filter((row) => row.parentId && row.status === 'ACTIVE'
  && categories.value.some((parent) => parent.id === row.parentId && parent.status === 'ACTIVE')))
watch([keyword, categoryId, statusFilter, fulfillmentFilter], () => { clearSelection() })
watch(batchCategoryId, () => { categoryPreview.value = null; categoryBatchKey.value = '' })

function clearSelection() {
  selectedSkuIds.value = []
  batchAction.value = null
  batchResult.value = null
  categoryPreview.value = null
  categoryBatchKey.value = ''
}
function setSelected(id: string, checked: boolean) {
  selectedSkuIds.value = checked ? [...selectedSkuIds.value, id] : selectedSkuIds.value.filter((item) => item !== id)
  batchAction.value = null
  batchResult.value = null
  categoryPreview.value = null
}
function togglePage(checked: boolean) {
  selectedSkuIds.value = checked ? pageData.value.rows.map((row) => row.skuId) : []
  batchAction.value = null
  batchResult.value = null
  categoryPreview.value = null
}

async function load(page = 1) {
  const sequence = ++loadSequence
  clearSelection()
  loading.value = true
  error.value = ''
  try {
    const query = new URLSearchParams({ page: String(page), pageSize: '20' })
    if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
    if (categoryId.value) query.set('categoryId', categoryId.value)
    if (statusFilter.value) query.set('status', statusFilter.value)
    if (fulfillmentFilter.value) query.set('fulfillmentKind', fulfillmentFilter.value)
    const [categoryRows, gradeRows, result] = await Promise.all([
      api<Category[]>('/categories'), api<MemberGrade[]>('/member-grades'), api<SkuPage>(`/sku-rows?${query}`),
    ])
    if (sequence !== loadSequence) return
    categories.value = categoryRows
    grades.value = gradeRows
    pageData.value = result
    inlineGradeDrafts.value = Object.fromEntries(result.rows.map((row) => [row.skuId,
      Object.fromEntries(row.gradePrices.map((price) => [price.gradeId, fenToYuan(price.priceFen)]))]))
    inlineErrors.value = {}
  } catch (reason) {
    if (sequence === loadSequence) error.value = reason instanceof Error ? reason.message : '商品加载失败。'
  } finally { if (sequence === loadSequence) loading.value = false }
}
onMounted(() => { void load() })
function applyFilters() { void load(1) }
function clearFilters() { keyword.value = ''; categoryId.value = ''; statusFilter.value = ''; fulfillmentFilter.value = ''; void load(1) }
function canLeaveEditor() { return !saving.value && !productMediaBusy.value && !createBusy.value && (!hasUnsaved.value || window.confirm('当前商品资料尚未保存，离开后已填写的内容会丢失。确定继续吗？')) }
function closeEditors(force = false) {
  if (!force && !canLeaveEditor()) return false
  productLoadSequence += 1
  productLoading.value = false
  productLoadError.value = ''
  productEditorError.value = ''
  productFieldErrors.value = {}
  specDirty.value = false
  productMediaBusy.value = false
  createBusy.value = false
  formOpen.value = false
  createDirty.value = false
  editingProduct.value = null
  editingSku.value = null
  error.value = ''
  return true
}
function openCreate() {
  if (!canLeaveEditor()) return
  editorTrigger = document.activeElement as HTMLElement | null
  closeEditors(true)
  formOpen.value = true
}

async function openProduct(id: string) {
  if (!canLeaveEditor()) return
  editorTrigger = document.activeElement as HTMLElement | null
  closeEditors(true)
  const sequence = ++productLoadSequence
  loadingProductId.value = id
  productLoading.value = true
  productLoadError.value = ''
  try {
    const item = await api<ProductDetail>(`/products/${id}`)
    if (sequence !== productLoadSequence) return
    editingProduct.value = item
    productName.value = item.name
    productCategoryId.value = item.categoryId
    productFulfillment.value = item.fulfillmentKind
    productRedeemValidUntil.value = item.redeemValidUntil || ''
    productDescription.value = item.descriptionHtml || ''
    productMainImage.value = item.mainImage ?? null
    productGalleryImages.value = item.galleryImages || []
    productVideo.value = item.video ?? null
  } catch (reason) {
    if (sequence === productLoadSequence) productLoadError.value = reason instanceof Error ? reason.message : '商品详情加载失败。'
  } finally { if (sequence === productLoadSequence) productLoading.value = false }
}

async function saveProduct() {
  const target = editingProduct.value
  if (!target || saving.value || !canWrite.value) return
  const activeCategory = activeLeafCategories.value.some((item) => item.id === productCategoryId.value)
  productFieldErrors.value = {
    ...(productName.value.trim() ? {} : { name: '请填写商品名称。' }),
    ...(activeCategory ? {} : { category: '请选择启用的二级分类。' }),
  }
  if (Object.keys(productFieldErrors.value).length) {
    productEditorError.value = '请检查标出的必填字段。'
    await nextTick()
    if (productFieldErrors.value.name) productNameInput.value?.focus()
    else productCategorySelect.value?.focus()
    return
  }
  if (target.status === 'ON_SALE' && !productMainImage.value) {
    productEditorError.value = '商品上架前须上传正方形主图。'
    return
  }
  if (productMediaBusy.value) { productEditorError.value = '请等待素材上传完成后再保存。'; return }
  saving.value = true
  productEditorError.value = ''
  try {
    const saved = await api<ProductDetail>(`/products/${target.productId}`, { method: 'PATCH', body: JSON.stringify({ name: productName.value.trim(),
      categoryId: productCategoryId.value, fulfillmentKind: productFulfillment.value,
      redeemValidUntil: productFulfillment.value === 'REDEEM' ? productRedeemValidUntil.value || null : null,
      descriptionHtml: productDescription.value.trim(), expectedRevision: target.productRevision,
      mainImageAssetId: productMainImage.value?.assetId ?? null,
      galleryAssetIds: productGalleryImages.value.map((item) => item.assetId),
      videoAssetId: productVideo.value?.assetId ?? null }) })
    editingProduct.value = saved
    productName.value = saved.name
    productCategoryId.value = saved.categoryId
    productFulfillment.value = saved.fulfillmentKind
    productRedeemValidUntil.value = saved.redeemValidUntil || ''
    productDescription.value = saved.descriptionHtml || ''
    productMainImage.value = saved.mainImage
    productGalleryImages.value = saved.galleryImages
    productVideo.value = saved.video
    ElMessage.success('商品资料已保存')
    await load(pageData.value.page)
  } catch (reason) { productEditorError.value = reason instanceof ApiError && reason.code === 'REVISION_CONFLICT'
    ? '商品已被其他人修改。已保留当前表单，请复制需要保留的内容后重新加载。'
    : reason instanceof Error ? reason.message : '商品保存失败。' }
  finally { saving.value = false }
}

function specsSaved() {
  closeEditors(true)
  ElMessage.success('规格与 SKU 已保存')
  void load(pageData.value.page)
}

function openSku(row: SkuRow, action: SkuAction) {
  if (!canLeaveEditor()) return
  editorTrigger = document.activeElement as HTMLElement | null
  closeEditors(true)
  editingSku.value = row
  skuAction.value = action
  skuStatus.value = row.saleStatus
  dailyPriceYuan.value = fenToYuan(row.listPriceFen)
  gradeInputs.value = Object.fromEntries(row.gradePrices.map((price) => [price.gradeId, fenToYuan(price.priceFen)]))
  baseUnit.value = row.unit.baseUnit
  saleUnit.value = row.unit.saleUnit
  ratio.value = row.unit.ratio
}
function updateGrade(id: string, value: string) { gradeInputs.value = { ...gradeInputs.value, [id]: value } }

async function saveSku() {
  const row = editingSku.value
  if (!row) return
  let path: string
  let method: 'PATCH' | 'PUT'
  let body: Record<string, unknown>
  if (skuAction.value === 'status') {
    path = `/skus/${row.skuId}/status`; method = 'PATCH'
    body = { saleStatus: skuStatus.value, expectedRevision: row.skuRevision }
  } else if (skuAction.value === 'price') {
    const listPriceFen = yuanToFen(dailyPriceYuan.value)
    if (listPriceFen === null) { error.value = '日常价格式无效，请输入最多两位小数的非负金额。'; return }
    path = `/skus/${row.skuId}/price`; method = 'PATCH'
    body = { listPriceFen, expectedRevision: row.skuRevision }
  } else if (skuAction.value === 'prices') {
    const gradePrices = []
    for (const grade of grades.value.filter((item) => item.enabled)) {
      const raw = gradeInputs.value[grade.id]?.trim() || ''
      if (!raw) continue
      const priceFen = yuanToFen(raw)
      if (priceFen === null) { error.value = `${grade.name} 的价格格式无效，请输入最多两位小数的非负金额。`; return }
      gradePrices.push({ gradeId: grade.id, priceFen })
    }
    path = `/skus/${row.skuId}/grade-prices`; method = 'PUT'
    body = { gradePrices, expectedRevision: row.skuRevision }
  } else {
    if (!baseUnit.value.trim() || !saleUnit.value.trim() || !Number.isInteger(ratio.value) || ratio.value < 1) {
      error.value = '请填写单位；换算比须为正整数。'; return
    }
    path = `/skus/${row.skuId}/unit`; method = 'PUT'
    body = { unit: { baseUnit: baseUnit.value.trim(), saleUnit: saleUnit.value.trim(), ratio: ratio.value }, expectedRevision: row.skuRevision }
  }
  saving.value = true
  error.value = ''
  try {
    await api(path, { method, body: JSON.stringify(body) })
    editingSku.value = null
    ElMessage.success('SKU 已保存')
    await load(pageData.value.page)
  } catch (reason) { await handleWriteError(reason, 'SKU 保存失败。') }
  finally { saving.value = false }
}

async function handleWriteError(reason: unknown, fallback: string) {
  if (reason instanceof ApiError && reason.code === 'REVISION_CONFLICT') {
    error.value = '记录已被其他人修改。当前表单已保留，请复制需要保留的内容后重新加载。'
  } else { error.value = reason instanceof Error ? reason.message : fallback }
}
function startBatch(action: BatchAction) {
  if (!selectedRows.value.length) return
  batchAction.value = action
  batchCategoryId.value = ''
  categoryPreview.value = null
  categoryBatchKey.value = ''
  batchResult.value = null
  error.value = ''
}

function batchReason(row: SkuRow): string {
  if (batchAction.value === 'CATEGORY') {
    if (!categoryPreview.value) return '待核对影响范围'
    const item = categoryPreview.value.items.find((entry) => entry.productId === row.productId)
    return item?.canChange ? '' : item?.reason || '不可调整'
  }
  if (row.saleStatus === batchAction.value) return '已是目标状态'
  return ''
}

async function requestCategoryPreview() {
  if (!batchCategoryId.value || !selectedSkuIds.value.length) return
  const requestedCategoryId = batchCategoryId.value
  const requestedSkuIds = [...selectedSkuIds.value]
  previewLoading.value = true
  categoryPreview.value = null
  error.value = ''
  try {
    const preview = await api<CategoryBatchPreview>('/products/batch-category/preview', {
      method: 'POST', body: JSON.stringify({ skuIds: requestedSkuIds, categoryId: requestedCategoryId }),
    })
    if (batchAction.value !== 'CATEGORY' || batchCategoryId.value !== requestedCategoryId
      || selectedSkuIds.value.join(',') !== requestedSkuIds.join(',')) return
    categoryPreview.value = preview
    categoryBatchKey.value = crypto.randomUUID()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '影响范围核对失败，请重试。' }
  finally { previewLoading.value = false }
}

async function executeBatch() {
  const action = batchAction.value
  if (!action || !selectedRows.value.length) return
  if (action === 'CATEGORY' && !categoryPreview.value) { error.value = '请先核对服务端返回的影响范围。'; return }
  const categoryTargets = action === 'CATEGORY' ? categoryPreview.value!.items.filter((item) => item.canChange) : []
  const skuTargets = action === 'CATEGORY' ? [] : eligibleRows.value
  if (!categoryTargets.length && !skuTargets.length) { error.value = '没有可执行的项目，请核对所选内容。'; return }
  const labels = new Map<string, string>(action === 'CATEGORY'
    ? categoryTargets.map((row) => [row.productId, `${selectedProducts.value.find((item) => item.productId === row.productId)?.productName || row.productNo}（${row.productNo}）`])
    : skuTargets.map((row) => [row.skuId, row.skuCode]))
  const skipped: (BatchItemResult & { label: string })[] = action === 'CATEGORY'
    ? categoryPreview.value!.items.filter((item) => !item.canChange).map((item) => ({ id: item.productId,
      label: `${selectedProducts.value.find((row) => row.productId === item.productId)?.productName || item.productNo}（${item.productNo}）`,
      success: false, skipped: true, message: item.reason || '不可调整' }))
    : selectedRows.value.filter((row) => !skuTargets.some((target) => target.skuId === row.skuId)).map((row) => ({
      id: row.skuId, label: row.skuCode, success: false, skipped: true, message: batchReason(row),
    }))
  saving.value = true
  error.value = ''
  try {
    const path = action === 'CATEGORY' ? '/products/batch-category' : '/sku-rows/batch-status'
    const body = action === 'CATEGORY'
      ? { categoryId: batchCategoryId.value, previewToken: categoryPreview.value!.previewToken,
        items: categoryTargets.map((row) => ({ productId: row.productId, expectedRevision: row.productRevision })) }
      : { saleStatus: action, items: skuTargets.map((row) => ({ skuId: row.skuId, expectedRevision: row.skuRevision })) }
    const result = await api<BatchResult>(path, { method: 'POST',
      headers: action === 'CATEGORY' ? { 'Idempotency-Key': categoryBatchKey.value } : undefined,
      body: JSON.stringify(body) })
    await load(pageData.value.page)
    batchResult.value = { action, successCount: result.successCount, failedCount: result.failedCount,
      skippedCount: skipped.length,
      items: [...result.results.map((item) => ({ ...item, label: labels.get(item.id) || item.id })), ...skipped] }
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '批量操作失败，请稍后重试。' }
  finally { saving.value = false }
}

function updateInlineGrade(skuId: string, gradeId: string, value: string) {
  inlineGradeDrafts.value = { ...inlineGradeDrafts.value,
    [skuId]: { ...inlineGradeDrafts.value[skuId], [gradeId]: value } }
  inlineErrors.value = { ...inlineErrors.value, [skuId]: '' }
}

function inlineGradeChanged(row: SkuRow): boolean {
  return grades.value.some((grade) => grade.enabled &&
    (inlineGradeDrafts.value[row.skuId]?.[grade.id]?.trim() || '') !==
    (row.gradePrices.find((price) => price.gradeId === grade.id)
      ? fenToYuan(row.gradePrices.find((price) => price.gradeId === grade.id)!.priceFen) : ''))
}

async function saveInlineGrade(row: SkuRow) {
  const gradePrices: { gradeId: string; priceFen: number }[] = []
  for (const grade of grades.value.filter((item) => item.enabled)) {
    const raw = inlineGradeDrafts.value[row.skuId]?.[grade.id]?.trim() || ''
    if (!raw) continue
    const priceFen = yuanToFen(raw)
    if (priceFen === null) {
      inlineErrors.value = { ...inlineErrors.value, [row.skuId]: `${grade.name}价格格式无效，请输入最多两位小数的非负金额。` }
      return
    }
    gradePrices.push({ gradeId: grade.id, priceFen })
  }
  savingGradeSku.value = row.skuId
  inlineErrors.value = { ...inlineErrors.value, [row.skuId]: '' }
  try {
    const updated = await api<SkuRow>(`/skus/${row.skuId}/grade-prices`, { method: 'PUT',
      body: JSON.stringify({ gradePrices, expectedRevision: row.skuRevision }) })
    pageData.value = { ...pageData.value, rows: pageData.value.rows.map((item) =>
      item.skuId === row.skuId ? { ...item, ...updated } : item) }
    inlineGradeDrafts.value = { ...inlineGradeDrafts.value,
      [row.skuId]: Object.fromEntries(updated.gradePrices.map((price) => [price.gradeId, fenToYuan(price.priceFen)])) }
    ElMessage.success(`${row.skuCode} 等级价已保存`)
  } catch (reason) {
    const conflict = reason instanceof ApiError && reason.code === 'REVISION_CONFLICT'
    if (conflict) await load(pageData.value.page)
    inlineErrors.value = { ...inlineErrors.value, [row.skuId]: conflict
      ? '记录已被其他人修改，列表已刷新。请核对后重填。'
      : reason instanceof Error ? reason.message : '等级价保存失败。' }
  } finally { savingGradeSku.value = '' }
}

function exportSelected() {
  if (!selectedRows.value.length) return
  const header = ['商品编号', '商品名称', '规格', '商品类型', '分类', 'SKU编码', '日常价（元）', '等级价（元）', '商品状态', 'SKU状态', '库存']
  const rows = selectedRows.value.map((row) => [row.productNo, row.productName,
    row.specs.map((spec) => `${spec.name}:${spec.value}`).join(' / ') || '默认规格',
    row.fulfillmentKind === 'SHIP' ? '快递发货' : '到店核销', categoryName(row.categoryId), row.skuCode,
    fenToYuan(row.listPriceFen), row.gradePrices.map((price) => `${gradeName(price.gradeId)}:${fenToYuan(price.priceFen)}`).join(' / '),
    row.productStatus, row.saleStatus, '未接入'])
  const csv = csvTable([header, ...rows])
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `sku-selected-${new Date().toISOString().slice(0, 10)}.csv`
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function created() { createBusy.value = false; formOpen.value = false; createDirty.value = false; ElMessage.success('商品草稿已创建'); void load(1) }
</script>

<template>
  <div class="catalog-section">
    <div v-show="!editorOpen" class="catalog-list">
    <div class="page-heading"><div><h1>商品管理</h1><p>按 SKU 管理商品、价格与销售状态。</p></div>
      <button v-if="canCreate" class="primary-button" type="button" :disabled="loading || !hasActiveLeaf || productMediaBusy || createBusy" @click="openCreate">新建商品</button>
    </div>
    <p v-if="canCreate && !loading && !hasActiveLeaf" class="hint">请先在“分类”中创建启用的一级及二级分类。</p>
    <p v-if="canWrite && !canCreate" class="hint">创建商品包含 SKU 状态、价格和单位写入，当前账号需要这三项权限。</p>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <form class="catalog-filters" role="search" @submit.prevent="applyFilters">
      <label>搜索商品或 SKU<input v-model="keyword" placeholder="名称或编号" /></label>
      <label>分类<select v-model="categoryId"><option value="">全部分类</option><option v-for="item in categories.filter((row) => row.parentId)" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label>商品类型<select v-model="fulfillmentFilter"><option value="">全部类型</option><option value="SHIP">快递发货</option><option value="REDEEM">到店核销</option></select></label>
      <label>SKU 状态<select v-model="statusFilter"><option value="">全部状态</option><option value="ON_SALE">上架</option><option value="OFF_SALE">下架</option></select></label>
      <button class="primary-button" type="submit" :disabled="loading">查询</button><button class="secondary-button" type="button" :disabled="loading" @click="clearFilters">重置</button>
    </form>
    <p v-if="loading" class="loading-inline" role="status">正在加载商品…</p>
    <template v-else>
      <div class="catalog-batch-toolbar" aria-label="所选 SKU 操作">
        <span><strong>已选 {{ selectedRows.length }} 个 SKU</strong> · 涉及 {{ selectedProducts.length }} 个商品 · 仅当前页有效</span>
        <div class="catalog-batch-buttons">
          <button v-if="canStatus" class="secondary-button" type="button" :disabled="!selectedRows.length || saving" @click="startBatch('ON_SALE')">批量上架 SKU</button>
          <button v-if="canStatus" class="secondary-button" type="button" :disabled="!selectedRows.length || saving" @click="startBatch('OFF_SALE')">批量下架 SKU</button>
          <button v-if="canWrite" class="secondary-button" type="button" :disabled="!selectedRows.length || saving" @click="startBatch('CATEGORY')">批量调整分类</button>
          <button class="secondary-button" type="button" :disabled="!selectedRows.length" @click="exportSelected">导出所选</button>
        </div>
        <small>每页 20 条</small>
      </div>
      <div class="panel table-wrap catalog-goods-table" tabindex="0" aria-label="商品 SKU 列表，可横向滚动">
        <table><thead><tr><th scope="col"><input type="checkbox" aria-label="全选本页 SKU" :checked="allPageSelected" :disabled="!pageData.rows.length" @change="togglePage(($event.target as HTMLInputElement).checked)" /></th><th scope="col">商品名称 / 规格</th><th scope="col">类型</th><th scope="col">SKU 编码</th><th scope="col">日常价</th><th scope="col">等级价（列表内编辑）</th><th scope="col">库存</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="row in pageData.rows" :key="row.skuId">
            <td><input type="checkbox" :aria-label="`选择 ${row.skuCode}`" :checked="selectedSkuIds.includes(row.skuId)" @change="setSelected(row.skuId, ($event.target as HTMLInputElement).checked)" /></td>
            <td><strong class="strong-cell">{{ row.productName }}</strong><span class="catalog-specs">{{ row.specs.length ? row.specs.map((spec) => `${spec.name}：${spec.value}`).join(' · ') : '默认规格' }}</span><small class="catalog-product-meta">商品编号 {{ row.productNo }} · {{ categoryName(row.categoryId) }}</small></td>
            <td>{{ row.fulfillmentKind === 'SHIP' ? '快递发货' : '到店核销' }}</td>
            <td class="code">{{ row.skuCode }}</td>
            <td class="catalog-price">¥{{ fenToYuan(row.listPriceFen) }}</td>
            <td class="catalog-grade-cell"><div v-if="grades.some((grade) => grade.enabled)" class="catalog-grade-list"><label v-for="grade in grades.filter((item) => item.enabled)" :key="grade.id"><span>{{ grade.name }}</span><input :value="inlineGradeDrafts[row.skuId]?.[grade.id] || ''" inputmode="decimal" :aria-label="`${row.skuCode} ${grade.name}等级价（元）`" placeholder="—" :disabled="!canPrice || savingGradeSku === row.skuId" @input="updateInlineGrade(row.skuId, grade.id, ($event.target as HTMLInputElement).value)" /></label></div><span v-else>暂无启用的等级</span><small v-if="inlineErrors[row.skuId]" class="catalog-inline-error" role="alert">{{ inlineErrors[row.skuId] }}</small></td>
            <td class="catalog-stock-pending">到库存管理查看<small>按 SKU 与仓库查询</small></td>
            <td><span :class="['badge', row.saleStatus === 'ON_SALE' ? 'badge-good' : 'badge-muted']">{{ row.saleStatus === 'ON_SALE' ? 'SKU 上架' : 'SKU 下架' }}</span><small class="catalog-product-meta">商品{{ row.productStatus === 'DRAFT' ? '草稿' : row.productStatus === 'ON_SALE' ? '在售' : '下架' }}</small></td>
            <td class="catalog-actions"><button v-if="canWrite" class="text-button" type="button" :disabled="productLoading || productMediaBusy || createBusy" @click="openProduct(row.productId)">编辑商品</button>
              <button v-if="canStatus" class="text-button" type="button" @click="openSku(row, 'status')">SKU 状态</button>
              <button v-if="canPrice" class="text-button" type="button" @click="openSku(row, 'price')">日常价</button>
              <button v-if="canPrice" class="text-button" type="button" :disabled="!inlineGradeChanged(row) || savingGradeSku === row.skuId" @click="saveInlineGrade(row)">{{ savingGradeSku === row.skuId ? '保存中…' : '保存等级价' }}</button>
              <button v-if="canUnit" class="text-button" type="button" @click="openSku(row, 'unit')">单位</button>
              <span v-if="!canWrite && !canStatus && !canPrice && !canUnit">仅查看</span></td>
          </tr></tbody></table>
        <p v-if="!pageData.rows.length" class="empty-state">没有匹配的 SKU。可调整筛选；有编辑权限时先创建商品草稿。</p>
      </div>
      <section v-if="batchAction" class="panel action-panel catalog-batch-preview" aria-labelledby="batch-title">
        <div class="panel-heading"><div><h3 id="batch-title">确认{{ batchAction === 'CATEGORY' ? '调整分类' : batchAction === 'ON_SALE' ? '批量上架 SKU' : '批量下架 SKU' }}</h3>
          <p v-if="batchAction === 'CATEGORY'">已选 {{ selectedRows.length }} 个 SKU，涉及 {{ selectedProducts.length }} 个商品。分类按商品调整，会影响这些商品的全部 SKU。</p>
          <p v-else>已选 {{ selectedRows.length }} 个 SKU，可执行 {{ eligibleRows.length }} 个，不能执行 {{ selectedRows.length - eligibleRows.length }} 个。首个 SKU 上架会同步上架商品；最后一个在售 SKU 下架会同步下架商品。缺少主图或分类停用的行会被拒绝。</p></div>
          <button class="text-button" type="button" :disabled="saving" @click="batchAction = null">取消</button></div>
        <div v-if="batchAction === 'CATEGORY'" class="catalog-batch-category-row"><label class="catalog-batch-category">目标二级分类<select v-model="batchCategoryId"><option value="">请选择</option><option v-for="item in activeLeafCategories" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
          <button class="secondary-button" type="button" :disabled="!batchCategoryId || previewLoading || saving" @click="requestCategoryPreview">{{ previewLoading ? '核对中…' : '核对影响范围' }}</button></div>
        <p v-if="batchAction === 'CATEGORY' && categoryPreview" class="catalog-impact-summary">服务端核对：所选涉及 {{ categoryPreview.productCount }} 个商品、共 {{ categoryPreview.skuCount }} 个 SKU，其中 {{ categoryPreview.otherSkuCount }} 个 SKU 在当前页所选之外。可调整 {{ categoryPreview.items.filter((item) => item.canChange).length }} 个商品。</p>
        <p v-else-if="batchAction === 'CATEGORY'" class="help-text">先选择目标分类并核对影响范围，再确认执行。</p>
        <ul class="catalog-batch-items"><li v-for="row in batchAction === 'CATEGORY' ? selectedProducts : selectedRows" :key="batchAction === 'CATEGORY' ? row.productId : row.skuId">
          <span>{{ batchAction === 'CATEGORY' ? `${row.productName}（${row.productNo}）` : row.skuCode }}</span>
          <small :class="batchReason(row) ? 'catalog-batch-skipped' : 'catalog-batch-ready'">{{ batchAction === 'CATEGORY' && categoryPreview ? `${categoryPreview.items.find((item) => item.productId === row.productId)?.totalSkuCount || 0} 个 SKU · ` : '' }}{{ batchReason(row) || '将执行' }}</small></li></ul>
        <button class="primary-button" type="button" :disabled="saving || (batchAction === 'CATEGORY' ? !categoryPreview || !categoryPreview.items.some((item) => item.canChange) : !eligibleRows.length)" @click="executeBatch">{{ saving ? '执行中…' : '确认执行' }}</button>
      </section>
      <section v-if="batchResult" class="panel action-panel catalog-batch-result" role="status" aria-label="批量操作结果">
        <div class="panel-heading"><h3>批量操作结果</h3><button class="text-button" type="button" @click="batchResult = null">关闭</button></div>
        <p>成功 {{ batchResult.successCount }} 项，失败 {{ batchResult.failedCount }} 项，未执行 {{ batchResult.skippedCount }} 项。{{ batchResult.action === 'CATEGORY' ? '以下按商品列出。' : '以下按 SKU 列出。' }}</p>
        <ul><li v-for="item in batchResult.items" :key="item.id"><strong>{{ item.label }}</strong>：{{ item.skipped ? `未执行：${item.message}` : item.success ? '成功' : `${item.message || item.code || '失败'}，请刷新后重试` }}</li></ul>
      </section>
      <div class="catalog-pagination"><span>共 {{ pageData.total }} 个 SKU · 第 {{ pageData.page }} 页</span>
        <div><button class="secondary-button" type="button" :disabled="pageData.page <= 1" @click="load(pageData.page - 1)">上一页</button>
          <button class="secondary-button" type="button" :disabled="!hasNext" @click="load(pageData.page + 1)">下一页</button></div></div>
    </template>
    <button v-if="error && !loading" class="text-button retry" type="button" @click="load(pageData.page)">重新加载</button>
    <p class="catalog-footnote">库存按 SKU 与仓库在“库存管理”查询；导出仅包含当前页勾选的 SKU。</p>
    </div>

    <section v-if="editorOpen" class="catalog-editor-workspace" aria-label="商品编辑工作区">
    <header class="catalog-workspace-heading"><div><button class="text-button catalog-back" type="button" :disabled="saving || productMediaBusy || createBusy" @click="closeEditors()">返回商品列表</button><h1 ref="workspaceHeading" tabindex="-1">{{ formOpen ? '新建商品' : editingSku ? '编辑 SKU' : '编辑商品' }}</h1><p>{{ editingProduct ? `${editingProduct.productNo} · ${editingProduct.status === 'DRAFT' ? '草稿' : editingProduct.status === 'ON_SALE' ? '在售' : '已下架'}` : editingSku ? editingSku.skuCode : '基础信息、媒体和规格分组管理' }}</p></div><span class="catalog-workspace-note">{{ saving || productMediaBusy || createBusy ? '操作进行中，请稍候' : hasUnsaved ? '有未保存的修改' : '退出前请确认各组资料已保存' }}</span></header>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <ProductCreateForm v-if="formOpen" :categories="categories" @created="created" @cancel="closeEditors()" @dirty-change="createDirty = $event" @busy-change="createBusy = $event" />
    <section v-if="productLoading || productLoadError" class="panel action-panel catalog-editor" aria-label="商品详情加载状态">
      <p v-if="productLoading" role="status">正在加载商品资料与 SKU…</p>
      <template v-else><p class="error notice" role="alert">{{ productLoadError }}</p>
        <button class="secondary-button" type="button" @click="openProduct(loadingProductId)">重试加载商品</button></template>
    </section>
    <form v-if="editingProduct" class="panel action-panel catalog-editor" novalidate @submit.prevent="saveProduct">
      <div class="panel-heading"><div><h2>基础信息与媒体</h2><p>修订 {{ editingProduct.productRevision }} · 本区单独保存，规格与 SKU 在下方核对。</p></div></div>
      <p v-if="productEditorError" class="error notice" role="alert">{{ productEditorError }}</p>
      <div class="form-grid"><label>商品名称<input ref="productNameInput" v-model="productName" maxlength="120" required :aria-invalid="!!productFieldErrors.name" @input="productFieldErrors.name = undefined" /><small v-if="productFieldErrors.name" class="catalog-field-error">{{ productFieldErrors.name }}</small></label>
        <label>二级分类<select ref="productCategorySelect" v-model="productCategoryId" required :aria-invalid="!!productFieldErrors.category" @change="productFieldErrors.category = undefined"><option value="" disabled>请选择</option><option v-for="item in categories.filter((row) => row.parentId && row.status === 'ACTIVE' && categories.some((parent) => parent.id === row.parentId && parent.status === 'ACTIVE'))" :key="item.id" :value="item.id">{{ item.name }}</option></select><small v-if="productFieldErrors.category" class="catalog-field-error">{{ productFieldErrors.category }}</small></label>
        <label>履约方式<select v-model="productFulfillment"><option value="SHIP">发货</option><option value="REDEEM">核销</option></select></label>
        <label v-if="productFulfillment === 'REDEEM'">核销截止日期（截止日当天有效）<input v-model="productRedeemValidUntil" type="date" /></label>
        </div>
      <p class="help-text">商品当前{{ editingProduct.status === 'DRAFT' ? '为草稿' : editingProduct.status === 'ON_SALE' ? '在售' : '已下架' }}。商品销售状态由 SKU 自动计算；请在列表的“SKU 状态”操作上架或下架。</p>
      <p v-if="!canStatus" class="help-text">当前账号没有调整 SKU 上下架状态的权限。</p>
      <ProductMediaEditor v-model:main-image="productMainImage" v-model:gallery-images="productGalleryImages" v-model:video="productVideo"
        :target-key="JSON.stringify([account.accountId, editingProduct.productId])" :can-upload="canUpload" :disabled="saving || !canWrite"
        @busy-change="productMediaBusy = $event" />
      <label>商品描述（HTML，服务端会过滤）<textarea v-model="productDescription" rows="4" /></label>
      <p class="help-text">SKU 上架后商品可在小程序展示；是否可购买仍由当前库存与报价决定。规格与 SKU 修改在下方独立核对并保存。</p>
      <button class="primary-button" type="submit" :disabled="saving || productMediaBusy || !canWrite">{{ saving ? '保存中…' : '保存商品资料' }}</button>
    </form>
    <section v-if="editingProduct" class="panel action-panel catalog-editor" aria-label="商品规格编辑">
      <ProductSpecEditor :product="editingProduct" :grades="grades" :can-edit="canEditSpecs" :basic-dirty="productDirty"
        @dirty-change="specDirty = $event" @saved="specsSaved" />
    </section>
    <form v-if="editingSku" class="panel action-panel catalog-editor" @submit.prevent="saveSku">
      <div class="panel-heading"><div><h3>编辑 SKU {{ editingSku.skuCode }}</h3><p>{{ editingSku.productName }} · 修订 {{ editingSku.skuRevision }}</p></div><button class="text-button" type="button" :disabled="productMediaBusy || createBusy || saving" @click="closeEditors()">取消</button></div>
      <label v-if="skuAction === 'status'">SKU 状态<select v-model="skuStatus"><option value="OFF_SALE">下架</option><option value="ON_SALE">上架</option></select></label>
      <label v-else-if="skuAction === 'price'">日常价（元）<input v-model="dailyPriceYuan" inputmode="decimal" required /></label>
      <template v-else-if="skuAction === 'prices'"><p>日常价 ¥{{ fenToYuan(editingSku.listPriceFen) }}。留空的已启用等级不设专属价；已停用等级的原价格会保留。</p>
        <div class="form-grid"><label v-for="grade in grades.filter((item) => item.enabled)" :key="grade.id">{{ grade.name }}价格（元）<input :value="gradeInputs[grade.id] || ''" inputmode="decimal" placeholder="留空沿用日常价" @input="updateGrade(grade.id, ($event.target as HTMLInputElement).value)" /></label></div></template>
      <div v-else class="form-grid"><label>基本单位<input v-model="baseUnit" maxlength="20" required /></label>
        <label>销售单位<input v-model="saleUnit" maxlength="20" required /></label>
        <label>换算比<input v-model.number="ratio" type="number" min="1" step="1" required /></label></div>
      <button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存 SKU' }}</button>
    </form>
    </section>
  </div>
</template>
