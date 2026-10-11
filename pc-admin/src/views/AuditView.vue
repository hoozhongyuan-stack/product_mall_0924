<script setup lang="ts">
import AdminCursorPager from '../shared/list/AdminCursorPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../shared/list/AdminSelectedRowsDialog.vue'
import { useCurrentPageSelection } from '../shared/list/useCurrentPageSelection'
import { computed, onMounted, ref } from 'vue'
import { api, type Account, type AuditEntry } from '../api'
import ExportPanel from './exports/ExportPanel.vue'
import type { ExportFilters } from './exports/client'

interface AuditPage { items: AuditEntry[]; nextCursor: string | null; from: string; to: string; timeZone: string }
interface Filters { from: string; to: string; actorId: string; actionCode: string; objectType: string; objectId: string; result: string }
const blank = (): Filters => ({ from: '', to: '', actorId: '', actionCode: '', objectType: '', objectId: '', result: '' })
const filters = ref<Filters>(blank())
const applied = ref<Filters>(blank())
const logs = ref<AuditEntry[]>([])
const selection = useCurrentPageSelection(()=>logs.value,row=>row.id)
const pageSize = ref(20), compareOpen = ref(false), selectedAudit = ref<AuditEntry|null>(null)
const compareColumns = [{key:'time',label:'时间'},{key:'action',label:'动作'},{key:'result',label:'结果'},{key:'actor',label:'操作者'},{key:'object',label:'对象'}]
const compareRows = computed(()=>selection.selectedRows.value.map(row=>({time:timestamp(row.occurredAt),action:row.actionCode,result:resultText[row.result]||row.result,actor:row.actorId,object:`${row.objectType} ${row.objectId}`})))
function changeSize(size:number) {pageSize.value=size;previous.value=[];void load('')}
const range = ref('默认近 90 天')
const loading = ref(true)
const error = ref('')
const cursor = ref('')
const nextCursor = ref<string | null>(null)
const previous = ref<string[]>([])
const loadedRange = ref<{ from: string; to: string } | null>(null)
const props = defineProps<{ account: Account }>()
const canExport = computed(() => props.account.permissionCodes.includes('audit.export'))
const exportFilters = computed<ExportFilters | null>(() => {
  if (!loadedRange.value || loading.value || error.value) return null
  const active = applied.value
  return {
    from: loadedRange.value.from, to: loadedRange.value.to,
    ...(active.actorId && { actorId: active.actorId }),
    ...(active.actionCode && { actionCode: active.actionCode }),
    ...(active.objectType && { objectType: active.objectType }),
    ...(active.objectId && { objectId: active.objectId }),
    ...(active.result && { result: active.result }),
  }
})
let generation = 0

