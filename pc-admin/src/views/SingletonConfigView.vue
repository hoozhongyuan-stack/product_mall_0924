<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, ApiError, type Account, type Confirmation } from '../api'
import AssetPicker from '../shared/AssetPicker.vue'
import PublicationHistory from '../shared/PublicationHistory.vue'
import { keepRollbackIntent, type RollbackResult } from '../shared/publication-history'
import { applyCurrentPublication, currentPublication, matchesPublishResult, parsePublishIntent, persistPublishIntent, publicationIntent, type PublishIntent } from '../shared/publication-sync'
import { copyConfig, reconcileSavedConfig, updateNavigationItem, validateCustomerService, validateNavigation, type CustomerServiceConfig, type NavigationConfig, type NavigationItem } from '../shared/singleton-config.mjs'
import type { Asset } from '../shared/media'
import './singleton-config.css'

type Config = NavigationConfig | CustomerServiceConfig
interface Draft { revision: number; publicationRevision: number; publishedRevision: number | null; publishedVersionId: string | null; config: Config }
interface Preview { revision: number; config: Config }
interface Publication { versionId: string; revision: number; publicationRevision: number; draftRevision: number }
const props = defineProps<{ account: Account; domain: 'navigation' | 'customer_service' }>()
const base = computed(() => `/${props.domain.replace('_', '-')}`)
const title = computed(() => props.domain === 'navigation' ? '底部导航' : '客服悬浮入口')
const canEdit = computed(() => props.account.permissionCodes.includes(`${props.domain}.edit`))
const canPublish = computed(() => props.account.permissionCodes.includes(`${props.domain}.publish`))
const draft = ref<Draft | null>(null)
const config = ref<Config | null>(null)
const savedSnapshot = ref('')
const preview = ref<Preview | null>(null)
const previewSnapshot = ref('')
const loading = ref(true)
const busy = ref('')
const error = ref('')
const notice = ref('')
const publishOpen = ref(false)
const password = ref('')
const intent = ref<PublishIntent | null>(null)
const recoveryBlocked = ref(false)
const syncPending = ref(false)
const failedMedia = ref<string[]>([])
const nav = computed(() => props.domain === 'navigation' ? config.value as NavigationConfig | null : null)
const service = computed(() => props.domain === 'customer_service' ? config.value as CustomerServiceConfig | null : null)
const dirty = computed(() => config.value !== null && JSON.stringify(config.value) !== savedSnapshot.value)
const validation = computed(() => config.value === null ? '' : props.domain === 'navigation' ? validateNavigation(config.value as NavigationConfig) : validateCustomerService(config.value as CustomerServiceConfig))
const previewReady = computed(() => !!draft.value && !!preview.value && preview.value.revision === draft.value.revision && previewSnapshot.value === savedSnapshot.value && !dirty.value)
const alreadyPublished = computed(() => !!draft.value && draft.value.publishedRevision === draft.value.revision)
const assetUrl = (id: string | null | undefined) => id ? `/api/v1/admin/assets/${encodeURIComponent(id)}/file` : ''
function mediaFailed(id: string | null) { if (id && !failedMedia.value.includes(id)) failedMedia.value = [...failedMedia.value, id] }
const storageKey = (targetBase: string) => `mall-publication-publish:${JSON.stringify([props.account.accountId, targetBase])}`
let generation = 0

