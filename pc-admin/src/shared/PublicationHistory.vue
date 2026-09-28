<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, ApiError, confirmedWrite, type Account } from '../api'
import HomePagePreview from '../views/pages/HomePagePreview.vue'
import type { PageConfig } from '../views/pages/types'
import type { CustomerServiceConfig, NavigationConfig } from './singleton-config.mjs'
import { historyScope, keepRollbackIntent, matchesRollbackResult, parseRollbackIntent, persistRollbackIntent, rollbackBody, type RollbackIntent, type RollbackResult, type VersionSummary } from './publication-history'
const props = defineProps<{ account: Account; base: string; objectId: string; startup?: boolean; domain?: 'navigation' | 'customer_service'; disabled?: boolean; draftRevision: number }>()
const emit = defineEmits<{ rolledBack: [value: RollbackResult] }>()
interface VersionDetail extends VersionSummary { config?: PageConfig | NavigationConfig | CustomerServiceConfig; gifUrl?: string; fallbackUrl?: string }
interface HistoryPage { list: VersionSummary[]; total: number; page: number; pageSize: number; currentVersionId: string | null; publicationRevision: number; draftRevision: number }
const open = ref(false)
const loading = ref(false)
const detailLoading = ref(false)
const busy = ref(false)
const error = ref('')
const detailError = ref('')
const detailId = ref('')
const rollbackError = ref('')
const notice = ref('')
const recoveryBlocked = ref(false)
const failedMedia = ref<string[]>([])
const syncRequested = ref(false)
const page = ref(1)
const history = ref<HistoryPage | null>(null)
const detail = ref<VersionDetail | null>(null)
const reason = ref('')
const password = ref('')
const pending = ref<RollbackIntent | null>(null)
const scope = computed(() => historyScope(props.account.accountId, props.base))
const permission = computed(() => props.domain || (props.startup ? 'startup' : 'page'))
const navConfig = computed(() => props.domain === 'navigation' ? detail.value?.config as NavigationConfig | undefined : undefined)
const serviceConfig = computed(() => props.domain === 'customer_service' ? detail.value?.config as CustomerServiceConfig | undefined : undefined)
const pageConfig = computed(() => !props.startup && !props.domain ? detail.value?.config as PageConfig | undefined : undefined)
const assetUrl = (id: string | null | undefined) => id ? `/api/v1/admin/assets/${encodeURIComponent(id)}/file` : ''
const canRead = computed(() => props.account.permissionCodes.includes(`${permission.value}.read`))
const canRollback = computed(() => canRead.value && props.account.permissionCodes.includes(`${permission.value}.publish`))
const storageKey = (value: string) => `mall-publication-rollback:${value}`
let generation = 0
let detailGeneration = 0
let operationGeneration = 0
let alive = true
function restoreIntent() {
 recoveryBlocked.value = false
 try {
  const raw = sessionStorage.getItem(storageKey(scope.value))
  pending.value = parseRollbackIntent(raw, scope.value)
  if (raw && !pending.value) { recoveryBlocked.value = true; rollbackError.value = '原回退恢复记录损坏，已禁止新回退。请核对线上状态并联系管理员恢复原请求。' }
 } catch { recoveryBlocked.value = true; rollbackError.value = '浏览器无法读取回退恢复记录，请启用会话存储后重新打开历史。' }
 if (pending.value) reason.value = pending.value.body.reason
}
function saveIntent(intent: RollbackIntent | null, targetScope = scope.value): boolean {
 try {
  if (intent) {
   if (!persistRollbackIntent(sessionStorage, storageKey(targetScope), intent)) return false
  } else sessionStorage.removeItem(storageKey(targetScope))
 } catch { return false }
 if (targetScope === scope.value) pending.value = intent
 return true
}
function publisher(version: VersionSummary): string { return typeof version.publishedBy === 'string' ? version.publishedBy : version.publishedBy.displayName }
watch(scope, () => {
 generation++; detailGeneration++; operationGeneration++
 syncRequested.value = false
 open.value = false; history.value = null; detail.value = null; pending.value = null
 loading.value = false; detailLoading.value = false; busy.value = false
 password.value = ''; reason.value = ''; error.value = ''; rollbackError.value = ''; notice.value = ''
 restoreIntent()
}, { immediate: true })
watch(open, value => { password.value = ''; if (!value) { generation++; detailGeneration++ } })
async function load() {
 if (!canRead.value) return
 const current = ++generation, target = scope.value
 loading.value = true; error.value = ''
 try {
  const result = await api<HistoryPage>(`${props.base}/versions?page=${page.value}&pageSize=10`)
  if (alive && current === generation && target === scope.value) {
   history.value = result
   if (syncRequested.value && result.currentVersionId) {
    const live = await api<VersionDetail>(`${props.base}/versions/${encodeURIComponent(result.currentVersionId)}`)
    if (!alive || current !== generation || target !== scope.value) return
    emit('rolledBack', { versionId: result.currentVersionId, revision: live.revision, publicationRevision: result.publicationRevision, draftRevision: result.draftRevision })
    syncRequested.value = false
    notice.value = `原回退请求已成功记录。当前线上为修订 ${live.revision}；草稿与未保存的编辑保留。`
    if (detail.value) detail.value = { ...detail.value, isCurrent: detail.value.versionId === result.currentVersionId }
   }
   return result
  }
 } catch (failure) { if (alive && current === generation && target === scope.value) error.value = failure instanceof Error ? failure.message : '历史读取失败，请重试。' }
 finally { if (alive && current === generation) loading.value = false }
}
async function readDetail(versionId: string) {
 detailId.value = versionId; failedMedia.value = []
 const current = ++detailGeneration, target = scope.value
 detailLoading.value = true; detailError.value = ''; detail.value = null
 try {
  const result = await api<VersionDetail>(`${props.base}/versions/${encodeURIComponent(versionId)}`)
  if (alive && current === detailGeneration && target === scope.value) detail.value = result
 } catch (failure) { if (alive && current === detailGeneration && target === scope.value) detailError.value = failure instanceof Error ? failure.message : '版本详情读取失败，请重试。' }
 finally { if (alive && current === detailGeneration) detailLoading.value = false }
}
function show() { if (!props.disabled) { open.value = true; page.value = 1; void load(); if (pending.value) void readDetail(pending.value.body.versionId) } }
async function rollback() {
 if (!canRollback.value || busy.value || props.disabled || recoveryBlocked.value || !password.value || !history.value || (!pending.value && !detail.value)) return
 const wasRecovery = !!pending.value
 let intent = pending.value
 try {
  if (!intent) intent = { scope: scope.value, key: crypto.randomUUID(), body: rollbackBody(detail.value!.versionId, props.draftRevision, history.value.publicationRevision, reason.value) }
 } catch (failure) { rollbackError.value = (failure as Error).message; return }
 if (!wasRecovery && !saveIntent(intent)) { rollbackError.value = '浏览器无法保存原请求的恢复记录，回退未提交。请启用会话存储后重试。'; return }
 const current = ++operationGeneration, targetScope = scope.value, targetBase = props.base
 const targetObject = `${props.objectId}:${intent.body.versionId}`
 const action = `${permission.value}.rollback`, secret = password.value
 let accepted = false
 busy.value = true; rollbackError.value = ''; notice.value = ''
 try {
  const result = await confirmedWrite<RollbackResult>(action, secret, `${targetBase}/rollback`, 'POST', { ...intent.body }, targetObject, intent.body.expectedPublicationRevision, { 'Idempotency-Key': intent.key })
  if (!matchesRollbackResult(result, intent.body)) throw new Error('服务端结果尚未确认，请按原请求恢复。')
  accepted = true
  saveIntent(null, targetScope)
  if (alive && current === operationGeneration && targetScope === scope.value) {
   password.value = ''; reason.value = ''; syncRequested.value = true
   notice.value = '原回退请求已成功记录，正在读取当前线上状态。'
   const latest = await load()
   if (!latest) notice.value = '原回退请求已成功记录，但当前线上状态读取失败。请重新读取历史，当前草稿与编辑保持原样。'
  }
 } catch (failure) {
  if (alive && current === operationGeneration && targetScope === scope.value) {
   if (accepted) { rollbackError.value = '原回退已成功记录，但当前线上状态读取失败，请重新读取历史。'; return }
   if (!keepRollbackIntent(failure instanceof ApiError ? failure.status : undefined, wasRecovery)) saveIntent(null)
   rollbackError.value = `${failure instanceof Error ? failure.message : '回退失败。'}${pending.value ? ' 结果未确认，保留原版本、原因和请求编号，请输入密码后按原请求恢复。' : ' 当前线上版本保持不变，请重新读取历史后重试。'}`
  }
 } finally { if (alive && current === operationGeneration && targetScope === scope.value) { busy.value = false; password.value = '' } }
}
onBeforeUnmount(() => { alive = false; generation++; detailGeneration++; operationGeneration++; password.value = '' })
</script>
<template>
 <el-button v-if="canRead" :disabled="disabled" @click="show">发布历史与回退</el-button>
 <el-drawer v-model="open" title="发布历史与回退" size="860px" class="publication-history-drawer" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
  <p class="help-text">回退只切换当前线上内容，当前草稿与未保存的编辑保留。素材和跳转目标会由服务端重新校验。</p>
  <p v-if="notice" class="history-success" role="status">{{ notice }}</p>
  <p v-if="error" class="error" role="alert">{{ error }} <el-button text @click="load">重新读取</el-button></p>
  <p v-if="loading" role="status">正在读取发布历史…</p>
  <p v-else-if="history && !history.list.length">尚无发布历史。先保存、预览并发布内容。</p>
  <ul v-if="history?.list.length" class="history-versions" :aria-busy="loading">
   <li v-for="version in history.list" :key="version.versionId">
    <div><strong>{{ version.name || (startup ? '启动配置' : domain === 'navigation' ? '底部导航' : domain === 'customer_service' ? '客服悬浮入口' : '页面') }} · 修订 {{ version.revision }}</strong><el-tag v-if="version.isCurrent" type="success">当前线上</el-tag><span>{{ new Date(version.publishedAt).toLocaleString('zh-CN') }} · {{ publisher(version) }}</span></div>
    <el-button :disabled="loading || busy || !!pending" @click="readDetail(version.versionId)">查看版本</el-button>
   </li>
  </ul>
  <el-pagination v-if="history && history.total > 10" layout="prev, pager, next" :total="history.total" :page-size="10" :current-page="page" :disabled="loading || busy" @current-change="(value: number) => { page = value; load() }" />
  <p v-if="recoveryBlocked" class="error" role="alert">{{ rollbackError }}</p>
  <p v-if="detailLoading" role="status">正在读取版本详情…</p>
  <p v-if="detailError" class="error" role="alert">{{ detailError }} <el-button text @click="readDetail(detailId)">重新读取版本</el-button></p>
  <section v-if="detail" class="history-detail" aria-label="历史版本详情">
   <h2>修订 {{ detail.revision }} 的内容</h2>
   <div v-if="startup" class="history-startup-preview"><figure><p v-if="failedMedia.includes('gif')" role="status">历史启动 GIF 无法读取，请检查素材状态。</p><img v-else :src="detail.gifUrl" alt="历史启动 GIF" @error="failedMedia = [...failedMedia, 'gif']" /><figcaption>启动 GIF</figcaption></figure><figure><p v-if="failedMedia.includes('fallback')" role="status">历史兜底图无法读取，请检查素材状态。</p><img v-else :src="detail.fallbackUrl" alt="历史静态兜底图" @error="failedMedia = [...failedMedia, 'fallback']" /><figcaption>静态兜底图</figcaption></figure></div>
   <div v-else-if="navConfig" class="history-navigation-preview" aria-label="历史底部导航预览"><div v-for="item in navConfig.items" :key="item.key"><div class="history-navigation-icons"><img v-if="item.iconAssetId && !failedMedia.includes(`${item.key}:normal`)" :src="assetUrl(item.iconAssetId)" :alt="`${item.label}普通图标`" @error="failedMedia = [...failedMedia, `${item.key}:normal`]" /><span v-else class="history-image-placeholder">{{ item.iconAssetId ? '图片失效' : '默认' }}</span><img v-if="item.selectedIconAssetId && !failedMedia.includes(`${item.key}:selected`)" :src="assetUrl(item.selectedIconAssetId)" :alt="`${item.label}选中图标`" @error="failedMedia = [...failedMedia, `${item.key}:selected`]" /><span v-else class="history-image-placeholder">{{ item.selectedIconAssetId ? '图片失效' : '默认' }}</span></div><strong>{{ item.label }}</strong><small>普通 / 选中</small></div></div>
   <div v-else-if="serviceConfig" class="history-service-preview" aria-label="历史客服悬浮入口预览"><p>展示：{{ serviceConfig.enabled ? '开启' : '关闭' }} · 方式：{{ serviceConfig.mode === 'PHONE' ? '拨打电话' : serviceConfig.mode === 'QR' ? '客服二维码' : '未设置' }}</p><p v-if="serviceConfig.prompt">入口文案：{{ serviceConfig.prompt }}</p><p v-if="serviceConfig.mode === 'PHONE' && serviceConfig.phone">联系电话：{{ serviceConfig.phone }}</p><figure v-if="serviceConfig.qrAssetId"><img v-if="!failedMedia.includes('qr')" :src="assetUrl(serviceConfig.qrAssetId)" alt="历史客服二维码" @error="failedMedia = [...failedMedia, 'qr']" /><figcaption v-else>历史二维码素材无法读取。</figcaption></figure></div>
   <HomePagePreview v-else-if="pageConfig" :config="pageConfig" :stale="false" :page-name="detail.name" />
   <form v-if="canRollback && (!detail.isCurrent || pending)" class="history-rollback" @submit.prevent="rollback">
    <h3>{{ pending ? '恢复原回退请求' : '切换线上到此版本' }}</h3><p>需填写原因并确认当前账号密码。此操作不会覆盖草稿。</p>
    <label>回退原因<el-input v-model="reason" type="textarea" :rows="3" maxlength="200" show-word-limit :disabled="busy || !!pending" /></label>
    <label>当前账号密码<el-input v-model="password" type="password" show-password autocomplete="current-password" :disabled="busy" /></label>
    <p v-if="pending" class="help-text">原请求已固定到此版本。恢复时使用原请求编号和原始修订号，不生成新回退。</p>
    <p v-if="rollbackError" class="error" role="alert">{{ rollbackError }}</p>
    <el-button native-type="submit" type="primary" :loading="busy" :disabled="disabled || loading || recoveryBlocked || !password || !reason.trim()">{{ pending ? '按原请求恢复' : '确认回退线上版本' }}</el-button>
   </form>
   <p v-else-if="!canRollback" class="help-text">当前账号仅可查看发布历史，没有发布及回退权限。</p>
   <p v-else class="help-text">此版本已在线上生效。</p>
  </section>
  <template #footer><el-button :disabled="busy" @click="open = false">关闭历史</el-button></template>
 </el-drawer>
