<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage } from 'element-plus'
import PublicationHistory from '../shared/PublicationHistory.vue'
import { keepRollbackIntent, type RollbackResult } from '../shared/publication-history'
import { applyCurrentPublication, currentPublication, publicationIntent, matchesPublishResult, parsePublishIntent, persistPublishIntent, type PublishIntent } from '../shared/publication-sync'
import { api, ApiError, type Account, type Confirmation } from '../api'
import HomePagePreview from './pages/HomePagePreview.vue'
import PageComponentEditor from './pages/PageComponentEditor.vue'
import { applyUploadedAsset, componentNames, createComponent, normalizeConfig, type ComponentType, type HomeDraft, type HomePreview, type HomePublication, type MicroPageList, type PageComponent, type PageConfig, type UploadedAssetBinding } from './pages/types'
import './pages/home.css'

const props = defineProps<{ account: Account }>()
const canEdit = computed(() => props.account.permissionCodes.includes('page.edit'))
const canPublish = computed(() => props.account.permissionCodes.includes('page.publish'))
const canUpload = computed(() => props.account.permissionCodes.includes('asset.upload'))
const loading = ref(true)
const busy = ref('')
const error = ref('')
const notice = ref('')
const draft = ref<HomeDraft | null>(null)
const editor = ref<PageConfig | null>(null)
const savedSnapshot = ref('')
const preview = ref<HomePreview | null>(null)
const previewSnapshot = ref('')
const selectedId = ref('theme')
const publishOpen = ref(false)
const publishPassword = ref('')
const publishIntent = ref<PublishIntent | null>(null)
const publicationRefresh = ref<{ base: string; objectId: string } | null>(null)
const publicationRecoveryBlocked = ref(false)
const categories = ref<{ id: string; name: string }[]>([])
const products = ref<{ productId: string; name: string }[]>([])
const pages = ref<{ pageId: string; name: string }[]>([])
const targetWarning = ref('')
const componentTypes: ComponentType[] = ['CAROUSEL', 'IMAGE_HOTZONE', 'DIVIDER', 'SEARCH', 'NOTICE', 'FILING']

const dirty = computed(() => !!editor.value && savedSnapshot.value !== JSON.stringify(editor.value))
const previewStale = computed(() => !!preview.value && (dirty.value || previewSnapshot.value !== savedSnapshot.value))
const previewReady = computed(() => !!preview.value && !previewStale.value && preview.value.revision === draft.value?.revision)
const alreadyPublished = computed(() => !!draft.value && draft.value.publishedRevision === draft.value.revision)
const selected = computed(() => editor.value?.components.find((item) => item.componentId === selectedId.value) || null)
const publicationLabel = computed(() => {
  if (!draft.value?.publishedRevision) return '尚未发布'
  return `线上版本 · 修订 ${draft.value.publishedRevision}`
})

