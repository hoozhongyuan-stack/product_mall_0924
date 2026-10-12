<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElButton, ElDialog } from 'element-plus'
import { api, ApiError, type Account } from '../../api'
import type { MicroPageSummary } from './types'
interface Selection { pageId: string; revision: number }
interface Intent { pageId: string; name: string; key: string; body: { expectedHomeRevision: number; expectedPublicationRevision: number } }
const props = defineProps<{ account: Account; target: MicroPageSummary | null; pages?: MicroPageSummary[] }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const selection = ref<Selection | null>(null), intent = ref<Intent | null>(null)
const error = ref(''), notice = ref(''), busy = ref(''), blocked = ref(false), syncPending = ref(false)
const opened = ref(false)
const canPublish = computed(() => ['page.read','page.publish'].every(code=>props.account.permissionCodes.includes(code)))
const validTarget = computed(() => !!props.target && props.target.publishedRevision !== null && !props.target.isHome && selection.value?.pageId!==props.target.pageId && Number.isInteger(props.target.publicationRevision))
const currentHomeName = computed(() => props.pages?.find(page => page.pageId===selection.value?.pageId)?.name || '')
const storageKey = () => `mall-home-selection:${props.account.accountId}`
let generation = 0
function clearIntent() { sessionStorage.removeItem(storageKey()); intent.value = null }
function restoreIntent() {
  blocked.value = false; intent.value = null
  try {
    const raw = sessionStorage.getItem(storageKey())
    if (!raw) return
    const value: Intent = JSON.parse(raw)
    if (typeof value.pageId !== 'string' || !value.pageId || typeof value.name !== 'string' || typeof value.key !== 'string' || !value.key || !Number.isInteger(value.body?.expectedHomeRevision) || !Number.isInteger(value.body?.expectedPublicationRevision)) throw new Error('请求记录无效')
    intent.value = value; opened.value = true
    notice.value = '上次设置首页的结果未确认。请按原请求恢复，避免重复操作。'
  } catch { blocked.value = true; opened.value = true; error.value = '无法读取原首页设置请求，已阻止新操作。请恢复会话存储后重试。' }
}
async function loadSelection() {
  const current = ++generation
  busy.value = 'read'; error.value = ''
  try {
    const result = await api<Selection>('/pages/home-selection')
    if (current !== generation) return
    if (!result.pageId || !Number.isInteger(result.revision)) throw new Error('当前首页状态格式不正确。')
    selection.value = result
    if (syncPending.value) {
      notice.value = result.pageId===intent.value?.pageId ? `已将“${intent.value?.name}”设为首页。` : '设置请求已完成，首页随后发生变化，请核对页面列表。'
      clearIntent(); syncPending.value = false; emit('changed')
    }
  } catch (reason) { if (current === generation) error.value = reason instanceof Error ? reason.message : '读取首页状态失败。' }
  finally { if (current === generation) busy.value = '' }
}
watch(() => props.account.accountId, () => {
  generation++; selection.value = null; error.value = ''; notice.value = ''; busy.value = ''; syncPending.value = false
  restoreIntent()
  if (opened.value && !blocked.value) void loadSelection()
}, { immediate: true })
watch(() => props.target, target => {
  if (!target) return
  opened.value = true; notice.value = ''
  if (!blocked.value) void loadSelection()
}, { immediate: true })
onUnmounted(() => { generation++ })
function close() { if (!busy.value) { opened.value = false; emit('close') } }
async function submit() {
  if (busy.value || blocked.value || syncPending.value || !canPublish.value) return
  if (!intent.value && (!validTarget.value || !selection.value)) return
  const current = ++generation
  const recovering = !!intent.value
  const request = intent.value || { pageId: props.target!.pageId, name: props.target!.name, key: crypto.randomUUID(), body: { expectedHomeRevision: selection.value!.revision, expectedPublicationRevision: props.target!.publicationRevision! } }
  try { sessionStorage.setItem(storageKey(), JSON.stringify(request)); if(sessionStorage.getItem(storageKey())!==JSON.stringify(request)) throw new Error('请求记录未保存') }
  catch { error.value = '无法保存原设置请求，操作未提交。请启用会话存储后重试。'; return }
  intent.value = request; busy.value = 'write'; error.value = ''; notice.value = ''
  try {
    const result = await api<Selection>(`/pages/${encodeURIComponent(request.pageId)}/set-home`, { method: 'POST', headers: { 'Idempotency-Key': request.key }, body: JSON.stringify(request.body) })
    if (current !== generation) return
    if (result.pageId !== request.pageId || result.revision!==request.body.expectedHomeRevision+1) throw new Error('首页设置结果未确认，请恢复原请求。')
    syncPending.value = true
    busy.value = ''
    await loadSelection()
  } catch (reason) {
    if (current !== generation) return
    // A definite rejection can release a newly-created request; an unknown or replayed request keeps its identity.
    if (!recovering && reason instanceof ApiError && reason.status >= 400 && reason.status < 500 && reason.status !== 408) clearIntent()
    error.value = `${reason instanceof Error ? reason.message : '设置首页失败。'}${intent.value ? ' 结果未确认，请恢复原请求。' : ' 请重新读取当前首页后再操作。'}`
  } finally { if (current === generation) busy.value = '' }
}
</script>
<template>
<ElDialog :model-value="opened" title="设置首页" width="min(520px,92vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" :before-close="close">
  <p>将“{{ intent?.name || target?.name }}”的已发布版本设为首页。未发布的草稿修改不会随之上线。</p>
  <p v-if="!intent && target?.publishedRevision===null" role="status">请先发布此微页面，再设置为首页。</p>
  <p v-if="!canPublish" role="status">当前账号没有设置首页权限。</p>
  <p v-if="selection" class="help-text">{{currentHomeName?`当前首页：${currentHomeName}`:'当前首页已配置。'}}</p>
  <p v-if="error" class="error notice" role="alert">{{ error }}</p><p v-if="notice" class="home-success" role="status">{{ notice }}</p>
  <p v-if="busy" role="status">{{busy==='read'?'正在核对当前首页…':'正在设置首页…'}}</p>
  <template #footer><ElButton :disabled="Boolean(busy)" @click="close">关闭</ElButton><ElButton :disabled="Boolean(busy)" @click="loadSelection">重新读取当前首页</ElButton><ElButton v-if="!syncPending" type="primary" :disabled="Boolean(busy) || blocked || !canPublish || (!intent && (!validTarget || !selection))" @click="submit">{{intent?'恢复原请求':'确认设为首页'}}</ElButton></template>
</ElDialog>
</template>
