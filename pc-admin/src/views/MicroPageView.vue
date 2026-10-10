<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import PublicationHistory from '../shared/PublicationHistory.vue'
import { keepRollbackIntent, type RollbackResult } from '../shared/publication-history'
import { applyCurrentPublication, currentPublication, publicationIntent, matchesPublishResult, parsePublishIntent, persistPublishIntent, type PublishIntent } from '../shared/publication-sync'
import { api, ApiError, type Account, type Confirmation } from '../api'
import HomePagePreview from './pages/HomePagePreview.vue'
import EditorActions from './pages/EditorActions.vue'
import ComponentLibrary from './pages/ComponentLibrary.vue'
import PageReusePanel from './pages/PageReusePanel.vue'
import PageMetadataEditor from './pages/PageMetadataEditor.vue'
import ReleaseReportPanel from './pages/ReleaseReportPanel.vue'
import { useReleaseReport } from './pages/use-release-report'
import PageCopyPanel from './pages/PageCopyPanel.vue'
import { useEditorHistory, reorderComponent, duplicateComponent } from './pages/editor-history'
import { useEditorRuntime } from './pages/editor-runtime'
import './pages/editor-enhancements.css'
import PageComponentEditor from './pages/PageComponentEditor.vue'
import { applyUploadedAsset, componentNames, createComponent, normalizeConfig,
  type ComponentType, type HomePreview, type HomePublication, type MicroDraft, type MicroPageList,
  type MicroPageSummary, type PageComponent, type PageConfig, type PageMetadata, type UploadedAssetBinding } from './pages/types'
import './pages/home.css'
import { usePageTargets } from './pages/targets'
import './pages/micro.css'

