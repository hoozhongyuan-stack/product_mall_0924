<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, type Account, type Confirmation } from '../api'
import ReleaseWorkflowPanel from './ReleaseWorkflowPanel.vue'
import './code-versions.css'

interface CodeVersion {
  versionId: string
  versionLabel: string
  sourceRevision: string | null
  sourceDigest: string
  packageSha256: string
  packageBytes: number
  fileCount: number
  storageStatus: string
  platformStatus: string
  createdAt: string
  completedAt: string | null
  failureCode: string
}
interface SyncJob {
  taskId: string
  versionId: string | null
  status: string
  failureCode: string
  createdAt: string
  completedAt: string | null
}
interface VersionPage { items: CodeVersion[]; nextCursor: string | null }
interface ReleaseCheck { code: string; status: 'PASS' | 'BLOCKED' | 'UNVERIFIED'; title: string; detail: string }
interface UploadKeyStatus { configured: boolean; revision: number; appId: string | null }
interface ReleaseReadiness { appId: string | null; versionId: string | null; egressIp: string | null; checks: ReleaseCheck[];
  uploadKey: UploadKeyStatus; developerAppId: string | null; developerUploadKey: UploadKeyStatus }

const props = defineProps<{ account: Account }>()

const versions = ref<CodeVersion[]>([])
const jobs = ref<SyncJob[]>([])
const nextCursor = ref<string | null>(null)
const currentCursor = ref('')
const previousCursors = ref<string[]>([])
const nextJobCursor = ref<string | null>(null)
const currentJobCursor = ref('')
const previousJobCursors = ref<string[]>([])
const versionLoading = ref(true)
const jobLoading = ref(true)
const versionError = ref('')
const jobError = ref('')
const selected = ref<CodeVersion | null>(null)
const selectedId = ref('')
const detailLoading = ref(false)
const detailError = ref('')
const readiness = ref<ReleaseReadiness | null>(null)
const readinessLoading = ref(true)
const readinessError = ref('')
const keyFile = ref<File | null>(null)
const keyInput = ref<HTMLInputElement | null>(null)
const keyPassword = ref('')
const keyConfirming = ref(false)
const keyBusy = ref(false)
const keyNeedsRead = ref(false)
const keyNotice = ref('')
const keyError = ref('')
const canManageKey = computed(() => props.account.permissionCodes.includes('code.version.read') && props.account.permissionCodes.includes('code.release.manage'))
const canManagePlatform = computed(() => props.account.permissionCodes.includes('wechat.integration.read') && props.account.permissionCodes.includes('wechat.integration.manage'))
const directCheckCodes = new Set(['APP_ID', 'SOURCE_PACKAGE', 'RELEASE_CONFIG', 'UPLOAD_KEY'])
const directChecks = computed(() => readiness.value?.checks.filter(check => directCheckCodes.has(check.code)) || [])
let versionGeneration = 0
let jobGeneration = 0
let detailGeneration = 0
let readinessGeneration = 0
let keyGeneration = 0

function checkLabel(status: ReleaseCheck['status']) {
  return { PASS: '已满足', BLOCKED: '未满足', UNVERIFIED: '无法验证' }[status]
}

async function loadReadiness() {
  const generation = ++readinessGeneration
  readinessLoading.value = true
  readinessError.value = ''
  readiness.value = null
  try {
    const value = await api<ReleaseReadiness>('/code-release/readiness')
    if (generation !== readinessGeneration) return
    if (!Array.isArray(value.checks) || !value.uploadKey || !Number.isInteger(value.uploadKey.revision)) throw new Error('Invalid readiness')
    readiness.value = value
    keyNeedsRead.value = false
  } catch {
    if (generation === readinessGeneration) { readinessError.value = '发布条件读取失败，请重新读取。'; keyNeedsRead.value = true }
  } finally {
    if (generation === readinessGeneration) readinessLoading.value = false
  }
}