</template>
<style>
.publication-history-drawer{max-width:100vw;color:var(--mall-color-text)}.history-versions{list-style:none;padding:0;margin:24px 0}.history-versions li{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:16px 0;border-bottom:1px solid var(--mall-color-border)}.history-versions li>div{display:flex;gap:8px;flex-wrap:wrap;min-width:0}.history-versions li span:not(.el-tag){display:block;width:100%;color:var(--mall-color-muted);overflow-wrap:anywhere}.history-detail{margin-top:32px}.history-detail h2{font-size:20px}.history-rollback{display:grid;gap:16px;margin-top:24px;padding-top:24px;border-top:1px solid var(--mall-color-border)}.history-rollback label{display:grid;gap:8px}.history-rollback .el-button{justify-self:start}.history-startup-preview{display:grid;grid-template-columns:1fr 1fr;gap:16px}.history-startup-preview figure{margin:0}.history-startup-preview img{display:block;width:100%;max-height:400px;object-fit:contain;background:var(--mall-color-canvas-admin)}.history-startup-preview figcaption{margin-top:8px;color:var(--mall-color-muted)}.history-success{color:var(--mall-color-success);background:var(--mall-color-canvas-admin);padding:12px}@media(max-width:600px){.history-versions li{align-items:start;flex-direction:column}.history-startup-preview{grid-template-columns:1fr}.publication-history-drawer .el-pagination{max-width:100%;overflow:auto}}
.history-navigation-preview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;max-width:600px;padding:12px;background:var(--mall-color-canvas-admin);border-radius:12px}.history-navigation-preview>div{min-width:0;display:grid;justify-items:center;gap:4px;text-align:center}.history-navigation-icons{display:flex;gap:4px}.history-navigation-preview img,.history-image-placeholder{width:32px;height:32px;object-fit:contain}.history-image-placeholder{font-size:10px;color:var(--mall-color-muted)}.history-navigation-preview strong{font-size:13px}.history-navigation-preview small{font-size:11px;color:var(--mall-color-muted)}.history-service-preview figure{margin:12px 0}.history-service-preview img{display:block;max-width:200px;max-height:200px;object-fit:contain}@media(max-width:600px){.history-navigation-preview{gap:4px;padding:8px}.history-navigation-preview img,.history-image-placeholder{width:28px}.history-navigation-preview small{font-size:10px}}
</style>
