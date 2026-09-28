<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, confirmedWrite, type Account } from '../api'
import { canRecover, EVENT_OPTIONS, eventLabel, reasonLabel, STATUS_OPTIONS, statusLabel, taskQuery } from './notifications/task-view.mjs'
import './notifications/tasks.css'

interface TaskSummary {
  taskId: string
  eventType: string
  status: string
  reasonCode: string
  attemptCount: number
  occurredAt: string
  createdAt: string
  updatedAt: string
  nextAttemptAt: string | null
  leaseUntil: string | null
  recoverable: boolean
}
interface Attempt {
  ordinal: number
  outcome: string
  failureCode: string
  startedAt: string
  finishedAt: string | null
}
interface TaskDetail extends TaskSummary { attempts: Attempt[] }
interface TaskPage { items: TaskSummary[]; nextCursor: string | null }
interface Filters { eventType: string; status: string; createdFrom: string; createdTo: string }

const props = defineProps<{ account: Account }>()
const canManageRecovery = computed(() => props.account.permissionCodes.includes('notification.recover'))
const filters = ref<Filters>({ eventType: '', status: '', createdFrom: '', createdTo: '' })
const appliedFilters = ref<Filters>({ ...filters.value })
const rows = ref<TaskSummary[]>([])
const nextCursor = ref<string | null>(null)
const currentCursor = ref('')
const previousCursors = ref<string[]>([])
const loading = ref(true)
const listError = ref('')
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const selected = ref<TaskDetail | null>(null)
const selectedId = ref('')
const recoveryOpen = ref(false)
const recoveryPassword = ref('')
const recovering = ref(false)
const recoveryError = ref('')
const recoveryNotice = ref('')
const uncertainTaskId = ref('')
let listGeneration = 0
let detailGeneration = 0