function message(reason: unknown): string {
  if (reason instanceof ApiError && reason.code === 'ALREADY_PUBLISHED') return '该草稿修订曾发布过。请通过历史切换线上版本，或修改草稿后再发布。'
  if (reason instanceof ApiError && reason.status === 409) return '草稿或线上版本已变化，请核对当前修订后重新读取。'
  return reason instanceof Error ? reason.message : '操作失败，请稍后重试。'
}
function restoreIntent() {
  recoveryBlocked.value = false
  try {
    const raw = sessionStorage.getItem(storageKey(base.value))
    intent.value = parsePublishIntent(raw, base.value, props.domain)
    if (raw && !intent.value) throw new Error('恢复记录损坏')
    if (intent.value) notice.value = '上次发布结果尚未确认。请使用原请求恢复；原请求编号及修订号已保留。'
  } catch { recoveryBlocked.value = true; error.value = '无法读取原发布恢复记录，已阻止新发布。请恢复会话存储后重新读取。' }
}
function saveIntent(value: PublishIntent): boolean {
  try { return persistPublishIntent(sessionStorage, storageKey(value.base), value) } catch { return false }
}
function forgetIntent(targetBase: string) {
  try { sessionStorage.removeItem(storageKey(targetBase)) } catch { /* A verified original request remains recoverable. */ }
}
async function loadDraft() {
  if (dirty.value && !window.confirm(`放弃未保存的${title.value}修改并重新读取草稿？`)) return
  const current = ++generation
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api<Draft>(`${base.value}/draft`)
    if (current !== generation) return
    draft.value = result
    config.value = copyConfig(result.config)
    savedSnapshot.value = JSON.stringify(result.config)
    preview.value = null; previewSnapshot.value = ''; failedMedia.value = []
    restoreIntent()
  } catch (reason) { if (current === generation) error.value = message(reason) }
  finally { if (current === generation) loading.value = false }
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault(); event.returnValue = ''
}
onMounted(() => { void loadDraft(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => { generation++; window.removeEventListener('beforeunload', beforeUnload); password.value = '' })
onBeforeRouteLeave(() => !dirty.value || window.confirm(`${title.value}草稿还有未保存的修改，确定离开吗？`))
watch(() => props.domain, () => { generation++; draft.value = null; config.value = null; intent.value = null; preview.value = null; void loadDraft() })
function setNavIcon(item: NavigationItem, role: 'iconAssetId' | 'selectedIconAssetId', assetId: string | null) {
  if (!nav.value || !canEdit.value || busy.value) return
  config.value = updateNavigationItem(nav.value, item.key, { [role]: assetId })
  preview.value = null
}
function setService(patch: Partial<CustomerServiceConfig>) {
  if (!service.value || !canEdit.value || busy.value) return
  config.value = { ...service.value, ...patch }
  preview.value = null
}
function setServiceMode(mode: 'PHONE' | 'QR' | null) {
  setService({ mode, phone: mode === 'PHONE' ? service.value?.phone || '' : null, qrAssetId: mode === 'QR' ? service.value?.qrAssetId || null : null })
}
async function saveDraft(): Promise<boolean> {
  if (!draft.value || !config.value || !canEdit.value || busy.value) return false
  if (!dirty.value) return true
  if (validation.value) { error.value = validation.value; return false }
  const current = ++generation, payload = copyConfig(config.value)
  busy.value = 'save'; error.value = ''
  try {
    const result = await api<Draft>(`${base.value}/draft`, { method: 'PUT', body: JSON.stringify({ expectedRevision: draft.value.revision, config: payload }) })
    if (current !== generation) return false
    const settled = reconcileSavedConfig(config.value, payload, result.config)
    draft.value = result
    config.value = settled.config
    savedSnapshot.value = settled.savedSnapshot
    preview.value = null; previewSnapshot.value = ''
    notice.value = settled.editedDuringSave ? '草稿已保存；保存期间新增的编辑仍未保存。' : '草稿已保存，线上配置未变化。'
    return true
  } catch (reason) { if (current === generation) error.value = message(reason); return false }
  finally { if (current === generation) busy.value = '' }
}
async function showPreview() {
  if (!draft.value || busy.value) return
  if (dirty.value && !(await saveDraft())) return
  if (!draft.value) return
  const current = ++generation
  busy.value = 'preview'; error.value = ''; preview.value = null
  try {
    const result = await api<Preview>(`${base.value}/preview`, { method: 'POST', body: JSON.stringify({ expectedRevision: draft.value.revision }) })
    if (current !== generation) return
    preview.value = result
    previewSnapshot.value = savedSnapshot.value
    notice.value = `服务端预览已生成 · 草稿修订 ${result.revision}。`
  } catch (reason) { if (current === generation) error.value = message(reason) }
  finally { if (current === generation) busy.value = '' }
}
function openPublish() {
  if (busy.value || syncPending.value || recoveryBlocked.value || !draft.value) return
  if (!intent.value && (dirty.value || !previewReady.value || alreadyPublished.value)) { error.value = '请先保存并预览待发布的草稿修订。'; return }
  error.value = ''; password.value = ''; publishOpen.value = true
}
async function syncPublication() {
  if (!draft.value || busy.value) return
  busy.value = 'sync'; error.value = ''
  try {
    const state = await currentPublication(api, base.value)
    if (draft.value) draft.value = applyCurrentPublication(draft.value, state)
    syncPending.value = false; forgetIntent(base.value)
    notice.value = `当前线上为${state.publishedRevision === null ? '尚未发布' : `修订 ${state.publishedRevision}`}；草稿与未保存编辑保留。`
  } catch (reason) { error.value = `发布已记录，但当前线上状态读取失败。请重新读取线上状态。${message(reason)}` }
  finally { busy.value = '' }
}
async function publish() {
  if (!draft.value || !password.value || busy.value || syncPending.value || recoveryBlocked.value) return
  if (!intent.value && (dirty.value || !previewReady.value || alreadyPublished.value)) return
  const recovering = !!intent.value
  const request = intent.value || publicationIntent(base.value, props.domain, draft.value, crypto.randomUUID())
  if (!saveIntent(request)) { error.value = '无法保存原发布请求，发布未提交。请启用会话存储后重试。'; return }
  intent.value = request
  busy.value = 'publish'; error.value = ''
  let submitted = false, accepted = false
  try {
    const { confirmationToken } = await api<Confirmation>('/auth/confirm', {
      method: 'POST', body: JSON.stringify({ action: `${props.domain}.publish`, password: password.value, objectId: request.objectId, revision: request.body.expectedRevision }),
    })
    submitted = true
    const result = await api<Publication>(`${request.base}/publish`, {
      method: 'POST', headers: { 'X-Action-Confirmation': confirmationToken, 'Idempotency-Key': request.key }, body: JSON.stringify(request.body),
    })
    if (!matchesPublishResult(result, request.body)) throw new Error('服务端发布结果尚未确认，请按原请求恢复。')
    accepted = true
    intent.value = null; syncPending.value = true; publishOpen.value = false; password.value = ''
    notice.value = '发布请求已记录，正在读取当前线上配置。'
    await syncPublicationAfterWrite(request.base)
    ElMessage.success('发布请求已完成')
  } catch (reason) {
    if (accepted) error.value = `发布已记录，但当前线上状态读取失败。请重新读取线上状态。${message(reason)}`
    else {
      if (!recovering && (!submitted || !keepRollbackIntent(reason instanceof ApiError ? reason.status : undefined, false))) { intent.value = null; forgetIntent(request.base) }
      error.value = `${message(reason)}${intent.value ? ' 结果未确认，请使用原请求恢复。' : ''}`
    }
  } finally { busy.value = ''; password.value = '' }
}
async function syncPublicationAfterWrite(targetBase: string) {
  const state = await currentPublication(api, targetBase)
  if (draft.value) draft.value = applyCurrentPublication(draft.value, state)
  syncPending.value = false; forgetIntent(targetBase)
  notice.value = `当前线上为${state.publishedRevision === null ? '尚未发布' : `修订 ${state.publishedRevision}`}；草稿与未保存编辑保留。`
}
function rolledBack(result: RollbackResult) {
  if (!draft.value) return
  draft.value = { ...draft.value, publishedRevision: result.revision, publishedVersionId: result.versionId, publicationRevision: result.publicationRevision }
  notice.value = `线上已切换至修订 ${result.revision}；草稿与未保存的编辑保持原样。`
}
</script>

<template>
  <section class="page-content singleton-page">
    <header class="page-heading singleton-heading">
      <div><h1>{{ title }}</h1><p>{{ domain === 'navigation' ? '固定四个入口；可替换普通与选中图标。更改草稿不会立即影响小程序。' : '设置首页与商品浏览页的客服悬浮入口。保存、预览并发布后才影响线上。' }}</p></div>
      <div class="singleton-actions">
        <PublicationHistory v-if="draft" :account="account" :base="base" :object-id="domain" :domain="domain" :draft-revision="draft.revision" :disabled="loading || Boolean(busy) || Boolean(intent) || syncPending || recoveryBlocked" @rolled-back="rolledBack" />
        <el-button :disabled="loading || Boolean(busy) || !canEdit || !dirty" :loading="busy === 'save'" @click="saveDraft">保存草稿</el-button>
        <el-button :disabled="loading || Boolean(busy)" :loading="busy === 'preview'" @click="showPreview">服务端预览</el-button>
        <el-button v-if="canPublish" type="primary" :disabled="loading || Boolean(busy) || recoveryBlocked || syncPending || (!intent && (dirty || !previewReady || alreadyPublished))" @click="openPublish">{{ intent ? '恢复原发布请求' : alreadyPublished ? '当前修订已发布' : '发布配置' }}</el-button>
      </div>
    </header>
    <p v-if="error" class="singleton-error" role="alert">{{ error }} <el-button v-if="!draft" text @click="loadDraft">重新读取</el-button></p>
    <p v-if="notice" class="singleton-notice" role="status">{{ notice }}</p>
    <p v-if="syncPending" class="singleton-error" role="status">发布已记录，当前线上状态待同步。<el-button text :disabled="Boolean(busy)" @click="syncPublication">重新读取线上状态</el-button></p>
    <p v-if="loading" role="status">正在读取{{ title }}草稿…</p>
    <template v-if="draft && config">
      <div class="singleton-meta"><span>草稿修订 {{ draft.revision }}</span><span>线上{{ draft.publishedRevision === null ? '尚未发布' : `修订 ${draft.publishedRevision}` }}</span><span v-if="dirty" class="singleton-dirty">有未保存修改</span><span v-else>草稿已保存</span></div>
      <p v-if="!canEdit" class="singleton-readonly">当前账号只有查看权限，不能修改草稿。</p>
      <p v-if="validation" class="singleton-error" role="alert">{{ validation }}</p>
      <section v-if="nav" class="singleton-editor" aria-labelledby="navigation-editor-title"><div class="singleton-section-head"><h2 id="navigation-editor-title">四个固定入口</h2><p>名称和跳转目标固定。每项可选一对静态图标；不配置时使用小程序默认图标。</p></div>
        <div class="singleton-nav-grid"><article v-for="item in nav.items" :key="item.key" class="singleton-nav-item"><div class="singleton-nav-title"><strong>{{ item.label }}</strong><small>{{ item.key }}</small></div><div class="singleton-icon-pair"><div><span>普通图标</span><img v-if="item.iconAssetId && !failedMedia.includes(item.iconAssetId)" :src="assetUrl(item.iconAssetId)" :alt="`${item.label}普通图标`" @error="mediaFailed(item.iconAssetId)" /><span v-else class="singleton-icon-empty">{{ item.iconAssetId ? '图片失效' : '默认' }}</span><AssetPicker v-if="canEdit" kind="IMAGE" square :disabled="Boolean(busy)" :target-key="JSON.stringify([draft.revision, item.key, 'normal', item.iconAssetId])" @select="setNavIcon(item, 'iconAssetId', $event.assetId)" /></div><div><span>选中图标</span><img v-if="item.selectedIconAssetId && !failedMedia.includes(item.selectedIconAssetId)" :src="assetUrl(item.selectedIconAssetId)" :alt="`${item.label}选中图标`" @error="mediaFailed(item.selectedIconAssetId)" /><span v-else class="singleton-icon-empty">{{ item.selectedIconAssetId ? '图片失效' : '默认' }}</span><AssetPicker v-if="canEdit" kind="IMAGE" square :disabled="Boolean(busy)" :target-key="JSON.stringify([draft.revision, item.key, 'selected', item.selectedIconAssetId])" @select="setNavIcon(item, 'selectedIconAssetId', $event.assetId)" /></div></div><el-button v-if="canEdit && (item.iconAssetId || item.selectedIconAssetId)" text :disabled="Boolean(busy)" @click="config = updateNavigationItem(nav, item.key, { iconAssetId: null, selectedIconAssetId: null }); preview = null">恢复默认图标</el-button></article></div>
      </section>
      <section v-if="service" class="singleton-editor singleton-service" aria-labelledby="service-editor-title"><div class="singleton-section-head"><h2 id="service-editor-title">展示与联系</h2><p>仅在首页及商品浏览页显示。关闭后保留已填写的草稿内容，发布关闭版本才会停止展示。</p></div>
        <label class="singleton-toggle"><input type="checkbox" :checked="service.enabled" :disabled="!canEdit || Boolean(busy)" @change="setService({ enabled: ($event.target as HTMLInputElement).checked })" />启用客服悬浮入口</label>
        <div class="singleton-form-grid"><label>联系方法<el-select :model-value="service.mode" placeholder="请选择" :disabled="!canEdit || Boolean(busy)" @update:model-value="setServiceMode($event as 'PHONE' | 'QR' | null)"><el-option label="不设置" :value="null" /><el-option label="拨打电话" value="PHONE" /><el-option label="客服二维码" value="QR" /></el-select></label><label>入口文案（最多 20 字）<el-input :model-value="service.prompt" maxlength="20" show-word-limit placeholder="例如：联系店铺" :disabled="!canEdit || Boolean(busy)" @update:model-value="setService({ prompt: $event })" /></label></div>
        <label v-if="service.mode === 'PHONE'" class="singleton-field">客服手机号<el-input :model-value="service.phone || ''" maxlength="11" inputmode="tel" autocomplete="tel" placeholder="11 位大陆手机号" :disabled="!canEdit || Boolean(busy)" @update:model-value="setService({ phone: $event })" /></label>
        <div v-if="service.mode === 'QR'" class="singleton-asset"><div><strong>客服二维码</strong><p>选择可用的静态图片，发布时服务端会再次检查。</p></div><img v-if="service.qrAssetId && !failedMedia.includes(service.qrAssetId)" :src="assetUrl(service.qrAssetId)" alt="当前草稿客服二维码" @error="mediaFailed(service.qrAssetId)" /><span v-else-if="service.qrAssetId" class="singleton-icon-empty">图片失效</span><AssetPicker v-if="canEdit" kind="IMAGE" :disabled="Boolean(busy)" :target-key="JSON.stringify([draft.revision, 'qr', service.qrAssetId])" @select="setService({ qrAssetId: $event.assetId })" /><el-button v-if="canEdit && service.qrAssetId" text :disabled="Boolean(busy)" @click="setService({ qrAssetId: null })">移除二维码</el-button></div>
        <div class="singleton-asset"><div><strong>悬浮入口图标（可选）</strong><p>未选择时使用小程序默认图标。</p></div><img v-if="service.iconAssetId && !failedMedia.includes(service.iconAssetId)" :src="assetUrl(service.iconAssetId)" alt="当前草稿悬浮入口图标" @error="mediaFailed(service.iconAssetId)" /><span v-else-if="service.iconAssetId" class="singleton-icon-empty">图片失效</span><AssetPicker v-if="canEdit" kind="IMAGE" square :disabled="Boolean(busy)" :target-key="JSON.stringify([draft.revision, 'icon', service.iconAssetId])" @select="setService({ iconAssetId: $event.assetId })" /><el-button v-if="canEdit && service.iconAssetId" text :disabled="Boolean(busy)" @click="setService({ iconAssetId: null })">恢复默认图标</el-button></div>
      </section>
      <section v-if="preview" class="singleton-preview" aria-label="服务端预览"><h2>服务端预览 · 修订 {{ preview.revision }}</h2><p>发布前确认：{{ domain === 'navigation' ? '四个固定入口与图标素材' : '启用状态、联系方法及素材' }}。</p><p v-if="!previewReady" class="singleton-error">草稿已变化，请重新生成预览。</p><div v-else-if="domain === 'navigation'" class="singleton-preview-nav"><span v-for="item in (preview.config as NavigationConfig).items" :key="item.key">{{ item.label }}{{ item.iconAssetId ? ' · 自定义图标' : ' · 默认图标' }}</span></div><p v-else>{{ (preview.config as CustomerServiceConfig).enabled ? '线上将展示客服入口' : '线上将隐藏客服入口' }} · {{ (preview.config as CustomerServiceConfig).mode === 'PHONE' ? '电话' : (preview.config as CustomerServiceConfig).mode === 'QR' ? '二维码' : '未设置' }}</p></section>
    </template>
    <el-dialog v-model="publishOpen" :title="intent ? `恢复原${title}发布请求` : `发布${title}`" width="min(460px, calc(100vw - 24px))" :close-on-click-modal="!busy"><p>{{ intent ? '按原请求编号和原修订号重新确认，不会生成新发布。' : '确认发布已保存并预览的草稿修订。' }}</p><label class="singleton-field">当前账号密码<el-input v-model="password" type="password" show-password autocomplete="current-password" :disabled="Boolean(busy)" /></label><p v-if="error" class="singleton-error" role="alert">{{ error }}</p><template #footer><el-button :disabled="Boolean(busy)" @click="publishOpen = false; password = ''">取消</el-button><el-button type="primary" :loading="busy === 'publish'" :disabled="!password || Boolean(busy)" @click="publish">{{ intent ? '按原请求恢复' : '确认发布' }}</el-button></template></el-dialog>
  </section>
</template>