const props = defineProps<{ account: Account }>()
const route = useRoute()
const router = useRouter()
const canEdit = computed(() => props.account.permissionCodes.includes('page.edit'))
const canPublish = computed(() => props.account.permissionCodes.includes('page.publish'))
const canUpload = computed(() => props.account.permissionCodes.includes('asset.upload'))
const list = ref<MicroPageSummary[]>([])
const listPage = ref(1)
const listTotal = ref(0)
const listLoading = ref(true)
const listError = ref('')
const listQuery = ref('')
const listTag = ref('')
const listFilters = ref({ q: '', tag: '' })
let listSequence = 0
const createName = ref('')
const creating = ref(false)
const draft = ref<MicroDraft | null>(null)
const editor = ref<PageConfig | null>(null)
const name = ref('')
const savedSnapshot = ref('')
const preview = ref<HomePreview | null>(null)
const previewSnapshot = ref('')
const selectedId = ref('theme')
const detailLoading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
const publishOpen = ref(false)
const publishPassword = ref('')
const publishIntent = ref<PublishIntent | null>(null)
const publicationRefresh = ref<{ base: string; objectId: string } | null>(null)
const publicationRecoveryBlocked = ref(false)
const { categories, products, pages: linkPages, warning: targetWarning, loading: targetsLoading, load: loadTargets } = usePageTargets()
const mobilePanel = ref('preview')
const { canUndo, canRedo, reset: resetHistory, undo, redo } = useEditorHistory(editor)
const { runtimeSchema, runtimeLoading, runtimeError, runtimeBlocked, loadRuntime } = useEditorRuntime(editor)
const draggingId = ref('')
function startDrag(event: DragEvent, id: string) {
  if (!canEdit.value || busy.value) return
  draggingId.value = id
  event.dataTransfer?.setData('text/plain', id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
function dropComponent(id: string) {
  if (canEdit.value && !busy.value && editor.value && draggingId.value) editor.value = reorderComponent(editor.value, draggingId.value, id)
  draggingId.value = ''
}
function copySelected() {
  if (!editor.value || !selected.value || !canEdit.value || busy.value) return
  const originalIndex = editor.value.components.findIndex(item => item.componentId === selectedId.value)
  const next = duplicateComponent(editor.value, selectedId.value)
  if (next === editor.value) return
  editor.value = next
  selectedId.value = next.components[originalIndex + 1]!.componentId
}
let detailSequence = 0

const currentPageId = computed(() => typeof route.query.pageId === 'string' ? route.query.pageId : '')
const dirty = computed(() => !!editor.value && !!draft.value &&
  (JSON.stringify(editor.value) !== savedSnapshot.value || name.value !== draft.value.name))
const { report, reportLoading, reportError, reportStale, refreshReport } = useReleaseReport(draft, dirty, busy, () => `/pages/${draft.value?.pageId || ''}`, saveDraft)
const previewReady = computed(() => !!preview.value && !dirty.value &&
  previewSnapshot.value === savedSnapshot.value && preview.value.revision === draft.value?.revision)
const checkedForPublish = computed(() => !!report.value && !reportStale.value && report.value.canPublish && report.value.runtimeSupported)
const publishGuidance = computed(() => {
  if (publicationRecoveryBlocked.value) return '发布结果尚未确认，请先核对原发布请求。'
  if (publishIntent.value) return '上次发布结果尚未确认，请恢复原发布请求，避免重复发布。'
  if (publicationRefresh.value) return '发布请求已完成，请核对当前线上状态。'
  if (dirty.value) return '还有未保存修改。先保存草稿，再检查并预览。'
  if (runtimeLoading.value) return '正在确认小程序是否支持此页面内容…'
  if (runtimeError.value) return '暂时无法确认小程序兼容状态，请重新检查。'
  if (runtimeBlocked.value) return '当前小程序暂不支持此页面内容。可保存草稿和预览，暂不能上线。请由部署负责人完成新版小程序验证。'
  if (reportError.value) return '发布检查失败，请重新检查并预览。'
  if (report.value && !reportStale.value && !report.value.canPublish) return '发布检查未通过，请展开检查详情处理问题。'
  if (draft.value?.publishedRevision === draft.value?.revision) return '当前草稿已上线。修改内容后可再次发布。'
  if (!previewReady.value || !checkedForPublish.value) return '草稿已保存。点击检查并预览，确认效果和上线条件。'
  return '检查已通过。核对画布效果后即可发布，发布前仍会确认权限和当前版本。'
})
const alreadyPublished = computed(() => !!draft.value && draft.value.publishedRevision === draft.value.revision)
const selected = computed(() => editor.value?.components.find((item) => item.componentId === selectedId.value) || null)
const publishedTargets = computed(() => linkPages.value.filter((item) =>
  item.publishedRevision !== null && item.pageId !== draft.value?.pageId)
  .map((item) => ({ pageId: item.pageId, name: item.name })))

function message(reason: unknown): string {
  if (reason instanceof ApiError && ['PAGE_ALREADY_PUBLISHED', 'STARTUP_ALREADY_PUBLISHED'].includes(reason.code)) return '该草稿已有发布版本，请通过版本历史切换线上内容，或修改草稿后再发布。'
  if (reason instanceof ApiError && reason.code === 'REVISION_CONFLICT') return '草稿已被其他人修改。当前输入已保留，请核对后重新读取。'
  return reason instanceof Error ? reason.message : '操作失败，请稍后重试。'
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onBeforeRouteLeave(async () => !dirty.value || await confirmAction('独立微页面有未保存修改，确定离开吗？'))
onMounted(() => { void loadList(); void loadTargets(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => { detailSequence++; window.removeEventListener('beforeunload', beforeUnload) })

async function loadList(page = 1) {
  const sequence = ++listSequence
  listLoading.value = true
  listError.value = ''
  const query = new URLSearchParams({ page: String(page), pageSize: '10' })
  if (listFilters.value.q) query.set('q', listFilters.value.q)
  if (listFilters.value.tag) query.set('tag', listFilters.value.tag)
  try {
    const result = await api<MicroPageList>(`/pages?${query}`)
    if (sequence !== listSequence) return
    const lastPage = Math.max(1, Math.ceil(result.total / result.pageSize))
    if (page > lastPage) { await loadList(lastPage); return }
    list.value = result.rows
    listPage.value = result.page
    listTotal.value = result.total
  } catch (reason) { if (sequence === listSequence) listError.value = message(reason) }
  finally { if (sequence === listSequence) listLoading.value = false }
}
function filterList() {
  const q = listQuery.value.trim(), tag = listTag.value.trim()
  if (q.length > 80 || tag.length > 20) { listError.value = '页面名称最多80字，业务标签最多20字。'; return }
  listFilters.value = { q, tag }
  list.value = []
  listTotal.value = 0
  void loadList(1)
}
function resetFilters() {
  listQuery.value = ''; listTag.value = ''
  filterList()
}

async function loadDetail(id: string) {
  const sequence = ++detailSequence
  draft.value = null
  editor.value = null
  detailLoading.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api<MicroDraft>(`/pages/${id}/draft`)
    if (sequence !== detailSequence) return
    const config = normalizeConfig(result.config)
    draft.value = result
    restorePublishIntent()
    editor.value = config
    resetHistory()
    name.value = result.name
    savedSnapshot.value = JSON.stringify(editor.value)
    preview.value = null
    previewSnapshot.value = ''
    selectedId.value = 'theme'
    mobilePanel.value = 'preview'
  } catch (reason) { if (sequence === detailSequence) error.value = message(reason) }
  finally { if (sequence === detailSequence) detailLoading.value = false }
}
watch(currentPageId, (id) => {
  if (id) void loadDetail(id)
  else { detailSequence++; draft.value = null; editor.value = null; preview.value = null }
}, { immediate: true })

/** Bind a pending choice to the editor and actor that displayed it, including browser history changes. */
function captureEditorContext() {
  const sequence = detailSequence, pageId = currentPageId.value, config = editor.value, currentDraft = draft.value
  const actor = props.account.accountId, permissions = JSON.stringify(props.account.permissionCodes)
  return () => sequence === detailSequence && pageId === currentPageId.value && config === editor.value
    && currentDraft === draft.value && actor === props.account.accountId
    && permissions === JSON.stringify(props.account.permissionCodes)
}
async function selectPage(id: string) {
  if (id === currentPageId.value || busy.value || creating.value) return
  const isCurrent = captureEditorContext()
  if (dirty.value && !await confirmAction('当前微页面有未保存修改，确定切换吗？')) return
  if (!isCurrent() || busy.value || creating.value) return
  await router.replace({ path: '/pages/micro', query: { pageId: id } })
}
async function createPage() {
  if (!canEdit.value || creating.value || busy.value) return
  const title = createName.value.trim()
  if (!title || title.length > 80) { listError.value = '请输入不超过 80 字的页面名称。'; return }
  const isCurrent = captureEditorContext()
  if (dirty.value && !await confirmAction('当前微页面有未保存修改，确定新建并切换吗？')) return
  if (!isCurrent() || !canEdit.value || creating.value || busy.value) return
  creating.value = true
  listError.value = ''
  try {
    const created = await api<MicroDraft>('/pages', { method: 'POST', body: JSON.stringify({ name: title }) })
    if (!isCurrent() || !canEdit.value) return
    createName.value = ''
    await loadList(1)
    await loadTargets()
    if (!isCurrent() || !canEdit.value) return
    await router.replace({ path: '/pages/micro', query: { pageId: created.pageId } })
    ElMessage.success('微页面草稿已创建')
  } catch (reason) { listError.value = message(reason) }
  finally { creating.value = false }
}
async function pageCopied(copied: MicroDraft) {
  notice.value = '独立副本草稿已创建，尚未发布。'
  await loadList(1)
  await loadTargets()
  await selectPage(copied.pageId)
}
function replaceEditor(config: PageConfig) {
  if (!editor.value || !canEdit.value || busy.value) return
  editor.value = normalizeConfig({ ...config, pageType: editor.value.pageType })
  selectedId.value = 'theme'
  notice.value = '编辑快照已应用，可撤销；请保存草稿。线上版本未变化。'
}
function appendCombination(items: PageComponent[]) {
  if (!editor.value || !canEdit.value || busy.value || editor.value.components.length + items.length > 40) return
  editor.value = normalizeConfig({ ...editor.value, components: [...editor.value.components, ...items] })
  selectedId.value = items[0]?.componentId || 'theme'
  notice.value = '组件组合已加入，可撤销；请补全素材与目标后保存。'
}
function updateMetadata(metadata: PageMetadata) {
  if (!editor.value || !canEdit.value || busy.value) return
  editor.value = normalizeConfig({ ...editor.value, schemaVersion: Math.max(3, editor.value.schemaVersion) as PageConfig['schemaVersion'], metadata })
}
function updateComponents(items: PageComponent[]) {
  if (!editor.value) return
  editor.value = normalizeConfig({ ...editor.value, components: items })
}
function addComponent(type: ComponentType) {
  if (!editor.value || !canEdit.value || busy.value || editor.value.components.length >= 40) return
  const component = createComponent(type, editor.value.components.length + 1)
  updateComponents([...editor.value.components, component])
  selectedId.value = component.componentId
  mobilePanel.value = 'settings'
}
function moveComponent(index: number, offset: number) {
  if (!editor.value) return
  const other = index + offset
  if (other < 0 || other >= editor.value.components.length) return
  const items = editor.value.components
  updateComponents(items.map((item, position) => position === index ? items[other]
    : position === other ? items[index] : item))
}
function updateComponent(value: PageComponent) {
  if (!editor.value) return
  updateComponents(editor.value.components.map((item) => item.componentId === value.componentId ? value : item))
}
function bindUploadedAsset(upload: UploadedAssetBinding) {
  if (!editor.value) return
  const next = applyUploadedAsset(editor.value, upload)
  if (next === editor.value) {
    notice.value = '图片已上传，但原组件或轮播位已变化；请重新选择图片。'
    return
  }
  editor.value = next
  notice.value = '图片已绑定到原组件，请保存草稿。'
}
async function removeComponent(id: string) {
  if (!editor.value || !canEdit.value || busy.value || creating.value) return
  const config = editor.value
  const isCurrent = captureEditorContext()
  if (!await confirmAction('移除此组件？未保存前可重新读取草稿恢复。')) return
  if (!isCurrent() || !canEdit.value || busy.value || creating.value) return
  updateComponents(config.components.filter((item) => item.componentId !== id))
  if (selectedId.value === id) selectedId.value = 'theme'
}
function updateTheme(key: keyof PageConfig['theme'], value: string) {
  if (editor.value) editor.value = { ...editor.value, theme: { ...editor.value.theme, [key]: value } }
}
async function saveDraft(): Promise<boolean> {
  if (!draft.value || !editor.value || !canEdit.value || busy.value) return false
  if (!dirty.value) return true
  const title = name.value.trim()
  if (!title || title.length > 80) { error.value = '页面名称须为 1—80 字。'; return false }
  busy.value = 'save'
  error.value = ''
  notice.value = ''
  const config = normalizeConfig(editor.value)
  const snapshot = JSON.stringify(config)
  try {
    const result = await api<MicroDraft>(`/pages/${draft.value.pageId}/draft`, {
      method: 'PUT', body: JSON.stringify({ name: title, expectedRevision: draft.value.revision, config }),
    })
    draft.value = result
    name.value = title
    savedSnapshot.value = snapshot
    notice.value = JSON.stringify(editor.value) === snapshot && name.value.trim() === title
      ? '草稿已保存，线上页面未变化。' : '草稿已保存；保存期间新增的修改仍待保存。'
    void loadList(listPage.value)
    return true
  } catch (reason) { error.value = message(reason); return false }
  finally { busy.value = '' }
}
async function checkAndPreview() {
  const pageId = draft.value?.pageId
  if (!pageId || busy.value) return
  await refreshReport()
  if (draft.value?.pageId !== pageId || dirty.value || reportError.value || !report.value || reportStale.value) return
  await showPreview()
}
async function showPreview() {
  if (!draft.value || busy.value) return
  if (dirty.value && !(await saveDraft())) return
  if (!draft.value || dirty.value) return
  const sequence = detailSequence, pageId = draft.value.pageId, revision = draft.value.revision, snapshot = savedSnapshot.value
  const matches = () => sequence === detailSequence && draft.value?.pageId === pageId && draft.value.revision === revision && savedSnapshot.value === snapshot && !dirty.value
  busy.value = 'preview'
  error.value = ''
  try {
    const result = await api<HomePreview>(`/pages/${draft.value.pageId}/preview`, {
      method: 'POST', body: JSON.stringify({ expectedRevision: revision }),
    })
    if (!matches()) return
    if (result.revision !== revision) throw new Error('预览内容已变化，请重新检查并预览。')
    preview.value = result
    previewSnapshot.value = savedSnapshot.value
    notice.value = `服务端预览已生成 · 修订 ${result.revision}。预览中的链接不会跳转。`
  } catch (reason) { if (matches()) { error.value = message(reason); preview.value = null } }
  finally { if (busy.value === 'preview') busy.value = '' }
}
function openPublish() {
  if (!draft.value || busy.value || publicationRefresh.value || publicationRecoveryBlocked.value) return
  if (publishIntent.value) {
    if (publishIntent.value.objectId !== draft.value.pageId) { error.value = '另一个页面的发布结果尚未确认，请返回原页面恢复原发布请求。'; return }
    error.value = ''; publishPassword.value = ''; publishOpen.value = true; return
  }
  if (runtimeBlocked.value || dirty.value || !previewReady.value || !checkedForPublish.value || alreadyPublished.value) return
  error.value = ''
  publishPassword.value = ''
  publishOpen.value = true
}
function publishStorageKey(base: string) { return `mall-publication-publish:${JSON.stringify([props.account.accountId, base])}` }
function restorePublishIntent() {
  if (!draft.value) return
  publicationRecoveryBlocked.value = false
  try {
    const raw = sessionStorage.getItem(publishStorageKey(`/pages/${draft.value.pageId}`))
    publishIntent.value = parsePublishIntent(raw, `/pages/${draft.value.pageId}`, draft.value.pageId)
    if (raw && !publishIntent.value) throw new Error('原发布恢复记录损坏。')
    if (publishIntent.value) notice.value = '原发布结果尚未确认，请按原请求恢复；原请求编号和双修订号已保留。'
  } catch { publicationRecoveryBlocked.value = true; error.value = '无法读取原发布恢复记录，已禁止新发布。请恢复浏览器会话存储并重新读取。' }
}
function savePublishIntent(intent: PublishIntent): boolean {
  try { return persistPublishIntent(sessionStorage, publishStorageKey(intent.base), intent) }
  catch { return false }
}
function forgetPublishIntent(base: string) {
  // If removal fails, re-entering safely recovers the completed original request.
  try { sessionStorage.removeItem(publishStorageKey(base)) } catch { /* The verified original request remains recoverable. */ }
}
async function syncPublication() {
  if (!draft.value || busy.value) return
  busy.value = 'publication-sync'
  error.value = ''
  try {
    const target = publicationRefresh.value || { base: `/pages/${draft.value.pageId}`, objectId: draft.value.pageId }
    const current = await currentPublication(api, target.base)
    if (draft.value && draft.value.pageId === target.objectId) draft.value = applyCurrentPublication(draft.value, current)
    publicationRefresh.value = null
    forgetPublishIntent(target.base)
    notice.value = `发布请求已完成。当前线上为${current.publishedRevision === null ? '尚未发布' : `修订 ${current.publishedRevision}`}；草稿与未保存的编辑保留。`
    void loadList(listPage.value)
    void loadTargets()
  } catch (reason) {
    error.value = `发布请求已完成，但当前线上状态读取失败。请重新读取线上状态后再发布。${message(reason)}`
  } finally { busy.value = '' }
}
async function publish() {
  if (!draft.value || !publishPassword.value || busy.value || publicationRefresh.value || publicationRecoveryBlocked.value) return
  if (!publishIntent.value && (runtimeBlocked.value || dirty.value || !previewReady.value || !checkedForPublish.value || alreadyPublished.value)) return
  if (publishIntent.value && publishIntent.value.objectId !== draft.value.pageId) { error.value = '另一个页面的发布结果尚未确认，请返回原页面恢复原发布请求。'; return }
  const recovering = !!publishIntent.value
  const intent = publishIntent.value || publicationIntent(`/pages/${draft.value.pageId}`, draft.value.pageId, draft.value, crypto.randomUUID())
  if (!savePublishIntent(intent)) { error.value = '无法保存原发布恢复记录，发布未提交。请启用会话存储后重试。'; return }
  publishIntent.value = intent
  busy.value = 'publish'
  error.value = ''
  let submitted = false
  let accepted = false
  try {
    const { confirmationToken } = await api<Confirmation>('/auth/confirm', {
      method: 'POST', body: JSON.stringify({ action: 'page.publish', password: publishPassword.value, objectId: intent.objectId, revision: intent.body.expectedRevision }),
    })
    submitted = true
    const result = await api<HomePublication>(`${intent.base}/publish`, {
      method: 'POST', headers: { 'X-Action-Confirmation': confirmationToken, 'Idempotency-Key': intent.key },
      body: JSON.stringify(intent.body),
    })
    if (!matchesPublishResult(result, intent.body)) throw new Error('服务端发布结果尚未确认，请按原请求恢复。')
    accepted = true
    publishIntent.value = null
    publicationRefresh.value = { base: intent.base, objectId: intent.objectId }
    publishOpen.value = false
    publishPassword.value = ''
    notice.value = '发布请求已完成，正在读取当前线上状态。'
    // An idempotent receipt describes the original request, not the current publication pointer.
    const current = await currentPublication(api, intent.base)
    if (draft.value && draft.value.pageId === intent.objectId) draft.value = applyCurrentPublication(draft.value, current)
    publicationRefresh.value = null
    forgetPublishIntent(intent.base)
    notice.value = `发布请求已完成。当前线上为${current.publishedRevision === null ? '尚未发布' : `修订 ${current.publishedRevision}`}；草稿与未保存的编辑保留。`
    void loadList(listPage.value)
    void loadTargets()
    ElMessage.success('发布请求已完成，线上状态已同步')
  } catch (reason) {
    if (accepted) {
      error.value = `发布请求已完成，但当前线上状态读取失败。请重新读取线上状态后再发布。${message(reason)}`
    } else {
      if (!recovering && (!submitted || !keepRollbackIntent(reason instanceof ApiError ? reason.status : undefined, false))) { publishIntent.value = null; forgetPublishIntent(intent.base) }
      error.value = `${message(reason)}${publishIntent.value ? ' 发布结果未确认，请按原请求恢复；原请求编号和双修订号保持不变。' : ''}`
    }
    publishPassword.value = ''
  } finally { busy.value = '' }
}
async function reloadDraft() {
  if (!draft.value || busy.value || creating.value) return
  const id = draft.value.pageId
  const isCurrent = captureEditorContext()
  if (dirty.value && !await confirmAction('放弃未保存的修改并重新读取草稿？')) return
  if (isCurrent() && !busy.value && !creating.value) void loadDetail(id)
}
function rolledBack(result: RollbackResult) {
  if (!draft.value) return
  draft.value = { ...draft.value, publishedRevision: result.revision, publishedVersionId: result.versionId, publicationRevision: result.publicationRevision }
  notice.value = `线上已切换到修订 ${result.revision}，当前草稿和未保存的编辑保持原样。`
  void loadList(listPage.value)
}
</script>

<template>
  <section class="page-content home-editor-page micro-editor-page">
    <header class="page-heading home-page-heading">
      <div><h1>独立微页面</h1><p>创建页面、配置组件、预览草稿，再发布可供首页与其他微页面引用的版本。</p></div>
      <RouterLink class="text-link" to="/pages/home">返回首页装修</RouterLink>
    </header>

    <details :key="currentPageId || 'list'" class="panel micro-page-list" :open="!currentPageId" aria-labelledby="micro-list-title">
      <summary id="micro-list-title">{{ draft ? `当前页面：${draft.name}` : '页面列表' }} <span>切换页面 / 新建 / 查询</span></summary>
      <div class="micro-list-heading"><div><h2>页面列表</h2><p>草稿不会改变已发布内容。可查看发布历史并回退线上版本。</p></div>
        <form v-if="canEdit" class="micro-create" @submit.prevent="createPage"><label>新页面名称<input v-model="createName" maxlength="80" placeholder="例如 品牌故事" :disabled="creating || Boolean(busy)" /></label>
          <button type="submit" class="primary-button" :disabled="creating || Boolean(busy)">{{ creating ? '创建中…' : '创建微页面' }}</button></form></div>
      <form class="micro-list-filters" @submit.prevent="filterList"><label>搜索页面名称<input v-model="listQuery" maxlength="80" placeholder="输入页面名称" /></label><label>筛选业务标签<input v-model="listTag" maxlength="20" placeholder="精确标签，例如 活动" /></label><button type="submit" class="secondary-button" :disabled="listLoading">查询页面</button><button type="button" class="text-button" :disabled="listLoading" @click="resetFilters">重置筛选</button></form>
      <p v-if="listError" class="error notice" role="alert">{{ listError }} <button type="button" class="text-button" @click="loadList(listPage)">重试读取列表</button></p>
      <p v-if="listLoading" class="loading-inline" role="status">正在读取微页面…</p>
      <p v-else-if="!list.length" class="micro-empty">{{ listFilters.q || listFilters.tag ? '未找到符合条件的微页面，请调整筛选。' : '还没有独立微页面。创建第一张草稿后，可添加公告、图片或其他组件。' }}</p>
      <div v-else class="micro-page-rows">
        <button v-for="item in list" :key="item.pageId" type="button" class="micro-page-row" :class="{ selected: currentPageId === item.pageId }" :disabled="Boolean(busy) || creating" @click="selectPage(item.pageId)">
          <div class="micro-page-identity"><strong>{{ item.name }}</strong><span v-if="item.tags?.length" class="micro-row-tags"><span v-for="tag in item.tags" :key="tag" class="badge badge-muted">{{ tag }}</span></span></div><span>草稿修订 {{ item.revision }}</span>
          <span :class="item.publishedRevision ? 'badge badge-good' : 'badge badge-muted'">{{ item.publishedRevision ? `线上修订 ${item.publishedRevision}` : '未发布' }}</span>
        </button>
      </div>
      <div v-if="listTotal > 10" class="micro-list-pagination"><span>共 {{ listTotal }} 张 · 第 {{ listPage }} 页</span>
        <button type="button" class="secondary-button" :disabled="listPage <= 1 || listLoading" @click="loadList(listPage - 1)">上一页</button>
        <button type="button" class="secondary-button" :disabled="listPage * 10 >= listTotal || listLoading" @click="loadList(listPage + 1)">下一页</button></div>
    </details>

    <p v-if="detailLoading" class="loading-inline" role="status">正在读取微页面草稿…</p>
    <p v-else-if="currentPageId && !draft && error" class="error notice" role="alert">{{ error }} <button type="button" class="text-button" @click="loadDetail(currentPageId)">重新加载</button></p>
    <p v-else-if="!currentPageId" class="micro-empty">从列表选择页面后开始编辑。</p>
    <template v-else-if="draft && editor">
      <div class="home-version-bar"><strong>{{ draft.name }}</strong>
        <span :class="draft.publishedRevision ? 'badge badge-good' : 'badge badge-muted'">{{ draft.publishedRevision ? `线上修订 ${draft.publishedRevision}` : '尚未发布' }}</span>
        <span>草稿修订 {{ draft.revision }}</span><span v-if="dirty" class="home-unsaved">● 有未保存修改</span><span v-else>草稿已保存</span>
        <button type="button" class="text-button home-reload" :disabled="Boolean(busy)" @click="reloadDraft">重新读取</button></div>
      <div class="micro-editor-actions"><PublicationHistory :account="account" :base="`/pages/${draft.pageId}`" :object-id="draft.pageId" :draft-revision="draft.revision" :disabled="Boolean(busy)" @rolled-back="rolledBack" /><PageCopyPanel v-if="canEdit" :account-id="account.accountId" :page-id="draft.pageId" :name="draft.name" :revision="draft.revision" :published-revision="draft.publishedRevision" :disabled="Boolean(busy)" @copied="pageCopied" /><label>页面名称<input v-model="name" maxlength="80" :disabled="!canEdit || Boolean(busy)" /></label>
        <button type="button" class="secondary-button" :disabled="!canEdit || !dirty || Boolean(busy)" @click="saveDraft">{{ busy === 'save' ? '保存中…' : '保存草稿' }}</button>
        <button type="button" class="secondary-button" :disabled="Boolean(busy)" @click="checkAndPreview">{{ busy === 'preview' || reportLoading ? '检查预览中…' : '检查并预览' }}</button>
        <button v-if="canPublish" type="button" class="primary-button" :disabled="Boolean(busy) || publicationRecoveryBlocked || Boolean(publicationRefresh) || (!publishIntent && (runtimeBlocked || dirty || !previewReady || !checkedForPublish || alreadyPublished))" @click="openPublish">{{ publishIntent ? '恢复原发布请求' : publicationRefresh ? '线上状态待同步' : alreadyPublished ? '当前修订已发布' : '发布微页面' }}</button></div>
      <p class="micro-workflow-guide" role="status"><strong>保存草稿 → 检查并预览 → 发布</strong><span>{{ publishGuidance }}<template v-if="dirty"> 检查并预览会先保存当前修改。</template></span></p>
      <p v-if="error" class="error notice" role="alert">{{ error }}</p>
      <p v-if="notice" class="home-success" role="status">{{ notice }}</p>
    <p v-if="publicationRefresh" role="status">发布请求已完成，当前线上状态待同步。<button type="button" class="text-button" :disabled="Boolean(busy)" @click="syncPublication">重新读取线上状态</button></p>
      <EditorActions :disabled="!canEdit || Boolean(busy)" :can-undo="canUndo" :can-redo="canRedo" :can-copy="Boolean(selected)" :count="editor.components.length" @undo="undo" @redo="redo" @copy="copySelected" @theme="selectedId = 'theme'; mobilePanel = 'settings'" />
      <p v-if="runtimeBlocked" class="micro-runtime-actions"><button type="button" class="text-button" :disabled="runtimeLoading || Boolean(busy)" @click="loadRuntime">重新检查兼容状态</button><RouterLink v-if="account.permissionCodes.includes('code.version.read')" to="/store/code-versions">查看小程序代码版本</RouterLink></p>
      <p v-if="!editor.components.some(item => item.visible)" class="help-text" role="status">当前没有可见内容，发布后顾客将看到空白页。请添加组件，或确认空白用途。</p>
      <div class="micro-mobile-panels" aria-label="编辑区域"><button v-for="tab in ([['preview', '预览'], ['components', '组件'], ['settings', '设置']] as const)" :key="tab[0]" type="button" :aria-pressed="mobilePanel === tab[0]" @click="mobilePanel = tab[0]">{{ tab[1] }}</button></div>
      <div class="home-editor-grid" :data-mobile-panel="mobilePanel">
        <aside class="home-toolbox panel" aria-label="组件与顺序"><h2>可用组件</h2><p>添加后可调整顺序、显隐和内容。</p>
          <ComponentLibrary :disabled="!canEdit || Boolean(busy) || editor.components.length >= 40" @add="addComponent" />
          <div class="home-toolbox-title"><h3>页面组件</h3><small>{{ editor.components.length }} 项</small></div>
          <p v-if="!editor.components.length" class="help-text">还没有组件。请从上方添加。</p>
          <ol v-else class="home-component-list"><li v-for="(item, index) in editor.components" :key="item.componentId" :class="{ selected: selectedId === item.componentId }" :draggable="canEdit && !busy" @dragstart="startDrag($event, item.componentId)" @dragover.prevent @drop.prevent="dropComponent(item.componentId)" @dragend="draggingId = ''">
            <button type="button" class="home-component-select" :aria-current="selectedId === item.componentId ? 'true' : undefined" @click="selectedId = item.componentId; mobilePanel = 'settings'"><strong>{{ componentNames[item.type] }}</strong><small>{{ item.visible ? '显示中' : '已隐藏' }}</small></button>
            <div v-if="canEdit" class="home-row-tools"><button type="button" :disabled="index === 0 || Boolean(busy)" :aria-label="`上移${componentNames[item.type]}`" @click="moveComponent(index, -1)">↑</button><button type="button" :disabled="index === editor.components.length - 1 || Boolean(busy)" :aria-label="`下移${componentNames[item.type]}`" @click="moveComponent(index, 1)">↓</button></div>
          </li></ol></aside>
        <section class="home-preview-panel panel" aria-labelledby="micro-preview-title"><div class="home-panel-title"><div><h2 id="micro-preview-title">即时效果</h2><p>点击画布选中组件，编辑结果即时显示；链接不会跳转。</p></div><span :class="previewReady ? 'badge badge-good' : 'badge badge-muted'">{{ previewReady ? '服务端校验通过' : '待服务端校验' }}</span></div>
          <HomePagePreview :config="editor" :stale="false" :page-name="name" :interactive="true" :selected-id="selectedId" @select="selectedId = $event; mobilePanel = 'settings'" /></section>
        <section class="home-settings panel" aria-labelledby="micro-settings-title"><div class="home-panel-title"><div><h2 id="micro-settings-title">{{ selected ? `组件设置 · ${componentNames[selected.type]}` : '页面设置 · 主题' }}</h2><p>修改草稿不会改变线上版本。</p></div></div>
          <template v-if="selected"><label v-if="canEdit" class="home-toggle"><input type="checkbox" :checked="selected.visible" :disabled="Boolean(busy)" @change="updateComponent({ ...selected, visible: ($event.target as HTMLInputElement).checked })">显示此组件</label>
            <span v-else class="badge badge-muted">{{ selected.visible ? '显示中' : '已隐藏' }}</span>
            <fieldset :disabled="!canEdit || Boolean(busy)" class="home-settings-fieldset"><PageComponentEditor :disabled="!canEdit || Boolean(busy)" :component="selected" :can-upload="canUpload" :categories="categories" :products="products" :pages="publishedTargets" @change="updateComponent" @uploaded="bindUploadedAsset" /></fieldset>
            <p v-if="targetsLoading" class="help-text" role="status">正在读取目标列表…</p>
            <p v-else-if="targetWarning" class="help-text" role="alert">{{ targetWarning }} <button type="button" class="text-button" @click="loadTargets">重试读取目标</button></p>
            <button v-if="canEdit" type="button" class="text-button danger home-remove" :disabled="Boolean(busy)" @click="removeComponent(selected.componentId)">移除组件</button></template>
          <div v-else class="home-theme-fields"><label v-for="field in ([['pageBackgroundColor', '页面背景色'], ['headerBackgroundColor', '顶部区域底色'], ['brandTextColor', '顶部文字色']] as const)" :key="field[0]">{{ field[1] }}<span class="home-color-control"><input type="color" :value="editor.theme[field[0]]" :disabled="!canEdit || Boolean(busy)" :aria-label="field[1]" @input="updateTheme(field[0], ($event.target as HTMLInputElement).value)"><input :value="editor.theme[field[0]]" :aria-label="`${field[1]}十六进制值`" maxlength="7" :disabled="!canEdit || Boolean(busy)" @change="updateTheme(field[0], ($event.target as HTMLInputElement).value.trim())"></span></label>
            <p class="help-text">主题色仅作用于此页面；发布前请在预览中检查文字可读性。</p></div>
        </section>
      </div>
      <PageReusePanel :key="draft.pageId" :config="editor" :disabled="!canEdit || Boolean(busy)" @replace="replaceEditor" @append="appendCombination" />
      <section class="panel page-metadata-panel" aria-label="分享与业务标签"><PageMetadataEditor :key="draft.pageId" :metadata="editor.metadata" :disabled="!canEdit || Boolean(busy)" @change="updateMetadata" /></section>
      <ReleaseReportPanel :report="report" :stale="reportStale" :loading="reportLoading" :error="reportError" :disabled="Boolean(busy) || detailLoading" @refresh="refreshReport" />
    </template>
    <el-dialog v-model="publishOpen" title="确认发布独立微页面" width="min(480px, 92vw)" @closed="publishPassword = ''">
      <p>将“{{ draft?.name }}”的草稿修订 {{ draft?.revision }} 发布到小程序。发布后，站内链接才可指向这个页面。</p>
      <label class="home-publish-password">当前账号密码<el-input v-model="publishPassword" type="password" autocomplete="current-password" show-password placeholder="输入密码确认发布" @keyup.enter="publish" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <template #footer><button type="button" class="secondary-button" :disabled="busy === 'publish'" @click="publishOpen = false">取消</button><button type="button" class="primary-button" :disabled="!publishPassword || busy === 'publish'" @click="publish">{{ busy === 'publish' ? '发布中…' : '确认发布' }}</button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.micro-page-identity{display:grid;gap:6px;min-width:0}.micro-row-tags{display:flex;flex-wrap:wrap;gap:4px}.micro-list-filters{display:grid;grid-template-columns:minmax(160px,1fr) minmax(160px,1fr) auto auto;align-items:end;gap:12px;margin:16px 0}.micro-list-filters label{display:grid;gap:6px;font-size:13px}.micro-list-filters input{min-width:0;min-height:40px;padding:8px 12px;border:1px solid var(--mall-color-border);border-radius:8px}.micro-list-filters button{min-height:40px}.page-metadata-panel{padding:20px;margin:16px 0}@media(max-width:700px){.micro-page-identity{grid-column:1/-1}.micro-list-filters{grid-template-columns:1fr 1fr}.micro-list-filters label{grid-column:1/-1}.page-metadata-panel{padding:16px}}
</style>