function formatTime(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

async function loadList(cursor = currentCursor.value) {
  const generation = ++listGeneration
  loading.value = true
  listError.value = ''
  try {
    const result = await api<TaskPage>(`/subscription-message-tasks${taskQuery(appliedFilters.value, cursor)}`)
    if (generation !== listGeneration) return false
    rows.value = result.items
    nextCursor.value = result.nextCursor
    currentCursor.value = cursor
    return true
  } catch (reason) {
    if (generation === listGeneration) listError.value = reason instanceof Error ? reason.message : '消息任务读取失败。'
    return false
  } finally {
    if (generation === listGeneration) loading.value = false
  }
}

function applyFilters() {
  appliedFilters.value = { ...filters.value }
  previousCursors.value = []
  void loadList('')
}

async function nextPage() {
  if (!nextCursor.value || loading.value) return
  const previous = currentCursor.value
  if (await loadList(nextCursor.value)) previousCursors.value = [...previousCursors.value, previous]
}

async function previousPage() {
  if (!previousCursors.value.length || loading.value) return
  const previous = previousCursors.value.at(-1) || ''
  if (await loadList(previous)) previousCursors.value = previousCursors.value.slice(0, -1)
}

async function loadDetail(taskId: string) {
  const generation = ++detailGeneration
  selectedId.value = taskId
  detailLoading.value = true
  detailError.value = ''
  try {
    const detail = await api<TaskDetail>(`/subscription-message-tasks/${taskId}`)
    if (generation !== detailGeneration) return
    selected.value = detail
    if (uncertainTaskId.value === taskId && (detail.status !== 'RESERVED' || !detail.recoverable)) {
      uncertainTaskId.value = ''
      recoveryNotice.value = '已从服务端核实最新任务状态。'
    }
  } catch (reason) {
    if (generation === detailGeneration) detailError.value = reason instanceof Error ? reason.message : '任务详情读取失败。'
  } finally {
    if (generation === detailGeneration) detailLoading.value = false
  }
}

function openDetail(taskId: string) {
  selected.value = null
  recoveryNotice.value = ''
  detailOpen.value = true
  void loadDetail(taskId)
}

function openRecovery() {
  if (!selected.value || !canRecover(selected.value, canManageRecovery.value) || uncertainTaskId.value === selected.value.taskId) return
  recoveryPassword.value = ''
  recoveryError.value = ''
  recoveryOpen.value = true
}

async function recoverReservation() {
  const task = selected.value
  if (!task || !canRecover(task, canManageRecovery.value) || !recoveryPassword.value || recovering.value) return
  recovering.value = true
  recoveryError.value = ''
  try {
    await confirmedWrite<TaskSummary>(
      'notification.task.recover_reservation', recoveryPassword.value,
      `/subscription-message-tasks/${task.taskId}/recover-reservation`, 'POST',
      { expectedUpdatedAt: task.updatedAt, expectedAttemptCount: task.attemptCount },
      task.taskId, task.attemptCount,
    )
    recoveryOpen.value = false
    recoveryNotice.value = '预留已恢复为待处理；本次操作没有发送消息。'
    await Promise.all([loadDetail(task.taskId), loadList()])
  } catch (reason) {
    if (reason instanceof ApiError && [400, 401, 403, 404, 409, 429].includes(reason.status)) {
      recoveryError.value = reason.message
      if (reason.status === 409) {
        recoveryOpen.value = false
        await Promise.all([loadDetail(task.taskId), loadList()])
      }
    } else {
      uncertainTaskId.value = task.taskId
      recoveryOpen.value = false
      recoveryNotice.value = '恢复结果未知。请重新读取任务状态；在核实前不会再次提交。'
      await Promise.allSettled([loadDetail(task.taskId), loadList()])
    }
  } finally {
    recoveryPassword.value = ''
    recovering.value = false
  }
}

onMounted(() => { void loadList('') })
</script>

<template>
  <section class="page-content message-tasks-page">
    <header class="page-heading">
      <div><p class="message-tasks-eyebrow">订阅消息 · 任务状态</p><h1>消息任务</h1><p>查询事件决策与发送尝试。模拟完成仅用于本机验证，不代表微信接收。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="loadList()">重新读取</button>
    </header>

    <div class="message-tasks-gate" role="status"><strong>真实发送未开通</strong><span>结果不明的任务标记为“待核验”，不会自动重发。只有本次预留尚未开始发送、租约已过期的任务可人工恢复。</span></div>

    <form class="panel message-tasks-filters" aria-label="筛选消息任务" @submit.prevent="applyFilters">
      <label>业务事件<select v-model="filters.eventType"><option value="">全部事件</option><option v-for="[code, title] in EVENT_OPTIONS" :key="code" :value="code">{{ title }}</option></select></label>
      <label>任务状态<select v-model="filters.status"><option value="">全部状态</option><option v-for="[code, title] in STATUS_OPTIONS" :key="code" :value="code">{{ title }}</option></select></label>
      <label>创建起始日<input v-model="filters.createdFrom" type="date"></label>
      <label>创建截止日<input v-model="filters.createdTo" type="date"></label>
      <button class="primary-button" type="submit" :disabled="loading">查询</button>
    </form>
    <p class="message-tasks-scope">默认显示最近 90 天，每页最多 20 条。日期按管理台时区筛选。</p>
    <p v-if="listError" class="notice" role="alert">{{ listError }} <button class="text-button" type="button" @click="loadList()">重试</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在读取消息任务…</p>
    <div v-if="!loading && !listError && !rows.length" class="panel message-tasks-empty">暂无匹配的消息任务。当前模板草稿不会产生可发送任务，可调整筛选后重试。</div>
    <div v-if="!loading && !listError && rows.length" class="message-tasks-list">
      <article v-for="item in rows" :key="item.taskId" class="panel message-task-card">
        <div class="message-task-primary"><div><p class="message-task-event">{{ eventLabel(item.eventType) }}</p><p class="message-task-date">事件 {{ formatTime(item.occurredAt) }} · 创建 {{ formatTime(item.createdAt) }}</p></div><span :class="['message-task-state', `message-task-state--${item.status.toLowerCase()}`]">{{ statusLabel(item.status) }}</span></div>
        <p v-if="item.reasonCode || item.attemptCount" class="message-task-reason"><span v-if="item.reasonCode">{{ reasonLabel(item.reasonCode) }}</span><span v-if="item.attemptCount">{{ item.reasonCode ? ' · ' : '' }}{{ item.attemptCount }} 次尝试</span></p>
        <div class="message-task-footer"><span class="message-task-id">任务 {{ item.taskId }}</span><button class="text-button" type="button" @click="openDetail(item.taskId)">查看详情</button></div>
      </article>
      <nav class="message-tasks-pagination" aria-label="消息任务分页"><button class="secondary-button" type="button" :disabled="loading || !previousCursors.length" @click="previousPage">上一页</button><span>每页 20 条</span><button class="secondary-button" type="button" :disabled="loading || !nextCursor" @click="nextPage">下一页</button></nav>
    </div>

    <el-dialog v-model="detailOpen" title="消息任务详情" width="min(680px, 94vw)" destroy-on-close @closed="detailGeneration++">
      <p v-if="detailLoading" role="status">正在读取任务详情…</p>
      <p v-if="detailError" class="message-task-error" role="alert">{{ detailError }} <button class="text-button" type="button" @click="loadDetail(selectedId)">重试</button></p>
      <div v-if="selected && !detailLoading && !detailError" class="message-task-detail">
        <div class="message-task-detail-heading"><strong>{{ eventLabel(selected.eventType) }}</strong><span :class="['message-task-state', `message-task-state--${selected.status.toLowerCase()}`]">{{ statusLabel(selected.status) }}</span></div>
        <dl><div><dt>任务 ID</dt><dd class="message-task-id">{{ selected.taskId }}</dd></div><div><dt>状态原因</dt><dd>{{ reasonLabel(selected.reasonCode) }}</dd></div><div><dt>业务发生</dt><dd>{{ formatTime(selected.occurredAt) }}</dd></div><div><dt>最近更新</dt><dd>{{ formatTime(selected.updatedAt) }}</dd></div><div v-if="selected.nextAttemptAt"><dt>计划重试</dt><dd>{{ formatTime(selected.nextAttemptAt) }}</dd></div><div v-if="selected.leaseUntil"><dt>预留到期</dt><dd>{{ formatTime(selected.leaseUntil) }}</dd></div></dl>
        <h3>发送尝试</h3>
        <p v-if="!selected.attempts.length" class="message-task-muted">尚无发送尝试。</p>
        <ol v-else class="message-task-attempts"><li v-for="attempt in selected.attempts" :key="attempt.ordinal"><strong>第 {{ attempt.ordinal }} 次 · {{ attempt.outcome === 'SIMULATED' ? '仅模拟完成' : attempt.outcome === 'UNKNOWN' ? '结果待核验' : attempt.outcome }}</strong><span>{{ formatTime(attempt.startedAt) }} — {{ formatTime(attempt.finishedAt) }}</span><span v-if="attempt.failureCode">{{ reasonLabel(attempt.failureCode) }}</span></li></ol>
        <p v-if="selected.status === 'UNKNOWN'" class="message-task-warning" role="status">结果可能已经到达渠道。请先核验平台记录；这里不提供重发或改为成功的操作。</p>
        <p v-if="recoveryNotice" class="message-task-info" role="status">{{ recoveryNotice }}</p>
        <div class="message-task-detail-actions"><button class="secondary-button" type="button" :disabled="detailLoading" @click="loadDetail(selected.taskId)">重新读取状态</button><button v-if="canRecover(selected, canManageRecovery) && uncertainTaskId !== selected.taskId" class="primary-button" type="button" @click="openRecovery">恢复过期预留</button></div>
        <p v-if="selected.status === 'RESERVED' && !canManageRecovery" class="message-task-muted">当前账号没有消息任务恢复权限。</p>
      </div>
    </el-dialog>

    <el-dialog v-model="recoveryOpen" title="确认恢复过期预留" width="min(480px, 94vw)" append-to-body :close-on-click-modal="!recovering" :close-on-press-escape="!recovering">
      <p>仅将这条本次预留尚未开始发送的任务恢复为“待处理”。操作本身不会发送消息；后续工作进程仍会重新校验模板和授权。</p>
      <p class="message-task-id">{{ selected?.taskId }}</p>
      <label class="message-task-password">当前登录密码<el-input v-model="recoveryPassword" type="password" autocomplete="current-password" show-password placeholder="输入密码完成二次确认" :disabled="recovering" @keyup.enter="recoverReservation" /></label>
      <p v-if="recoveryError" class="message-task-error" role="alert">{{ recoveryError }}</p>
      <template #footer><button class="secondary-button" type="button" :disabled="recovering" @click="recoveryOpen = false">取消</button><button class="primary-button" type="button" :disabled="recovering || !recoveryPassword" @click="recoverReservation">{{ recovering ? '正在核验…' : '确认恢复' }}</button></template>
    </el-dialog>
  </section>
</template>