async function load(next = cursor.value) {
  const current = ++generation
  loading.value = true
  selection.clear()
  error.value = ''
  const query = new URLSearchParams({limit:String(pageSize.value)})
  for (const [key, value] of Object.entries(applied.value)) if (value) query.set(key, value)
  if (next) query.set('cursor', next)
  try {
    const page = await api<AuditPage>(`/audit-logs?${query}`)
    if (current !== generation) return false
    logs.value = page.items
    nextCursor.value = page.nextCursor
    cursor.value = next
    range.value = `${page.from} 至 ${page.to} · ${page.timeZone}`
    loadedRange.value = { from: page.from, to: page.to }
    return true
  } catch (reason) {
    if (current === generation) {
      logs.value = []
      nextCursor.value = null
      loadedRange.value = null
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
  loadedRange.value = null
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
  <section class="page-content audit-page admin-list-page">
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
    <p class="audit-range">{{ range }} · 按时间倒序</p>
    <p v-if="error" class="error notice" role="alert">{{ error }} <button class="text-button" type="button" @click="load()">重试</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在查询操作日志…</p>
    <AdminSelectionBar v-if="!loading && !error" :count="selection.selectedIds.value.length" @clear="selection.clear"><button class="secondary-button" :disabled="!selection.selectedIds.value.length" @click="compareOpen=true">核对所选</button></AdminSelectionBar>
    <div v-if="!loading && !error" class="panel table-wrap admin-list-table" tabindex="0" aria-label="操作日志列表，可横向滚动">
      <table><thead><tr><th scope="col"><input type="checkbox" :checked="selection.allSelected.value" :indeterminate="selection.indeterminate.value" aria-label="选择当前页日志" @change="selection.togglePage(($event.target as HTMLInputElement).checked)"></th><th scope="col">时间</th><th scope="col">操作</th><th scope="col">结果</th><th scope="col">操作者 ID</th><th scope="col">对象</th><th scope="col">详情</th></tr></thead>
        <tbody><tr v-for="item in logs" :key="item.id">
          <td><input type="checkbox" :checked="selection.selectedIds.value.includes(item.id)" :aria-label="`选择日志 ${item.id}`" @change="selection.toggle(item.id,($event.target as HTMLInputElement).checked)"></td><td>{{ timestamp(item.occurredAt) }}</td><td class="strong-cell">{{ item.actionCode }}</td>
          <td><AdminStatusBadge :status="item.result" :label="resultText[item.result] || item.result"/></td>
          <td class="code">{{ item.actorId || '未识别' }}</td>
          <td>{{ item.objectType }}<br /><span class="code">{{ item.objectId || '—' }}</span></td>
          <td><button class="text-button" @click="selectedAudit=item">查看脱敏详情</button></td>
        </tr></tbody></table>
      <p v-if="!logs.length" class="empty-state">当前条件下没有操作日志。可调整日期或清除筛选。</p>
    </div>
    <AdminCursorPager :page="previous.length+1" :page-size="pageSize" :has-previous="Boolean(previous.length)" :has-next="Boolean(nextCursor)" :loading="loading" @previous="back" @next="forward" @size="changeSize"/>
    <AdminSelectedRowsDialog v-model="compareOpen" title="核对所选日志" :columns="compareColumns" :rows="compareRows"/>
    <el-dialog :model-value="Boolean(selectedAudit)" title="日志脱敏详情" width="760px" :close-on-click-modal="false" @update:model-value="(value:boolean)=>{if(!value)selectedAudit=null}"><pre v-if="selectedAudit" style="max-width:100%">{{JSON.stringify({before:selectedAudit.before,after:selectedAudit.after,requestId:selectedAudit.requestId},null,2)}}</pre><template #footer><button class="secondary-button" @click="selectedAudit=null">关闭</button></template></el-dialog>

    <ExportPanel v-if="canExport" kind="AUDIT" :filters="exportFilters" :actor-id="account.accountId" />
  </section>
</template>

<style scoped>
.audit-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;padding:20px;margin-bottom:12px}
.audit-filters label{display:grid;gap:6px;font-size:13px;font-weight:650;color:var(--mall-color-muted)}
.audit-filters input,.audit-filters select{min-width:0;width:100%;min-height:42px;border:1px solid var(--mall-color-border);border-radius:9px;padding:8px 10px;background:#fff;color:var(--mall-color-text)}
.audit-filters :is(input,select):focus-visible{outline:2px solid var(--mall-color-brand);outline-offset:2px}
.audit-actions{display:flex;align-items:end;gap:8px}.audit-range{margin:8px 0 18px;font-size:13px}
.audit-pages{display:flex;justify-content:space-between;align-items:center;margin:16px 0;color:var(--mall-color-muted);font-size:13px}.audit-pages>div{display:flex;gap:8px}
.audit-page pre{max-width:45ch;white-space:pre-wrap;overflow-wrap:anywhere}.audit-page summary{cursor:pointer;color:var(--mall-color-brand)}
@media(max-width:850px){.audit-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:540px){.audit-filters{grid-template-columns:1fr}.audit-pages{align-items:start;gap:10px;flex-direction:column}}
</style>