function message(reason: unknown) {
  if (reason instanceof ApiError && ['PAGE_ALREADY_PUBLISHED', 'STARTUP_ALREADY_PUBLISHED'].includes(reason.code)) return '该草稿已有发布版本，请通过版本历史切换线上内容，或修改草稿后再发布。'
  if (reason instanceof ApiError && reason.status === 409) return '草稿已被其他操作修改，请刷新后再编辑。'
  return reason instanceof Error ? reason.message : '操作失败，请稍后重试。'
}
async function loadDraft() {
  loading.value = true
  error.value = ''
  try {
    const result = await api<HomeDraft>('/pages/home/draft')
    draft.value = result
    restorePublishIntent()
    editor.value = normalizeConfig(result.config)
    savedSnapshot.value = JSON.stringify(editor.value)
    preview.value = null
    previewSnapshot.value = ''
    selectedId.value = 'theme'
  } catch (reason) { error.value = message(reason) }
  finally { loading.value = false }
}
async function loadTargets() {
  const [category, product, page] = await Promise.allSettled([
    fetch('/api/v1/app/categories').then((result) => result.json()),
    fetch('/api/v1/app/products?page=1&pageSize=100').then((result) => result.json()),
    api<MicroPageList>('/pages?page=1&pageSize=50'),
  ])
  if (category.status === 'fulfilled' && category.value.success && Array.isArray(category.value.data)) categories.value = category.value.data
  if (product.status === 'fulfilled' && product.value.success && Array.isArray(product.value.data?.rows)) products.value = product.value.data.rows
  if (page.status === 'fulfilled') pages.value = page.value.rows.filter((item) => item.publishedRevision !== null)
    .map((item) => ({ pageId: item.pageId, name: item.name }))
  if ([category, product, page].some((result) => result.status === 'rejected')) targetWarning.value = '部分目标列表暂不可用，可输入已知的分类、商品或微页面 ID。'
}
onMounted(() => { void loadDraft(); void loadTargets(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onBeforeRouteLeave(() => !dirty.value || window.confirm('首页草稿尚未保存，确定离开吗？'))

function updateTheme(key: keyof PageConfig['theme'], value: string) {
  if (!editor.value) return
  editor.value = { ...editor.value, theme: { ...editor.value.theme, [key]: value } }
}
function updateComponents(items: PageComponent[]) {
  if (!editor.value) return
  editor.value = normalizeConfig({ ...editor.value, components: items })
}
function updateComponent(value: PageComponent) {
  if (!editor.value) return
  updateComponents(editor.value.components.map((item) => item.componentId === value.componentId ? value : item))
}
function bindUploadedAsset(upload: UploadedAssetBinding) {
  if (!editor.value) return
  const updated = applyUploadedAsset(editor.value, upload)
  if (updated === editor.value) {
    notice.value = '图片上传完成，但原组件或轮播位已变化，图片未绑定。请重新选择图片。'
    return
  }
  editor.value = updated
  notice.value = '图片已上传并绑定到原组件，记得保存草稿。'
}
function addComponent(type: ComponentType) {
  if (!editor.value) return
  const item = createComponent(type, editor.value.components.length + 1)
  updateComponents([...editor.value.components, item])
  selectedId.value = item.componentId
}
function moveComponent(index: number, offset: number) {
  if (!editor.value) return
  const destination = index + offset
  if (destination < 0 || destination >= editor.value.components.length) return
  const items = [...editor.value.components]
  const current = items[index]
  items[index] = items[destination]
  items[destination] = current
  updateComponents(items)
}
function removeComponent(id: string) {
  if (!editor.value) return
  updateComponents(editor.value.components.filter((item) => item.componentId !== id))
  if (selectedId.value === id) selectedId.value = 'theme'
}

async function saveDraft(): Promise<boolean> {
  if (!draft.value || !editor.value || !canEdit.value) return false
  if (!dirty.value) return true
  if (busy.value) return false
  busy.value = 'save'
  error.value = ''
  notice.value = ''
  const config = normalizeConfig(editor.value)
  const snapshot = JSON.stringify(config)
  try {
    const result = await api<HomeDraft>('/pages/home/draft', {
      method: 'PUT', body: JSON.stringify({ expectedRevision: draft.value.revision, config }),
    })
    draft.value = result
    savedSnapshot.value = snapshot
    notice.value = JSON.stringify(editor.value) === snapshot ? '草稿已保存，线上页面未变化。' : '草稿已保存；保存期间新增的修改仍待保存。'
    return true
  } catch (reason) { error.value = message(reason); return false }
  finally { busy.value = '' }
}
async function showPreview() {
  if (!draft.value || busy.value) return
  if (dirty.value && !(await saveDraft())) return
  if (!draft.value) return
  busy.value = 'preview'
  error.value = ''
  try {
    const result = await api<HomePreview>('/pages/home/preview', {
      method: 'POST', body: JSON.stringify({ expectedRevision: draft.value.revision }),
    })
    preview.value = result
    previewSnapshot.value = savedSnapshot.value
    notice.value = `服务端预览已生成 · 草稿修订 ${result.revision}。链接在此预览中不执行跳转。`
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = '' }
}
function openPublish() {
  if (!draft.value || busy.value || publicationRefresh.value || publicationRecoveryBlocked.value) return
  if (publishIntent.value) {
    if (publishIntent.value.objectId !== draft.value.pageId) { error.value = '另一个页面的发布结果尚未确认，请返回原页面恢复原发布请求。'; return }
    error.value = ''; publishPassword.value = ''; publishOpen.value = true; return
  }
  if (dirty.value) { error.value = '请先保存草稿，再发布当前修订。'; return }
  if (!previewReady.value) { error.value = '请先生成当前草稿的服务端预览，再发布。'; return }
  if (alreadyPublished.value) { error.value = '当前草稿修订已发布。'; return }
  error.value = ''
  publishPassword.value = ''
  publishOpen.value = true
}
function closePublish() { publishOpen.value = false; publishPassword.value = '' }
function reloadDraft() {
  if (!dirty.value || window.confirm('放弃未保存的修改并重新读取草稿？')) void loadDraft()
}
function publishStorageKey(base: string) { return `mall-publication-publish:${JSON.stringify([props.account.accountId, base])}` }
function restorePublishIntent() {
  if (!draft.value) return
  publicationRecoveryBlocked.value = false
  try {
    const raw = sessionStorage.getItem(publishStorageKey('/pages/home'))
    publishIntent.value = parsePublishIntent(raw, '/pages/home', draft.value.pageId)
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
    const target = publicationRefresh.value || { base: '/pages/home', objectId: draft.value.pageId }
    const current = await currentPublication(api, target.base)
    if (draft.value && draft.value.pageId === target.objectId) draft.value = applyCurrentPublication(draft.value, current)
    publicationRefresh.value = null
    forgetPublishIntent(target.base)
    notice.value = `发布请求已完成。当前线上为${current.publishedRevision === null ? '尚未发布' : `修订 ${current.publishedRevision}`}；草稿与未保存的编辑保留。`
  } catch (reason) {
    error.value = `发布请求已完成，但当前线上状态读取失败。请重新读取线上状态后再发布。${message(reason)}`
  } finally { busy.value = '' }
}
async function publish() {
  if (!draft.value || !publishPassword.value || busy.value || publicationRefresh.value || publicationRecoveryBlocked.value) return
  if (!publishIntent.value && (dirty.value || !previewReady.value || alreadyPublished.value)) return
  if (publishIntent.value && publishIntent.value.objectId !== draft.value.pageId) { error.value = '另一个页面的发布结果尚未确认，请返回原页面恢复原发布请求。'; return }
  const recovering = !!publishIntent.value
  const intent = publishIntent.value || publicationIntent('/pages/home', draft.value.pageId, draft.value, crypto.randomUUID())
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
function rolledBack(result: RollbackResult) {
  if (!draft.value) return
  draft.value = { ...draft.value, publishedRevision: result.revision, publishedVersionId: result.versionId, publicationRevision: result.publicationRevision }
  notice.value = `线上已切换到修订 ${result.revision}，当前草稿和未保存的编辑保持原样。`
}
</script>

<template>
  <section class="page-content home-editor-page">
    <header class="page-heading home-page-heading">
      <div><h1>首页微页面装修</h1><p>编辑草稿、服务端预览，再将验证通过的版本发布到小程序。</p></div>
      <div class="home-heading-actions"><PublicationHistory v-if="draft" :account="account" base="/pages/home" :object-id="draft.pageId" :draft-revision="draft.revision" :disabled="loading || Boolean(busy)" @rolled-back="rolledBack" />
        <button type="button" class="secondary-button" :disabled="loading || Boolean(busy) || !canEdit || !dirty" @click="saveDraft">{{ busy === 'save' ? '保存中…' : '保存草稿' }}</button>
        <button type="button" class="secondary-button" :disabled="loading || Boolean(busy)" @click="showPreview">{{ busy === 'preview' ? '生成中…' : '预览' }}</button>
        <button v-if="canPublish" type="button" class="primary-button" :disabled="loading || Boolean(busy) || publicationRecoveryBlocked || Boolean(publicationRefresh) || (!publishIntent && (dirty || !previewReady || alreadyPublished))" :title="alreadyPublished ? '当前草稿已发布' : !previewReady ? '请先预览当前草稿' : dirty ? '请先保存草稿' : ''" @click="openPublish">{{ publishIntent ? '恢复原发布请求' : publicationRefresh ? '线上状态待同步' : alreadyPublished ? '当前修订已发布' : '发布首页' }}</button>
      </div>
    </header>
    <div v-if="error" class="notice home-error" role="alert">{{ error }} <button v-if="!draft" type="button" class="text-button" @click="loadDraft">重新加载</button></div>
    <p v-if="notice" class="home-success" role="status">{{ notice }}</p>
    <p v-if="publicationRefresh" role="status">发布请求已完成，当前线上状态待同步。<button type="button" class="text-button" :disabled="Boolean(busy)" @click="syncPublication">重新读取线上状态</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在读取首页草稿…</p>
    <template v-else-if="draft && editor">
      <div class="home-version-bar">
        <span :class="draft.publishedRevision ? 'badge badge-good' : 'badge badge-muted'">{{ publicationLabel }}</span>
        <span>草稿修订 {{ draft.revision }}</span>
        <span v-if="dirty" class="home-unsaved">● 有未保存修改</span>
        <span v-else>草稿已保存</span>
        <button type="button" class="text-button home-reload" :disabled="Boolean(busy)" @click="reloadDraft">重新读取</button>
      </div>
      <div class="home-editor-grid">
        <aside class="home-toolbox panel" aria-label="组件与顺序">
          <h2>可用组件</h2><p>添加后设置内容、顺序和显示状态。</p>
          <div class="home-component-library">
            <button v-for="type in componentTypes" :key="type" type="button" :disabled="!canEdit" @click="addComponent(type)"><span>{{ componentNames[type] }}</span><strong aria-hidden="true">＋</strong></button>
          </div>
          <div class="home-toolbox-title"><h3>页面组件</h3><small>{{ editor.components.length }} 项</small></div>
          <p v-if="!editor.components.length" class="help-text">还没有组件。请从上方添加。</p>
          <ol v-else class="home-component-list">
            <li v-for="(item, index) in editor.components" :key="item.componentId" :class="{ selected: selectedId === item.componentId }">
              <button type="button" class="home-component-select" :aria-current="selectedId === item.componentId ? 'true' : undefined" @click="selectedId = item.componentId"><strong>{{ componentNames[item.type] }}</strong><small>{{ item.visible ? '显示中' : '已隐藏' }}</small></button>
              <div v-if="canEdit" class="home-row-tools"><button type="button" :disabled="index === 0" :aria-label="`上移${componentNames[item.type]}`" @click="moveComponent(index, -1)">↑</button><button type="button" :disabled="index === editor.components.length - 1" :aria-label="`下移${componentNames[item.type]}`" @click="moveComponent(index, 1)">↓</button></div>
            </li>
          </ol>
        </aside>

        <section class="home-preview-panel panel" aria-labelledby="home-preview-title">
          <div class="home-panel-title"><div><h2 id="home-preview-title">页面预览</h2><p>以服务端校验的草稿配置展示</p></div><span class="badge badge-muted">{{ preview ? `修订 ${preview.revision}` : '待预览' }}</span></div>
          <HomePagePreview v-if="preview" :config="preview.config" :stale="previewStale" />
          <div v-else class="home-preview-empty"><strong>预览尚未生成</strong><p>点击“预览”后，页面会保存未提交的修改，再显示服务端校验结果。</p><button type="button" class="secondary-button" :disabled="Boolean(busy)" @click="showPreview">生成预览</button></div>
        </section>

        <section class="home-settings panel" aria-labelledby="home-settings-title">
          <div class="home-panel-title"><div><h2 id="home-settings-title">{{ selected ? `组件设置 · ${componentNames[selected.type]}` : '页面设置 · 首页主题' }}</h2><p>{{ selected ? '修改后保存草稿，预览不会影响线上页面。' : '三项主题颜色只作用于首页。' }}</p></div></div>
          <template v-if="selected">
            <label v-if="canEdit" class="home-toggle"><input type="checkbox" :checked="selected.visible" @change="updateComponent({ ...selected, visible: ($event.target as HTMLInputElement).checked })">显示此组件</label>
            <span v-else class="badge badge-muted">{{ selected.visible ? '显示中' : '已隐藏' }}</span>
            <fieldset :disabled="!canEdit" class="home-settings-fieldset"><PageComponentEditor :disabled="!canEdit || Boolean(busy)" :component="selected" :can-upload="canUpload" :categories="categories" :products="products" :pages="pages" @change="updateComponent" @uploaded="bindUploadedAsset" /></fieldset>
            <p v-if="targetWarning" class="help-text">{{ targetWarning }}</p>
            <button v-if="canEdit" type="button" class="text-button danger home-remove" @click="removeComponent(selected.componentId)">移除组件</button>
          </template>
          <div v-else class="home-theme-fields">
            <label v-for="field in ([['pageBackgroundColor', '页面背景色'], ['headerBackgroundColor', '顶部区域底色'], ['brandTextColor', '顶部品牌文字色']] as const)" :key="field[0]">{{ field[1] }}<span class="home-color-control"><input type="color" :value="editor.theme[field[0]]" :disabled="!canEdit" :aria-label="field[1]" @input="updateTheme(field[0], ($event.target as HTMLInputElement).value)"><input :value="editor.theme[field[0]]" :disabled="!canEdit" maxlength="7" pattern="#[0-9A-Fa-f]{6}" @change="updateTheme(field[0], ($event.target as HTMLInputElement).value.trim())"></span></label>
            <p class="help-text">发布前会校验颜色格式；请在手机预览中检查文字与背景的对比。</p>
          </div>
        </section>
      </div>
      <section class="home-version-note"><h2>版本状态</h2><p>{{ draft.publishedRevision ? `修订 ${draft.publishedRevision} 正在线上生效。继续修改草稿不会改变这个版本。` : '首页尚未发布。小程序当前不会读取到首页内容。' }}</p><p>通过发布历史可查看与回退线上版本；回退保留当前草稿。</p></section>
    </template>
    <el-dialog v-model="publishOpen" title="确认发布首页" width="min(480px, 92vw)" @closed="publishPassword = ''">
      <p>将草稿修订 {{ draft?.revision }} 发布到小程序。发布后用户会看到这个版本，后续草稿修改不会立即上线。</p>
      <label class="home-publish-password">当前账号密码<el-input v-model="publishPassword" type="password" autocomplete="current-password" show-password placeholder="输入密码确认发布" @keyup.enter="publish" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <template #footer><button type="button" class="secondary-button" :disabled="busy === 'publish'" @click="closePublish">取消</button><button type="button" class="primary-button" :disabled="!publishPassword || busy === 'publish'" @click="publish">{{ busy === 'publish' ? '发布中…' : '确认发布' }}</button></template>
    </el-dialog>
  </section>
</template>
