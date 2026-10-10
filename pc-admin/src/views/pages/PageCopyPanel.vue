<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, ApiError } from '../../api'
import type { MicroDraft } from './types'

const props = defineProps<{ accountId: string; pageId: string; name: string; revision: number; publishedRevision?: number | null; disabled?: boolean }>()
const emit = defineEmits<{ copied: [draft: MicroDraft] }>()
type Intent = { key: string; pageId: string; body: { name: string; source: 'DRAFT' | 'PUBLISHED'; expectedRevision: number } }
const storageKey = `mall-page-copy:${props.accountId}`
const open = ref(false)
const title = ref('')
const source = ref<'DRAFT' | 'PUBLISHED'>('DRAFT')
const busy = ref(false)
const error = ref('')
const storageBlocked = ref(false)
const pending = ref<Intent | null>(null)
try {
  const raw = sessionStorage.getItem(storageKey)
  if (raw) {
    const value = JSON.parse(raw) as Intent
    if (typeof value.key !== 'string' || typeof value.pageId !== 'string' || typeof value.body?.name !== 'string' || !['DRAFT', 'PUBLISHED'].includes(value.body.source) || !Number.isInteger(value.body.expectedRevision)) throw new Error('invalid')
    pending.value = value
    open.value = true
  }
} catch { storageBlocked.value = true; error.value = '无法读取复制恢复记录。请恢复浏览器会话存储后重新进入页面，暂不能创建新副本。' }
const blocked = computed(() => props.disabled || busy.value || storageBlocked.value)
function begin() {
  if (blocked.value) return
  title.value = `${props.name.slice(0, 75)} 副本`
  source.value = 'DRAFT'
  error.value = ''
  open.value = true
}
function persist(intent: Intent) {
  sessionStorage.setItem(storageKey, JSON.stringify(intent))
  pending.value = intent
}
async function copy() {
  if (blocked.value) return
  if (!pending.value) {
    const name = title.value.trim()
    if (!name || name.length > 80) { error.value = '请输入 1—80 字的副本名称。'; return }
    const expectedRevision = source.value === 'DRAFT' ? props.revision : props.publishedRevision
    if (!expectedRevision) { error.value = '当前没有可复制的线上版本。'; return }
    try { persist({ key: crypto.randomUUID(), pageId: props.pageId, body: { name, source: source.value, expectedRevision } }) }
    catch { storageBlocked.value = true; error.value = '无法保存复制请求，未提交。请恢复会话存储后重试。'; return }
  }
  await submit(pending.value!)
}
async function submit(intent: Intent) {
  busy.value = true
  error.value = ''
  try {
    const result = await api<MicroDraft>(`/pages/${intent.pageId}/copy`, { method: 'POST', headers: { 'Idempotency-Key': intent.key }, body: JSON.stringify(intent.body) })
    if (!result.pageId || result.pageId === intent.pageId || result.publishedRevision !== null) throw new Error('复制结果不完整，请恢复原请求确认。')
    sessionStorage.removeItem(storageKey)
    pending.value = null
    open.value = false
    emit('copied', result)
  } catch (reason) {
    if (reason instanceof ApiError && [400, 404, 409, 422].includes(reason.status)) {
      try { sessionStorage.removeItem(storageKey); pending.value = null } catch { storageBlocked.value = true }
      error.value = `${reason.message} 请重新读取源页面后再复制。`
    } else error.value = '复制结果尚未确认。保留了原名称、来源和请求编号，请恢复原复制请求。'
  } finally { busy.value = false }
}
</script>

<template>
  <div class="page-copy-control">
    <button v-if="!pending" type="button" class="secondary-button" :disabled="blocked" @click="begin">复制页面</button>
    <button v-else type="button" class="secondary-button" :disabled="blocked" @click="copy">恢复原复制请求</button>
    <form v-if="open && !pending" class="page-copy-form" @submit.prevent="copy">
      <label>副本名称<input v-model="title" maxlength="80" :disabled="blocked"></label>
      <label>复制来源<select v-model="source" :disabled="blocked"><option value="DRAFT">已保存草稿</option><option value="PUBLISHED" :disabled="!publishedRevision">线上版本</option></select></label>
      <small>只复制已保存内容，新页面保持未发布；当前未保存修改不会进入副本。</small>
      <div class="page-copy-actions"><button type="button" class="text-button" :disabled="busy" @click="open = false">取消</button><button type="submit" class="primary-button" :disabled="blocked">{{ busy ? '复制中…' : '创建独立草稿' }}</button></div>
    </form>
    <p v-if="pending" class="help-text" role="status">原复制请求待确认：{{ pending.body.name }}。新复制暂不可用。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.page-copy-control{position:relative}.page-copy-form{display:grid;gap:12px;padding:16px;background:var(--mall-color-canvas-admin);border:1px solid var(--mall-color-border);border-radius:12px;width:min(360px,100%);margin-top:12px}.page-copy-form label{display:grid;gap:6px;font-size:13px}.page-copy-form input,.page-copy-form select{min-height:40px;padding:8px;border:1px solid var(--mall-color-border);border-radius:8px;min-width:0}.page-copy-form small{color:var(--mall-color-muted);line-height:1.6}.page-copy-actions{display:flex;justify-content:flex-end;gap:12px}.page-copy-control p{max-width:40ch}
</style>
