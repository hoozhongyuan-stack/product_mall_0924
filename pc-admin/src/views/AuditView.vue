<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AuditEntry } from '../api'

interface AuditPage { items: AuditEntry[]; nextCursor: string | null; from: string; to: string; timeZone: string }
interface Filters { from: string; to: string; actorId: string; actionCode: string; objectType: string; objectId: string; result: string }
const blank = (): Filters => ({ from: '', to: '', actorId: '', actionCode: '', objectType: '', objectId: '', result: '' })
const filters = ref<Filters>(blank())
const applied = ref<Filters>(blank())
const logs = ref<AuditEntry[]>([])
const range = ref('默认近 90 天')
const loading = ref(true)
const error = ref('')
const cursor = ref('')
const nextCursor = ref<string | null>(null)
const previous = ref<string[]>([])
let generation = 0

async function load(next = cursor.value) {
  const current = ++generation
  loading.value = true
  error.value = ''
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(applied.value)) if (value) query.set(key, value)
  if (next) query.set('cursor', next)
  try {
    const page = await api<AuditPage>(`/audit-logs?${query}`)
    if (current !== generation) return false
    logs.value = page.items
    nextCursor.value = page.nextCursor
    cursor.value = next
    range.value = `${page.from} 至 ${page.to} · ${page.timeZone}`
    return true
  } catch (reason) {
    if (current === generation) {
      logs.value = []
      nextCursor.value = null
      error.value = reason instanceof Error ? reason.message : '日志读取失败。'
    }
    return false
  } finally {
    if (current === generation) loading.value = false
  }
}

function apply() {
  applied.value = { ...filters.value }
  previous.value = []
  void load('')
}
function reset() {
  filters.value = blank()
  apply()
}
async function forward() {
  if (!nextCursor.value || loading.value) return
  const old = cursor.value
  if (await load(nextCursor.value)) previous.value = [...previous.value, old]
}
async function back() {
  if (!previous.value.length || loading.value) return
  const old = previous.value.at(-1) || ''
  if (await load(old)) previous.value = previous.value.slice(0, -1)
}
onMounted(() => { void load('') })

function timestamp(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}
const resultText: Record<string, string> = { SUCCESS: '成功', DENIED: '已拒绝', FAILED: '失败' }
</script>

<template>
  <section class="page-content audit-page">
    <header class="page-heading"><div><h1>操作日志</h1><p>按操作时间筛选；前后值由服务端脱敏后展示。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load()">重新读取</button></header>
    <form class="panel audit-filters" aria-label="筛选操作日志" @submit.prevent="apply">
      <label>开始日期<input v-model="filters.from" type="date" /></label>
      <label>结束日期<input v-model="filters.to" type="date" /></label>
      <label>操作者 ID<input v-model.trim="filters.actorId" placeholder="完整 UUID" /></label>
      <label>动作代码<input v-model.trim="filters.actionCode" placeholder="如 catalog.update" /></label>
      <label>对象类型<input v-model.trim="filters.objectType" placeholder="如 product" /></label>
      <label>对象 ID<input v-model.trim="filters.objectId" placeholder="完整对象 ID" /></label>
      <label>结果<select v-model="filters.result"><option value="">全部结果</option><option value="SUCCESS">成功</option><option value="DENIED">已拒绝</option><option value="FAILED">失败</option></select></label>
      <div class="audit-actions"><button class="primary-button" type="submit" :disabled="loading">查询</button><button class="secondary-button" type="button" :disabled="loading" @click="reset">重置</button></div>
    </form>
    <p class="audit-range">{{ range }} · 每页 20 条，按时间倒序</p>
    <p v-if="error" class="error notice" role="alert">{{ error }} <button class="text-button" type="button" @click="load()">重试</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在查询操作日志…</p>
    <div v-else-if="!error" class="panel table-wrap" tabindex="0" aria-label="操作日志列表，可横向滚动">
      <table><thead><tr><th scope="col">时间</th><th scope="col">操作</th><th scope="col">结果</th><th scope="col">操作者 ID</th><th scope="col">对象</th><th scope="col">详情</th></tr></thead>
        <tbody><tr v-for="item in logs" :key="item.id">
          <td>{{ timestamp(item.occurredAt) }}</td><td class="strong-cell">{{ item.actionCode }}</td>
          <td><span :class="['badge', item.result === 'SUCCESS' ? 'badge-good' : 'badge-muted']">{{ resultText[item.result] || item.result }}</span></td>
          <td class="code">{{ item.actorId || '未识别' }}</td>
          <td>{{ item.objectType }}<br /><span class="code">{{ item.objectId || '—' }}</span></td>
          <td><details><summary>查看脱敏详情</summary><pre>{{ JSON.stringify({ before: item.before, after: item.after, requestId: item.requestId }, null, 2) }}</pre></details></td>
        </tr></tbody></table>
      <p v-if="!logs.length" class="empty-state">当前条件下没有操作日志。可调整日期或清除筛选。</p>
    </div>
    <div v-if="!error && !loading" class="audit-pages"><span>第 {{ previous.length + 1 }} 页</span><div><button class="secondary-button" :disabled="!previous.length" @click="back">上一页</button><button class="secondary-button" :disabled="!nextCursor" @click="forward">下一页</button></div></div>
  </section>
</template>

<style scoped>
.audit-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;padding:20px;margin-bottom:12px}
.audit-filters label{display:grid;gap:6px;font-size:13px;font-weight:650;color:#51695a}
.audit-filters input,.audit-filters select{min-width:0;width:100%;min-height:42px;border:1px solid #cbd8ce;border-radius:9px;padding:8px 10px;background:#fff;color:#243329}
.audit-filters :is(input,select):focus-visible{outline:2px solid #328055;outline-offset:2px}
.audit-actions{display:flex;align-items:end;gap:8px}.audit-range{margin:8px 0 18px;font-size:13px}
.audit-pages{display:flex;justify-content:space-between;align-items:center;margin:16px 0;color:#51695a;font-size:13px}.audit-pages>div{display:flex;gap:8px}
.audit-page pre{max-width:45ch;white-space:pre-wrap;overflow-wrap:anywhere}.audit-page summary{cursor:pointer;color:#245b3d}
@media(max-width:850px){.audit-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:540px){.audit-filters{grid-template-columns:1fr}.audit-pages{align-items:start;gap:10px;flex-direction:column}}
</style>
