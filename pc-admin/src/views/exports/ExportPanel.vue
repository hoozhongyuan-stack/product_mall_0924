<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../../api'
import { downloadExportCsv, type ExportFilters, type ExportTask } from './client'
import { canDownloadExport, exportStatusText, isDefinitiveExportError, type ExportKind } from './task.mjs'

interface ExportPage { items: ExportTask[]; nextCursor: string | null }
interface PendingSubmission { requestKey: string; filters: ExportFilters }
const props = defineProps<{ kind: ExportKind; filters: ExportFilters | null; actorId: string }>()
const rows = ref<ExportTask[]>([])
const loading = ref(true)
const listError = ref('')
const nextCursor = ref<string | null>(null)
const currentCursor = ref('')
const previousCursors = ref<string[]>([])
const submitting = ref(false)
const downloadingId = ref('')
const actionError = ref('')
const notice = ref('')
const pending = ref<PendingSubmission | null>(null)
const now = ref(Date.now())
let generation = 0
let expiryTimer: number | undefined

const storageKey = `mall:e41:pending-export:${props.actorId}:${props.kind}`
const kindLabel = props.kind === 'AUDIT' ? '操作日志' : '经营统计'
function formatTime(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}
function formatSize(value: number | null) {
  return Number.isSafeInteger(value) && value !== null && value >= 0 ? `${(value / 1024).toFixed(1)} KiB` : '—'
}
function savePending(value: PendingSubmission | null) {
  pending.value = value
  try {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value))
    else sessionStorage.removeItem(storageKey)
  } catch { /* Private browsing may disable session storage; the in-memory key remains usable. */ }
}
function restorePending() {
  try {
    const raw = sessionStorage.getItem(storageKey)
    if (!raw) return
    const value = JSON.parse(raw) as PendingSubmission
    if (/^[\da-f-]{36}$/i.test(value.requestKey) && /^\d{4}-\d{2}-\d{2}$/.test(value.filters?.from) && /^\d{4}-\d{2}-\d{2}$/.test(value.filters?.to)) {
      pending.value = value
    } else sessionStorage.removeItem(storageKey)
  } catch { /* Ignore malformed local state; the server remains authoritative. */ }
}
async function loadTasks(cursor = currentCursor.value) {
  const current = ++generation
  loading.value = true
  listError.value = ''
  const query = new URLSearchParams({ kind: props.kind })
  if (cursor) query.set('cursor', cursor)
  try {
    const page = await api<ExportPage>(`/exports?${query}`)
    if (current !== generation) return false
    rows.value = page.items
    nextCursor.value = page.nextCursor
    currentCursor.value = cursor
    if (pending.value && page.items.some(item => item.requestKey === pending.value?.requestKey)) {
      savePending(null)
      notice.value = '已核实上次提交，任务已出现在列表中。'
    }
    return true
  } catch (reason) {
    if (current === generation) listError.value = reason instanceof Error ? reason.message : '导出任务读取失败。'
    return false
  } finally {
    if (current === generation) loading.value = false
  }
}
async function nextPage() {
  if (!nextCursor.value || loading.value) return
  const old = currentCursor.value
  if (await loadTasks(nextCursor.value)) previousCursors.value = [...previousCursors.value, old]
}
async function previousPage() {
  if (!previousCursors.value.length || loading.value) return
  const old = previousCursors.value.at(-1) || ''
  if (await loadTasks(old)) previousCursors.value = previousCursors.value.slice(0, -1)
}
async function createExport() {
  if (submitting.value || (!pending.value && !props.filters)) return
  actionError.value = ''
  notice.value = ''
  const submission = pending.value || { requestKey: crypto.randomUUID(), filters: { ...props.filters! } }
  savePending(submission)
  submitting.value = true
  try {
    await api<ExportTask>('/exports', {
      method: 'POST',
      body: JSON.stringify({ kind: props.kind, filters: submission.filters, requestKey: submission.requestKey }),
    })
    savePending(null)
    notice.value = '已创建导出任务。文件生成后可在下方下载。'
    previousCursors.value = []
    await loadTasks('')
  } catch (reason) {
    if (reason instanceof ApiError && isDefinitiveExportError(reason.status)) {
      savePending(null)
      actionError.value = reason.message
    } else {
      notice.value = '提交结果尚未核实。保留了原请求号；可先刷新任务，或用原请求号安全重试。'
      await loadTasks('')
    }
  } finally {
    submitting.value = false
  }
}
async function download(task: ExportTask) {
  if (!canDownloadExport(task, now.value) || downloadingId.value) return
  downloadingId.value = task.taskId
  actionError.value = ''
  try {
    await downloadExportCsv(props.kind, task.taskId)
  } catch (reason) {
    actionError.value = reason instanceof Error ? reason.message : '下载失败，请刷新任务状态后重试。'
    if (reason instanceof ApiError && [403, 404, 409, 410].includes(reason.status)) void loadTasks()
  } finally {
    downloadingId.value = ''
  }
}
onMounted(() => {
  restorePending()
  void loadTasks('')
  expiryTimer = window.setInterval(() => { now.value = Date.now() }, 60_000)
})
onUnmounted(() => { generation++; window.clearInterval(expiryTimer) })
</script>