function chooseKey(event: Event) {
  const input = event.target as HTMLInputElement
  keyConfirming.value = false
  keyPassword.value = ''
  keyFile.value = input.files?.[0] || null
  keyNotice.value = ''
  keyError.value = ''
  if (keyFile.value && (keyFile.value.size < 1 || keyFile.value.size > 16 * 1024 || !keyFile.value.name.endsWith('.key'))) {
    keyFile.value = null
    input.value = ''
    keyError.value = '请选择不超过 16 KiB 的 .key 文件。'
  }
}

function clearKeyInput() {
  keyFile.value = null
  if (keyInput.value) keyInput.value.value = ''
}

function beginKeyUpload() {
  if (!canManageKey.value || !readiness.value?.appId || !keyFile.value || keyNeedsRead.value || keyBusy.value) return
  keyConfirming.value = true
  keyPassword.value = ''
  keyError.value = ''
}

async function uploadKey() {
  const current = readiness.value
  const file = keyFile.value
  if (!canManageKey.value || !current?.appId || !file || !keyConfirming.value || !keyPassword.value || keyBusy.value || keyNeedsRead.value) return
  const generation = ++keyGeneration
  keyBusy.value = true
  keyError.value = ''
  keyNotice.value = ''
  try {
    const confirmation = await api<Confirmation>('/auth/confirm', {
      method: 'POST', body: JSON.stringify({ action: 'code.release.upload_key', objectId: current.appId,
        revision: current.uploadKey.revision, password: keyPassword.value }),
    })
    if (generation !== keyGeneration || !canManageKey.value || !confirmation.confirmationToken) return
    const key = await file.text()
    if (generation !== keyGeneration || !canManageKey.value || readiness.value?.appId !== current.appId ||
        readiness.value?.uploadKey.revision !== current.uploadKey.revision || keyFile.value !== file) return
    const result = await api<UploadKeyStatus>('/code-release/upload-key', {
      method: 'PUT', headers: { 'X-Action-Confirmation': confirmation.confirmationToken },
      body: JSON.stringify({ appId: current.appId, key, expectedRevision: current.uploadKey.revision }),
    })
    if (generation !== keyGeneration) return
    if (!result.configured || result.appId !== current.appId || result.revision !== current.uploadKey.revision + 1) throw new Error('Unexpected result')
    keyConfirming.value = false
    keyNotice.value = '密钥已保存。保存只证明配置完成，微信是否接受密钥需在实际上传时验证。'
    await loadReadiness()
  } catch {
    if (generation === keyGeneration) {
      keyError.value = '密钥保存结果待核对。请重新读取状态后再操作。'
      keyNeedsRead.value = true
      keyConfirming.value = false
    }
  } finally {
    keyBusy.value = false
    keyPassword.value = ''
    clearKeyInput()
  }
}

function formatTime(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes: number) {
  return Number.isFinite(bytes) && bytes >= 0 ? `${(bytes / 1024 / 1024).toFixed(2)} MiB` : '—'
}

function storageLabel(status: string) {
  return ({ READY: '摘要已核实', STORED_UNVERIFIED: '文件存在，摘要待核实', UNAVAILABLE: '文件不可用' } as Record<string, string>)[status] || '状态待核实'
}

function jobLabel(status: string) {
  return ({ STARTED: '处理中', SUCCEEDED: '已同步到商城后台', FAILED: '失败' } as Record<string, string>)[status] || '状态待核实'
}

async function loadVersions(cursor = currentCursor.value) {
  const generation = ++versionGeneration
  versionLoading.value = true
  versionError.value = ''
  try {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    const page = await api<VersionPage>(`/code-versions${query}`)
    if (generation !== versionGeneration) return false
    versions.value = page.items
    nextCursor.value = page.nextCursor
    currentCursor.value = cursor
    return true
  } catch (reason) {
    if (generation === versionGeneration) versionError.value = reason instanceof Error ? reason.message : '代码版本读取失败。'
    return false
  } finally {
    if (generation === versionGeneration) versionLoading.value = false
  }
}

