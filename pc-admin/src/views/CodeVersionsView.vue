<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
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
let versionGeneration = 0
let jobGeneration = 0
let detailGeneration = 0

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
  void Promise.all([loadVersions(), loadJobs()])
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

onMounted(() => { void Promise.all([loadVersions(''), loadJobs()]) })
</script>

<template>
  <section class="page-content code-versions-page">
    <header class="page-heading">
      <div><h1>代码版本</h1><p>查看服务器构建的小程序源码快照，以及同步到商城后台私有存储的结果。</p></div>
      <button class="secondary-button" type="button" :disabled="versionLoading || jobLoading" @click="refresh">重新读取</button>
    </header>

    <div class="code-versions-gate" role="status"><strong>微信平台尚未接入</strong><span>这里的“已同步”只表示包已保存到商城后台的私有存储。当前没有真实小程序账号，不能据此预览、提审或发布。</span></div>

    <section class="code-versions-section" aria-labelledby="code-versions-title">
      <div class="code-versions-section-heading"><h2 id="code-versions-title">不可变版本</h2><span>最近构建优先</span></div>
      <p v-if="versionError" class="notice" role="alert">{{ versionError }} <button class="text-button" type="button" @click="loadVersions()">重试</button></p>
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
      <div class="code-versions-section-heading"><h2 id="code-jobs-title">同步记录</h2><span>失败不会产生可提审版本</span></div>
      <p v-if="jobError" class="notice" role="alert">{{ jobError }} <button class="text-button" type="button" @click="loadJobs()">重试</button></p>
      <p v-if="jobLoading" class="loading-inline" role="status">正在读取同步记录…</p>
      <div v-if="!jobLoading && !jobError && !jobs.length" class="panel code-versions-empty">暂无同步记录。构建由服务器部署流程发起，后台不接收本地上传包。</div>
      <ol v-if="!jobLoading && !jobError && jobs.length" class="code-jobs-list"><li v-for="job in jobs" :key="job.taskId" class="panel"><div><strong>{{ jobLabel(job.status) }}</strong><span>{{ formatTime(job.createdAt) }} → {{ formatTime(job.completedAt) }}</span></div><p v-if="job.failureCode" class="code-jobs-failure">失败代码：{{ job.failureCode }}。请由运维检查构建日志和持久存储后重试部署命令。</p><small>任务 {{ job.taskId }}</small></li></ol>
      <nav v-if="jobs.length && !jobError" class="code-versions-pagination" aria-label="同步记录分页"><button class="secondary-button" type="button" :disabled="jobLoading || !previousJobCursors.length" @click="previousJobPage">上一页</button><button class="secondary-button" type="button" :disabled="jobLoading || !nextJobCursor" @click="nextJobPage">下一页</button></nav>
    </section>

    <el-drawer :model-value="!!selectedId" title="代码版本详情" size="min(540px, 94vw)" @close="closeDetail">
      <p v-if="detailLoading" role="status">正在读取版本详情…</p>
      <p v-if="detailError" class="notice" role="alert">{{ detailError }} <button class="text-button" type="button" @click="loadDetail(selectedId)">重试</button></p>
      <div v-if="selected && !detailLoading && !detailError" class="code-version-detail">
        <h2>{{ selected.versionLabel }}</h2>
        <p class="code-version-detail-status">{{ storageLabel(selected.storageStatus) }} · 微信平台{{ selected.platformStatus === 'NOT_CONFIGURED' ? '未配置' : '状态待核实' }}</p>
        <dl><div><dt>版本 ID</dt><dd>{{ selected.versionId }}</dd></div><div><dt>来源修订</dt><dd>{{ selected.sourceRevision || '未证明' }}</dd></div><div><dt>源码摘要 SHA-256</dt><dd>{{ selected.sourceDigest }}</dd></div><div><dt>包摘要 SHA-256</dt><dd>{{ selected.packageSha256 }}</dd></div><div><dt>包体积</dt><dd>{{ formatSize(selected.packageBytes) }}</dd></div><div><dt>文件数</dt><dd>{{ selected.fileCount }}</dd></div><div><dt>创建时间</dt><dd>{{ formatTime(selected.createdAt) }}</dd></div><div><dt>完成时间</dt><dd>{{ formatTime(selected.completedAt) }}</dd></div><div v-if="selected.failureCode"><dt>失败代码</dt><dd>{{ selected.failureCode }}</dd></div></dl>
        <p class="code-version-detail-note">此版本只证明服务器源码包与商城后台私有存储记录。微信上传、审核和发布均须另外核验平台回执。</p>
      </div>
    </el-drawer>
  </section>
</template>