<template>
  <section class="export-section" :aria-label="`${kindLabel}导出`">
    <div class="export-heading"><div><h2>导出任务</h2><p>按当前已查询的条件生成 CSV。文件仅保留 24 小时，下载时仍会核验权限。</p></div>
      <div class="export-heading-actions"><button class="secondary-button" type="button" :disabled="loading || submitting" @click="loadTasks()">刷新任务</button>
        <button class="primary-button" type="button" :disabled="submitting || (!pending && !filters)" @click="createExport">{{ submitting ? '正在提交…' : pending ? '用原请求号重试' : '创建导出' }}</button></div></div>
    <p v-if="pending" class="export-warning" role="status">上次提交结果待核实：{{ pending.filters.from }} 至 {{ pending.filters.to }}。重试会沿用原条件与请求号。</p>
    <p v-if="notice" class="export-info" role="status">{{ notice }}</p>
    <p v-if="actionError" class="notice" role="alert">{{ actionError }}</p>
    <p v-if="listError" class="notice" role="alert">{{ listError }} <button class="text-button" type="button" @click="loadTasks()">重试读取</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在读取导出任务…</p>
    <div v-else-if="!listError && !rows.length" class="panel export-empty">暂无{{ kindLabel }}导出任务。查询所需范围后，可在这里创建文件。</div>
    <div v-else-if="!listError" class="panel export-table-wrap" tabindex="0" :aria-label="`${kindLabel}导出任务，可横向滚动`">
      <table><thead><tr><th scope="col">创建时间</th><th scope="col">范围</th><th scope="col">状态</th><th scope="col">结果</th><th scope="col">可下载至</th><th scope="col">操作</th></tr></thead>
        <tbody><tr v-for="task in rows" :key="task.taskId"><td>{{ formatTime(task.createdAt) }}<small class="export-task-id">{{ task.taskId }}</small></td>
          <td>{{ task.filters.from }} 至 {{ task.filters.to }}</td><td><span :class="['export-status', `is-${task.status === 'READY' && !canDownloadExport(task, now) ? 'expired' : task.status.toLowerCase()}`]">{{ task.status === 'READY' && !canDownloadExport(task, now) ? '已过期' : exportStatusText(task.status) }}</span></td>
          <td><template v-if="task.status === 'READY'">{{ task.rowCount ?? '—' }} 行 · {{ formatSize(task.fileBytes) }}</template><template v-else-if="task.status === 'FAILED'">失败代码：{{ task.failureCode || '待核实' }}</template><template v-else>—</template></td>
          <td>{{ formatTime(task.expiresAt) }}</td><td><button class="text-button" type="button" :disabled="!canDownloadExport(task, now) || !!downloadingId" @click="download(task)">{{ downloadingId === task.taskId ? '下载中…' : '下载 CSV' }}</button></td></tr></tbody></table>
    </div>
    <nav v-if="!listError && rows.length" class="export-pagination" aria-label="导出任务分页"><button class="secondary-button" type="button" :disabled="loading || !previousCursors.length" @click="previousPage">上一页</button><button class="secondary-button" type="button" :disabled="loading || !nextCursor" @click="nextPage">下一页</button></nav>
  </section>
</template>

<style scoped>
.export-section{margin-top:34px}.export-heading{display:flex;align-items:start;justify-content:space-between;gap:18px;margin-bottom:16px}.export-heading h2{font-size:20px;margin:0 0 6px}.export-heading p{font-size:13px;margin:0;max-width:70ch}.export-heading-actions{display:flex;gap:8px;flex-wrap:wrap}.export-warning,.export-info{padding:12px 16px;border-radius:9px;font-size:13px}.export-warning{color:#754709;background:#fff5e5}.export-info{color:var(--mall-color-success);background:#e6f4e8}.export-empty{padding:24px;font-size:14px;color:var(--mall-color-muted)}.export-table-wrap{overflow-x:auto}.export-table-wrap table{min-width:820px}.export-task-id{display:block;margin-top:5px;color:var(--mall-color-muted);font-size:11px;overflow-wrap:anywhere;max-width:230px}.export-status{font-weight:650;color:var(--mall-color-muted)}.export-status.is-ready{color:var(--mall-color-success)}.export-status.is-failed,.export-status.is-expired{color:var(--mall-color-danger)}.export-pagination{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}.export-section :is(button,a):focus-visible{outline:2px solid var(--mall-color-brand);outline-offset:2px}.export-section button:disabled{cursor:not-allowed;opacity:.6}.export-section td{font-variant-numeric:tabular-nums}
@media(max-width:600px){.export-heading{flex-direction:column}.export-heading-actions{width:100%}.export-heading-actions button{flex:1}}
</style>