async function loadJobs(cursor = currentJobCursor.value) {
  const generation = ++jobGeneration
  jobLoading.value = true
  jobError.value = ''
  try {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    const result = await api<{ items: SyncJob[]; nextCursor: string | null }>(`/code-sync-jobs${query}`)
    if (generation !== jobGeneration) return false
    jobs.value = result.items
    nextJobCursor.value = result.nextCursor
    currentJobCursor.value = cursor
    return true
  } catch (reason) {
    if (generation === jobGeneration) jobError.value = reason instanceof Error ? reason.message : '同步记录读取失败。'
    return false
  } finally {
    if (generation === jobGeneration) jobLoading.value = false
  }
}

async function nextJobPage() {
  if (!nextJobCursor.value || jobLoading.value) return
  const previous = currentJobCursor.value
  if (await loadJobs(nextJobCursor.value)) previousJobCursors.value = [...previousJobCursors.value, previous]
}

async function previousJobPage() {
  if (!previousJobCursors.value.length || jobLoading.value) return
  const previous = previousJobCursors.value.at(-1) || ''
  if (await loadJobs(previous)) previousJobCursors.value = previousJobCursors.value.slice(0, -1)
}

function refresh() {
  void Promise.all([loadVersions(), loadJobs(), loadReadiness()])
  if (selectedId.value) void loadDetail(selectedId.value)
}

async function nextPage() {
  if (!nextCursor.value || versionLoading.value) return
  const previous = currentCursor.value
  if (await loadVersions(nextCursor.value)) previousCursors.value = [...previousCursors.value, previous]
}

async function previousPage() {
  if (!previousCursors.value.length || versionLoading.value) return
  const previous = previousCursors.value.at(-1) || ''
  if (await loadVersions(previous)) previousCursors.value = previousCursors.value.slice(0, -1)
}

async function loadDetail(id: string) {
  const generation = ++detailGeneration
  selectedId.value = id
  selected.value = null
  detailLoading.value = true
  detailError.value = ''
  try {
    const result = await api<CodeVersion>(`/code-versions/${id}`)
    if (generation === detailGeneration) selected.value = result
  } catch (reason) {
    if (generation === detailGeneration) detailError.value = reason instanceof Error ? reason.message : '版本详情读取失败。'
  } finally {
    if (generation === detailGeneration) detailLoading.value = false
  }
}

function closeDetail() {
  ++detailGeneration
  selectedId.value = ''
  selected.value = null
  detailError.value = ''
  detailLoading.value = false
}

onMounted(() => { void Promise.all([loadVersions(''), loadJobs(), loadReadiness()]) })
onUnmounted(() => { ++readinessGeneration; ++keyGeneration; keyPassword.value = ''; clearKeyInput() })
</script>

