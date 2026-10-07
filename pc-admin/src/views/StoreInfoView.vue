<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage } from 'element-plus'
import PublicationHistory from '../shared/PublicationHistory.vue'
import { keepRollbackIntent, type RollbackResult } from '../shared/publication-history'
import { applyCurrentPublication, currentPublication, publicationIntent, matchesPublishResult, parsePublishIntent, persistPublishIntent, type PublishIntent } from '../shared/publication-sync'
import { api, ApiError, type Account, type Confirmation } from '../api'
import AssetPicker from '../shared/AssetPicker.vue'
import type { Asset } from '../shared/media'
import { applyStartupUpload, type StartupDraft, type StartupPreview, type StartupPublication, type StartupSelection } from './startup/editor'
import './startup/store.css'

interface ShippingPolicy {
  feeFen: number
  deliveryScope: 'NATIONWIDE'
  revision: number
}

const props = defineProps<{ account: Account }>()
const canEdit = computed(() => props.account.permissionCodes.includes('startup.edit'))
const canPublish = computed(() => props.account.permissionCodes.includes('startup.publish'))
const canUpload = computed(() => props.account.permissionCodes.includes('asset.upload'))
const canManageShipping = computed(() => props.account.permissionCodes.includes('settlement.shipping.manage'))
const loading = ref(true)
const busy = ref('')
const error = ref('')
const notice = ref('')
const draft = ref<StartupDraft | null>(null)
const selection = ref<StartupSelection | null>(null)
const savedSnapshot = ref('')
const preview = ref<StartupPreview | null>(null)
const previewSnapshot = ref('')
const previewImageFailed = ref(false)
const publishOpen = ref(false)
const publishPassword = ref('')
const publishIntent = ref<PublishIntent | null>(null)
const publicationRefresh = ref<{ base: string; objectId: string } | null>(null)
const publicationRecoveryBlocked = ref(false)
const dirty = computed(() => !!selection.value && JSON.stringify(selection.value) !== savedSnapshot.value)
const previewStale = computed(() => !!preview.value && (dirty.value || previewSnapshot.value !== savedSnapshot.value))
const previewReady = computed(() => !!preview.value && !previewStale.value && preview.value.revision === draft.value?.revision)
const alreadyPublished = computed(() => !!draft.value && draft.value.publishedRevision === draft.value.revision)
const fileUrl = (id: string | null | undefined) => id ? `/api/v1/admin/assets/${encodeURIComponent(id)}/file` : ''
const shippingPolicy = ref<ShippingPolicy | null>(null)
const shippingFeeInput = ref('')
const shippingLoading = ref(false)
const shippingSaving = ref(false)
const shippingError = ref('')
const shippingNotice = ref('')
const shippingConflict = ref(false)
const shippingDirty = computed(() => {
  if (!shippingPolicy.value) return false
  const parsed = feeFenFromYuan(shippingFeeInput.value)
  return parsed === null ? shippingFeeInput.value !== (shippingPolicy.value.feeFen / 100).toFixed(2)
    : parsed !== shippingPolicy.value.feeFen
})
const hasUnsavedChanges = computed(() => dirty.value || shippingDirty.value)

function feeFenFromYuan(value: string): number | null {
  if (!/^(?:0|[1-9]\d{0,4})(?:\.\d{1,2})?$/.test(value)) return null
  const [yuan, cents = ''] = value.split('.')
  const feeFen = Number(yuan) * 100 + Number(cents.padEnd(2, '0'))
  return feeFen <= 1_000_000 ? feeFen : null
}
const shippingValidationError = computed(() =>
  feeFenFromYuan(shippingFeeInput.value) === null ? '请输入 0 至 10000 元的金额，最多两位小数。' : '')

function message(reason: unknown) {
  if (reason instanceof ApiError && ['PAGE_ALREADY_PUBLISHED', 'STARTUP_ALREADY_PUBLISHED'].includes(reason.code)) return '该草稿已有发布版本，请通过版本历史切换线上内容，或修改草稿后再发布。'
  if (reason instanceof ApiError && reason.status === 409) return '草稿已被其他操作修改，请重新读取后再编辑。'
  return reason instanceof Error ? reason.message : '操作失败，请稍后重试。'
}
async function loadDraft() {
  loading.value = true
  error.value = ''
  try {
    const result = await api<StartupDraft>('/startup/draft')
    draft.value = result
    restorePublishIntent()
    selection.value = { gifAssetId: result.gifAssetId, fallbackAssetId: result.fallbackAssetId }
    savedSnapshot.value = JSON.stringify(selection.value)
    preview.value = null
    previewSnapshot.value = ''
  } catch (reason) { error.value = message(reason) }
  finally { loading.value = false }
}
async function loadShippingPolicy() {
  if (!canManageShipping.value) return
  shippingLoading.value = true
  shippingError.value = ''
  shippingNotice.value = ''
  shippingConflict.value = false
  try {
    const result = await api<ShippingPolicy>('/settlement/shipping-policy')
    shippingPolicy.value = result
    shippingFeeInput.value = (result.feeFen / 100).toFixed(2)
  } catch (reason) { shippingError.value = message(reason) }
  finally { shippingLoading.value = false }
}
async function reloadShippingPolicy() {
  if (!shippingDirty.value || await confirmAction('放弃未保存的运费修改并重新读取配置？')) void loadShippingPolicy()
}
async function saveShippingPolicy() {
  const current = shippingPolicy.value
  const feeFen = feeFenFromYuan(shippingFeeInput.value)
  if (!current || !canManageShipping.value || shippingSaving.value || shippingConflict.value || feeFen === null) return
  shippingSaving.value = true
  shippingError.value = ''
  shippingNotice.value = ''
  const inputAtSave = shippingFeeInput.value
  try {
    const result = await api<ShippingPolicy>('/settlement/shipping-policy', {
      method: 'PUT',
      body: JSON.stringify({ feeFen, deliveryScope: 'NATIONWIDE', expectedRevision: current.revision }),
    })
    shippingPolicy.value = result
    if (shippingFeeInput.value === inputAtSave) shippingFeeInput.value = (result.feeFen / 100).toFixed(2)
    shippingNotice.value = shippingFeeInput.value === (result.feeFen / 100).toFixed(2)
      ? `运费已保存，当前修订 ${result.revision}。`
      : `运费修订 ${result.revision} 已保存；保存期间的新修改仍待保存。`
    ElMessage.success('运费配置已保存')
  } catch (reason) {
    shippingConflict.value = reason instanceof ApiError && reason.status === 409
    shippingError.value = shippingConflict.value
      ? '配置已被其他操作修改。请重新读取最新版本后再编辑。'
      : message(reason)
  } finally { shippingSaving.value = false }
}
onMounted(() => {
  void loadDraft()
  if (canManageShipping.value) void loadShippingPolicy()
  window.addEventListener('beforeunload', beforeUnload)
})
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
function beforeUnload(event: BeforeUnloadEvent) {
  if (!hasUnsavedChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}
onBeforeRouteLeave(async () => !hasUnsavedChanges.value || await confirmAction('店铺配置还有未保存的修改，确定离开吗？'))
async function reloadDraft() {
  if (!dirty.value || await confirmAction('放弃未保存的修改并重新读取草稿？')) void loadDraft()
}
function clearAsset(role: 'gif' | 'fallback') {
  if (!selection.value || !canEdit.value) return
  selection.value = { ...selection.value, [role === 'gif' ? 'gifAssetId' : 'fallbackAssetId']: null }
  preview.value = null
  notice.value = '素材已从本地草稿移除；保存后生效，线上版本不受影响。'
}
function chooseAsset(asset: Asset, role: 'gif' | 'fallback') {
  if (!selection.value || !canEdit.value || busy.value) return
  selection.value = { ...selection.value, [role === 'gif' ? 'gifAssetId' : 'fallbackAssetId']: asset.assetId }
  preview.value = null
  notice.value = '素材已选择到本地草稿，请保存后生效。'
}
async function upload(event: Event, role: 'gif' | 'fallback') {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !selection.value || busy.value || !canEdit.value || !canUpload.value) return
  const expectedAssetId = role === 'gif' ? selection.value.gifAssetId : selection.value.fallbackAssetId
  const validTypes = role === 'gif' ? ['image/gif'] : ['image/jpeg', 'image/png']
  if (!validTypes.includes(file.type) || !file.size || file.size > 10 * 1024 * 1024) {
    error.value = role === 'gif' ? '请选择不超过 10 MiB 的 GIF 文件。' : '请选择不超过 10 MiB 的 JPG 或 PNG 图片。'
    return
  }
  busy.value = `upload-${role}`
  error.value = ''
  notice.value = ''
  try {
    const body = new FormData()
    body.append('file', file)
    body.append('kind', role === 'gif' ? 'GIF' : 'IMAGE')
    const asset = await api<Asset>('/assets', { method: 'POST', body })
    const updated = applyStartupUpload(selection.value, { role, expectedAssetId, assetId: asset.assetId })
    if (updated === selection.value) {
      notice.value = '上传完成，但该素材位已经变化，新素材未绑定。请重新选择。'
      return
    }
    selection.value = updated
    preview.value = null
    notice.value = `${role === 'gif' ? '启动 GIF' : '兜底图'}已上传并绑定到草稿，记得保存。`
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = '' }
}
async function saveDraft(): Promise<boolean> {
  if (!draft.value || !selection.value || !canEdit.value) return false
  if (!dirty.value) return true
  if (busy.value) return false
  busy.value = 'save'
  error.value = ''
  const snapshot = JSON.stringify(selection.value)
  try {
    const result = await api<StartupDraft>('/startup/draft', {
      method: 'PUT',
      body: JSON.stringify({ expectedRevision: draft.value.revision, ...selection.value }),
    })
    draft.value = result
    savedSnapshot.value = snapshot
    preview.value = null
    notice.value = JSON.stringify(selection.value) === snapshot ? '草稿已保存；线上启动页未变化。' : '草稿已保存；保存期间新增的修改仍待保存。'
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
  notice.value = ''
  preview.value = null
  previewSnapshot.value = ''
  previewImageFailed.value = false
  try {
    const result = await api<StartupPreview>('/startup/preview', {
      method: 'POST', body: JSON.stringify({ expectedRevision: draft.value.revision }),
    })
    preview.value = result
    previewSnapshot.value = savedSnapshot.value
    notice.value = `服务端预览已生成 · 草稿修订 ${result.revision}。`
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = '' }
}
function openPublish() {
  if (!draft.value || busy.value || publicationRefresh.value || publicationRecoveryBlocked.value) return
  if (publishIntent.value) {
    if (publishIntent.value.objectId !== 'startup') { error.value = '另一个页面的发布结果尚未确认，请返回原页面恢复原发布请求。'; return }
    error.value = ''; publishPassword.value = ''; publishOpen.value = true; return
  }
  if (dirty.value) { error.value = '请先保存草稿，再发布当前修订。'; return }
  if (!previewReady.value) { error.value = '请先生成当前草稿的服务端预览。'; return }
  if (alreadyPublished.value) { error.value = '当前修订已发布。'; return }
  error.value = ''
  publishPassword.value = ''
  publishOpen.value = true
}
function closePublish() { publishOpen.value = false; publishPassword.value = '' }
function publishStorageKey(base: string) { return `mall-publication-publish:${JSON.stringify([props.account.accountId, base])}` }
function restorePublishIntent() {
  if (!draft.value) return
  publicationRecoveryBlocked.value = false
  try {
    const raw = sessionStorage.getItem(publishStorageKey('/startup'))
    publishIntent.value = parsePublishIntent(raw, '/startup', 'startup')
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
    const target = publicationRefresh.value || { base: '/startup', objectId: 'startup' }
    const current = await currentPublication(api, target.base)
    if (draft.value && 'startup' === target.objectId) draft.value = applyCurrentPublication(draft.value, current)
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
  if (publishIntent.value && publishIntent.value.objectId !== 'startup') { error.value = '另一个页面的发布结果尚未确认，请返回原页面恢复原发布请求。'; return }
  const recovering = !!publishIntent.value
  const intent = publishIntent.value || publicationIntent('/startup', 'startup', draft.value, crypto.randomUUID())
  if (!savePublishIntent(intent)) { error.value = '无法保存原发布恢复记录，发布未提交。请启用会话存储后重试。'; return }
  publishIntent.value = intent
  busy.value = 'publish'
  error.value = ''
  let submitted = false
  let accepted = false
  try {
    const { confirmationToken } = await api<Confirmation>('/auth/confirm', {
      method: 'POST', body: JSON.stringify({ action: 'startup.publish', password: publishPassword.value, objectId: intent.objectId, revision: intent.body.expectedRevision }),
    })
    submitted = true
    const result = await api<StartupPublication>(`${intent.base}/publish`, {
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
    if (draft.value && 'startup' === intent.objectId) draft.value = applyCurrentPublication(draft.value, current)
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
  <section class="page-content startup-page">
    <header class="page-heading startup-heading">
      <div><h1>冷启动画面</h1><p>上传动图与静态兜底图，保存草稿并预览后发布。发布仅影响小程序下一次冷启动。</p></div>
      <div class="startup-actions"><PublicationHistory v-if="draft" :account="account" base="/startup" :object-id="'startup'" :draft-revision="draft.revision" :disabled="loading || Boolean(busy)" startup @rolled-back="rolledBack" />
        <button type="button" class="secondary-button" :disabled="loading || Boolean(busy) || !canEdit || !dirty" @click="saveDraft">{{ busy === 'save' ? '保存中…' : '保存草稿' }}</button>
        <button type="button" class="secondary-button" :disabled="loading || Boolean(busy)" @click="showPreview">{{ busy === 'preview' ? '生成中…' : '预览' }}</button>
        <button v-if="canPublish" type="button" class="primary-button" :disabled="loading || Boolean(busy) || publicationRecoveryBlocked || Boolean(publicationRefresh) || (!publishIntent && (dirty || !previewReady || alreadyPublished))" @click="openPublish">{{ publishIntent ? '恢复原发布请求' : publicationRefresh ? '线上状态待同步' : alreadyPublished ? '当前修订已发布' : '发布启动页' }}</button>
      </div>
    </header>
    <div v-if="error" class="notice startup-error" role="alert">{{ error }} <button v-if="!draft" type="button" class="text-button" @click="loadDraft">重新加载</button></div>
    <p v-if="notice" class="startup-success" role="status">{{ notice }}</p>
    <p v-if="publicationRefresh" role="status">发布请求已完成，当前线上状态待同步。<button type="button" class="text-button" :disabled="Boolean(busy)" @click="syncPublication">重新读取线上状态</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在读取店铺启动配置…</p>
    <template v-else-if="draft && selection">
      <div class="startup-version">
        <span :class="draft.publishedRevision !== null ? 'badge badge-good' : 'badge badge-muted'">{{ draft.publishedRevision !== null ? `线上版本 · 修订 ${draft.publishedRevision}` : '尚未发布' }}</span>
        <span>草稿修订 {{ draft.revision }}</span><span v-if="dirty" class="startup-unsaved">● 有未保存修改</span><span v-else>草稿已保存</span>
        <button type="button" class="text-button" :disabled="Boolean(busy)" @click="reloadDraft">重新读取</button>
      </div>
      <div class="startup-grid">
        <div class="startup-materials">
          <article class="panel startup-material" aria-labelledby="startup-gif-title">
            <div class="startup-card-head"><div><span class="startup-step">01 / 主画面</span><h2 id="startup-gif-title">冷启动 GIF</h2><p>每次冷启动播放，页面出现后才开始 3 秒倒计时。</p></div><span class="badge" :class="selection.gifAssetId ? 'badge-good' : 'badge-muted'">{{ selection.gifAssetId ? '已选择' : '待上传' }}</span></div>
            <div class="startup-asset-frame"><img v-if="selection.gifAssetId" :src="fileUrl(selection.gifAssetId)" alt="当前草稿的冷启动 GIF" /><div v-else class="startup-placeholder"><span aria-hidden="true">▧</span><strong>尚未上传启动动图</strong><small>GIF · 最多 10 MiB</small></div></div>
            <div class="startup-card-foot"><label v-if="canEdit && canUpload" class="startup-upload secondary-button">{{ busy === 'upload-gif' ? '上传中…' : selection.gifAssetId ? '更换 GIF' : '上传 GIF' }}<input type="file" accept="image/gif" :disabled="Boolean(busy)" @change="upload($event, 'gif')" /></label><AssetPicker v-if="canEdit" kind="GIF" :disabled="Boolean(busy)" :target-key="JSON.stringify([draft.revision, selection.gifAssetId])" @select="chooseAsset($event, 'gif')" /><button v-if="canEdit && selection.gifAssetId" type="button" class="text-button danger" :disabled="Boolean(busy)" @click="clearAsset('gif')">移除</button><small v-if="!canUpload && canEdit">当前账号没有素材上传权限。</small></div>
          </article>
          <article class="panel startup-material" aria-labelledby="startup-fallback-title">
            <div class="startup-card-head"><div><span class="startup-step">02 / 故障兜底</span><h2 id="startup-fallback-title">静态兜底图</h2><p>GIF 无法加载时展示，不阻断进入原定页面。</p></div><span class="badge" :class="selection.fallbackAssetId ? 'badge-good' : 'badge-muted'">{{ selection.fallbackAssetId ? '已选择' : '待上传' }}</span></div>
            <div class="startup-asset-frame"><img v-if="selection.fallbackAssetId" :src="fileUrl(selection.fallbackAssetId)" alt="当前草稿的静态兜底图" /><div v-else class="startup-placeholder"><span aria-hidden="true">▣</span><strong>尚未上传静态图片</strong><small>JPG / PNG · 最多 10 MiB</small></div></div>
            <div class="startup-card-foot"><label v-if="canEdit && canUpload" class="startup-upload secondary-button">{{ busy === 'upload-fallback' ? '上传中…' : selection.fallbackAssetId ? '更换图片' : '上传图片' }}<input type="file" accept="image/jpeg,image/png" :disabled="Boolean(busy)" @change="upload($event, 'fallback')" /></label><AssetPicker v-if="canEdit" kind="IMAGE" :disabled="Boolean(busy)" :target-key="JSON.stringify([draft.revision, selection.fallbackAssetId])" @select="chooseAsset($event, 'fallback')" /><button v-if="canEdit && selection.fallbackAssetId" type="button" class="text-button danger" :disabled="Boolean(busy)" @click="clearAsset('fallback')">移除</button><small v-if="!canUpload && canEdit">当前账号没有素材上传权限。</small></div>
          </article>
        </div>
        <aside class="panel startup-preview" aria-labelledby="startup-preview-title">
          <div class="startup-preview-heading"><span class="startup-step">03 / 发布前检查</span><h2 id="startup-preview-title">启动页预览</h2><p>预览使用服务端校验后的当前草稿。屏幕示意比例以手机为准。</p></div>
          <div class="startup-phone"><div class="startup-phone-screen"><template v-if="preview && !previewStale"><img v-if="!previewImageFailed" :src="preview.gifUrl" alt="启动页 GIF 服务端预览" @error="previewImageFailed = true" /><img v-else :src="preview.fallbackUrl" alt="启动页静态兜底图预览" /><span class="startup-skip">跳过 3s</span></template><div v-else class="startup-preview-empty"><strong>{{ previewStale ? '预览已过期' : '等待服务端预览' }}</strong><small>{{ previewStale ? '请保存修改并重新预览' : '两张素材准备好后点击预览' }}</small></div></div></div>
          <p v-if="previewReady" class="startup-preview-ready" role="status">草稿修订 {{ preview?.revision }} 已通过服务端预览，可以发布。</p>
          <p v-else class="help-text">草稿可以单独保存；预览和发布要求 GIF、兜底图均有效。</p>
        </aside>
      </div>
      <div class="panel startup-note"><h2>当前线上版本</h2><p>{{ draft.publishedRevision !== null ? `修订 ${draft.publishedRevision} 正在生效。编辑草稿不会改变它。` : '尚未发布启动内容。小程序会使用内置静态品牌图继续进入目标页。' }}</p><p>通过发布历史可查看与回退线上版本；回退保留当前草稿。</p></div>
    </template>
    <section v-if="canManageShipping" class="panel shipping-policy" aria-labelledby="shipping-policy-title">
      <div class="shipping-policy-head">
        <div>
          <h2 id="shipping-policy-title">配送与运费</h2>
          <p>快递订单按单收取固定运费；纯到店核销订单不收运费，混合订单只收一次。</p>
        </div>
        <span v-if="shippingPolicy" class="badge badge-muted">修订 {{ shippingPolicy.revision }}</span>
      </div>
      <p v-if="shippingLoading" class="loading-inline" role="status">正在读取运费配置…</p>
      <div v-if="shippingError" class="notice startup-error" role="alert">
        {{ shippingError }}
        <button type="button" class="text-button" :disabled="shippingLoading || shippingSaving" @click="reloadShippingPolicy">重新读取</button>
      </div>
      <template v-if="shippingPolicy && !shippingLoading">
        <div class="shipping-policy-fields">
          <label class="shipping-policy-field" for="shipping-fee">
            <span>每单固定运费</span>
            <el-input id="shipping-fee" v-model="shippingFeeInput" inputmode="decimal" autocomplete="off" placeholder="例如 10.00" aria-describedby="shipping-fee-help shipping-fee-error">
              <template #append>元</template>
            </el-input>
            <small id="shipping-fee-help">0 至 10000 元，最多两位小数。</small>
            <small v-if="shippingDirty && shippingValidationError" id="shipping-fee-error" class="shipping-policy-invalid" role="alert">{{ shippingValidationError }}</small>
          </label>
          <div class="shipping-policy-field">
            <span>配送范围</span>
            <strong class="shipping-policy-scope">全国地址</strong>
            <small>首版固定为全国配送；提交订单时由服务端校验地址。</small>
          </div>
        </div>
        <div class="shipping-policy-footer">
          <span v-if="shippingDirty" class="startup-unsaved">● 运费修改尚未保存</span>
          <span v-else>当前配置已保存</span>
          <button type="button" class="text-button" :disabled="shippingSaving" @click="reloadShippingPolicy">重新读取</button>
          <button type="button" class="primary-button" :disabled="!shippingDirty || Boolean(shippingValidationError) || shippingSaving || shippingConflict" @click="saveShippingPolicy">
            {{ shippingSaving ? '保存中…' : '保存运费配置' }}
          </button>
        </div>
        <p v-if="shippingNotice" class="startup-success shipping-policy-notice" role="status">{{ shippingNotice }}</p>
      </template>
    </section>
    <el-dialog v-model="publishOpen" title="确认发布启动页" width="min(480px, 92vw)" @closed="publishPassword = ''">
      <p>将草稿修订 {{ draft?.revision }} 发布到小程序。下一次冷启动将读取这个版本。</p>
      <label class="startup-password">当前账号密码<el-input v-model="publishPassword" type="password" autocomplete="current-password" show-password placeholder="输入密码确认发布" @keyup.enter="publish" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <template #footer><button type="button" class="secondary-button" :disabled="busy === 'publish'" @click="closePublish">取消</button><button type="button" class="primary-button" :disabled="!publishPassword || busy === 'publish'" @click="publish">{{ busy === 'publish' ? '发布中…' : '确认发布' }}</button></template>
    </el-dialog>
  </section>
</template>