<template>
  <section class="page-content code-versions-page">
    <header class="page-heading">
      <div><h1>代码版本</h1><p>上传密钥，选择代码包，直接上传到微信开发版本。</p></div>
      <button class="secondary-button" type="button" :disabled="versionLoading || jobLoading" @click="refresh">重新读取</button>
    </header>

    <section class="code-release-readiness" aria-labelledby="code-release-readiness-title">
      <div class="code-versions-section-heading"><h2 id="code-release-readiness-title">上传准备</h2><span v-if="readiness?.appId">{{ readiness.appId }}</span></div>
      <p v-if="readinessLoading" role="status">正在检查发布条件…</p>
      <p v-if="readinessError" class="notice" role="alert">{{ readinessError }} <button class="text-button" type="button" @click="loadReadiness">重试</button></p>
      <template v-if="readiness && !readinessLoading && !readinessError">
        <ul class="code-release-checks"><li v-for="check in directChecks" :key="check.code" :class="`is-${check.status.toLowerCase()}`"><div><h3>{{ check.title }}</h3><span :class="`code-release-check-status is-${check.status.toLowerCase()}`">{{ check.code === 'UPLOAD_KEY' && check.status === 'PASS' ? '已保存' : checkLabel(check.status) }}</span></div><p v-if="check.status !== 'PASS'">{{ check.detail }}</p></li></ul>
        <p class="code-release-summary" role="status">已完成 {{ directChecks.filter(check => check.status === 'PASS').length }} / {{ directChecks.length }} 项本地准备检查。密钥与代码上传 IP 白名单将在实际上传时由微信验证。</p>
      </template>
    </section>

    <section v-if="canManageKey" class="code-release-key panel" aria-labelledby="code-release-key-title">
      <div class="code-versions-section-heading"><h2 id="code-release-key-title">代码上传密钥</h2><span>{{ readiness?.uploadKey.configured ? '已配置，可替换' : '尚未配置' }}</span></div>
      <p>在微信公众平台下载 .key 文件，在此加密保存。</p>
      <label for="code-upload-key-file">选择密钥文件</label>
      <input id="code-upload-key-file" ref="keyInput" type="file" accept=".key,text/plain" :disabled="keyBusy || keyNeedsRead || keyConfirming" @change="chooseKey">
      <p v-if="keyError" class="notice" role="alert">{{ keyError }}</p>
      <p v-if="keyNotice" class="code-release-key-notice" role="status">{{ keyNotice }}</p>
      <button data-test="begin-key-upload" class="secondary-button" type="button" :disabled="!keyFile || !readiness?.appId || keyBusy || keyNeedsRead || readinessLoading" @click="beginKeyUpload">上传代码密钥</button>
      <form v-if="keyConfirming" data-test="key-upload-form" class="code-release-key-confirm" @submit.prevent="uploadKey">
        <label for="code-upload-key-password">输入当前密码确认替换密钥</label>
        <input id="code-upload-key-password" v-model="keyPassword" data-test="key-password" type="password" autocomplete="current-password" :disabled="keyBusy">
        <div><button class="secondary-button" type="button" :disabled="keyBusy" @click="keyConfirming = false; keyPassword = ''">取消</button><button class="primary-button" type="submit" :disabled="!keyPassword || keyBusy">确认保存</button></div>
      </form>
    </section>

      <p v-if="versionError" class="notice" role="alert">{{ versionError }} <button class="text-button" type="button" @click="loadVersions()">重试</button></p>

    <ReleaseWorkflowPanel :readiness="readinessLoading || readinessError ? null : readiness" :versions="versions" :can-manage="canManageKey" :can-manage-platform="canManagePlatform" @refresh="refresh" />

    <details class="code-release-disclosure code-build-details"><summary>代码包与构建记录</summary>
    <section class="code-versions-section" aria-labelledby="code-versions-title">
      <div class="code-versions-section-heading"><h2 id="code-versions-title">不可变版本</h2><span>最近构建优先</span></div>
      <p v-if="versionLoading" class="loading-inline" role="status">正在读取代码版本…</p>
      <div v-if="!versionLoading && !versionError && !versions.length" class="panel code-versions-empty">暂无代码版本。部署流程完成首次构建与私有存储同步后，记录会出现在这里。</div>
      <div v-if="!versionLoading && !versionError && versions.length" class="panel code-versions-table-wrap">
        <table class="code-versions-table"><thead><tr><th scope="col">版本</th><th scope="col">内部存储</th><th scope="col">构建来源</th><th scope="col">包体积</th><th scope="col">完成时间</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="item in versions" :key="item.versionId"><th scope="row">{{ item.versionLabel }}</th><td><span :class="['code-versions-state', item.storageStatus === 'READY' ? 'is-ready' : item.storageStatus === 'STORED_UNVERIFIED' ? 'is-present' : 'is-warning']">{{ storageLabel(item.storageStatus) }}</span></td><td class="code-versions-hash">{{ item.sourceRevision || `SHA-256 ${item.sourceDigest.slice(0, 12)}…` }}</td><td>{{ formatSize(item.packageBytes) }}</td><td>{{ formatTime(item.completedAt) }}</td><td><button class="text-button" type="button" :aria-label="`查看版本 ${item.versionLabel} 详情`" @click="loadDetail(item.versionId)">查看详情</button></td></tr></tbody>
        </table>
      </div>
      <nav v-if="versions.length && !versionError" class="code-versions-pagination" aria-label="代码版本分页"><button class="secondary-button" type="button" :disabled="versionLoading || !previousCursors.length" @click="previousPage">上一页</button><button class="secondary-button" type="button" :disabled="versionLoading || !nextCursor" @click="nextPage">下一页</button></nav>
    </section>

    <section class="code-versions-section" aria-labelledby="code-jobs-title">
      <div class="code-versions-section-heading"><h2 id="code-jobs-title">同步记录</h2></div>
      <p v-if="jobError" class="notice" role="alert">{{ jobError }} <button class="text-button" type="button" @click="loadJobs()">重试</button></p>
      <p v-if="jobLoading" class="loading-inline" role="status">正在读取同步记录…</p>
      <div v-if="!jobLoading && !jobError && !jobs.length" class="panel code-versions-empty">暂无同步记录。构建由服务器部署流程发起，后台不接收本地上传包。</div>
      <ol v-if="!jobLoading && !jobError && jobs.length" class="code-jobs-list"><li v-for="job in jobs" :key="job.taskId" class="panel"><div><strong>{{ jobLabel(job.status) }}</strong><span>{{ formatTime(job.createdAt) }} → {{ formatTime(job.completedAt) }}</span></div><p v-if="job.failureCode" class="code-jobs-failure">失败代码：{{ job.failureCode }}。请由运维检查构建日志和持久存储后重试部署命令。</p><small>任务 {{ job.taskId }}</small></li></ol>
      <nav v-if="jobs.length && !jobError" class="code-versions-pagination" aria-label="同步记录分页"><button class="secondary-button" type="button" :disabled="jobLoading || !previousJobCursors.length" @click="previousJobPage">上一页</button><button class="secondary-button" type="button" :disabled="jobLoading || !nextJobCursor" @click="nextJobPage">下一页</button></nav>
    </section>
    </details>

    <el-drawer :model-value="!!selectedId" title="代码版本详情" size="min(540px, 94vw)" @close="closeDetail">
      <p v-if="detailLoading" role="status">正在读取版本详情…</p>
      <p v-if="detailError" class="notice" role="alert">{{ detailError }} <button class="text-button" type="button" @click="loadDetail(selectedId)">重试</button></p>
      <div v-if="selected && !detailLoading && !detailError" class="code-version-detail">
        <h2>{{ selected.versionLabel }}</h2>
        <p class="code-version-detail-status">{{ storageLabel(selected.storageStatus) }}</p>
        <dl><div><dt>版本 ID</dt><dd>{{ selected.versionId }}</dd></div><div><dt>来源修订</dt><dd>{{ selected.sourceRevision || '未证明' }}</dd></div><div><dt>源码摘要 SHA-256</dt><dd>{{ selected.sourceDigest }}</dd></div><div><dt>包摘要 SHA-256</dt><dd>{{ selected.packageSha256 }}</dd></div><div><dt>包体积</dt><dd>{{ formatSize(selected.packageBytes) }}</dd></div><div><dt>文件数</dt><dd>{{ selected.fileCount }}</dd></div><div><dt>创建时间</dt><dd>{{ formatTime(selected.createdAt) }}</dd></div><div><dt>完成时间</dt><dd>{{ formatTime(selected.completedAt) }}</dd></div><div v-if="selected.failureCode"><dt>失败代码</dt><dd>{{ selected.failureCode }}</dd></div></dl>
        <p class="code-version-detail-note">此版本只证明服务器源码包与商城后台私有存储记录。微信上传、审核和发布均须另外核验平台回执。</p>
      </div>
    </el-drawer>
  </section>
</template>
