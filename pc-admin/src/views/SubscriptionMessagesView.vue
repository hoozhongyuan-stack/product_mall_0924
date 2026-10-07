<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, ApiError, type Account } from '../api'
import {
  EVENT_TYPES, draftBody, draftIssue, parseTemplateRow, parseTemplateSettings, reconcileDraft,
  type DraftFields, type EventType, type TemplateRow,
} from './notifications/draft.mjs'
import './notifications/subscription.css'

const props = defineProps<{ account: Account }>()
const details: Record<EventType, { title: string; description: string }> = {
  ORDER_PAID: { title: '订单支付成功', description: '现金订单收款确认后产生候选事件。' },
  ORDER_SHIPPED: { title: '订单首次发货', description: '实物订单首次发货后产生候选事件。' },
  REFUND_SUCCEEDED: { title: '退款成功', description: '资金退款成功后产生候选事件。' },
}
const statusLabel = { UNBOUND: '未配置', INCOMPLETE: '信息不完整', DRAFT_UNVERIFIED: '待平台核验' }
const rows = ref<TemplateRow[]>([])
const forms = ref({} as Record<EventType, DraftFields>)
const saved = ref({} as Record<EventType, DraftFields>)
const errors = ref({} as Partial<Record<EventType, string>>)
const notices = ref({} as Partial<Record<EventType, string>>)
const conflicts = ref({} as Partial<Record<EventType, boolean>>)
const uncertain = ref({} as Partial<Record<EventType, DraftFields>>)
const loading = ref(true)
const busy = ref<EventType | null>(null)
const loadError = ref('')
let generation = 0

const canManage = computed(() => props.account.permissionCodes.includes('notification.manage'))
const dirty = (event: EventType) => !!forms.value[event] && !!saved.value[event] &&
  (forms.value[event].draftAppId !== saved.value[event].draftAppId ||
    forms.value[event].draftTemplateId !== saved.value[event].draftTemplateId)
const hasPending = () => !!busy.value || EVENT_TYPES.some(event => dirty(event) || !!uncertain.value[event])

async function load(force = false) {
  if (busy.value) return
  if (!force && !Object.keys(uncertain.value).length && EVENT_TYPES.some(dirty) &&
      !await confirmAction('放弃未保存的消息模板草稿并重新读取？')) return
  const currentGeneration = ++generation
  const preserveEdits = force && rows.value.length > 0
  loading.value = true
  loadError.value = ''
  try {
    const result = parseTemplateSettings(await api<unknown>('/subscription-templates'))
    if (currentGeneration !== generation) return
    const recovering = Object.keys(uncertain.value).length > 0
    const nextForms = { ...forms.value }
    const nextSaved = { ...saved.value }
    const nextErrors = { ...errors.value }
    const nextNotices = { ...notices.value }
    const nextConflicts = { ...conflicts.value }
    const nextUncertain = { ...uncertain.value }
    for (const row of result) {
      const event = row.eventType
      const serverForm = { draftAppId: row.draftAppId, draftTemplateId: row.draftTemplateId }
      const pending = uncertain.value[event]
      nextSaved[event] = { ...serverForm }
      if (pending) {
        const matched = pending.draftAppId === row.draftAppId && pending.draftTemplateId === row.draftTemplateId
        nextForms[event] = matched ? reconcileDraft(forms.value[event], pending, row).form : forms.value[event]
        nextConflicts[event] = !matched
        nextNotices[event] = matched ? '已从服务端核实草稿保存成功；发送仍未开通。' : ''
        nextErrors[event] = matched ? '' : '服务端内容与上次提交不一致，请核对后再保存。'
        delete nextUncertain[event]
      } else {
        const keepLocal = (recovering || preserveEdits) && dirty(event)
        nextForms[event] = keepLocal ? { ...forms.value[event] } : { ...serverForm }
        nextConflicts[event] = false
        nextErrors[event] = ''
        nextNotices[event] = keepLocal ? '已重新读取服务端草稿；本地未保存输入已保留，请核对后再提交。' : ''
      }
    }
    rows.value = result
    forms.value = nextForms
    saved.value = nextSaved
    errors.value = nextErrors
    notices.value = nextNotices
    conflicts.value = nextConflicts
    uncertain.value = nextUncertain
  } catch (reason) {
    if (currentGeneration === generation) loadError.value = reason instanceof Error ? reason.message : '读取消息配置失败。'
  } finally {
    if (currentGeneration === generation) loading.value = false
  }
}

async function save(event: EventType) {
  const row = rows.value.find(item => item.eventType === event)
  if (!row || !canManage.value || loading.value || loadError.value || busy.value || conflicts.value[event] || uncertain.value[event] || !dirty(event)) return
  const issue = draftIssue(forms.value[event])
  if (issue) { errors.value = { ...errors.value, [event]: issue }; return }
  const submitted = draftBody(forms.value[event], row.revision)
  busy.value = event
  errors.value = { ...errors.value, [event]: '' }
  notices.value = { ...notices.value, [event]: '' }
  try {
    const result = parseTemplateRow(await api<unknown>(`/subscription-templates/${event}`, {
      method: 'PUT', body: JSON.stringify(submitted),
    }))
    if (result.eventType !== event || result.revision !== row.revision + 1 ||
        result.draftAppId !== submitted.draftAppId || result.draftTemplateId !== submitted.draftTemplateId) {
      throw new Error('保存回执不完整，请重新读取服务端状态。')
    }
    rows.value = rows.value.map(item => item.eventType === event ? result : item)
    forms.value = { ...forms.value, [event]: reconcileDraft(forms.value[event], submitted, result).form }
    saved.value = { ...saved.value, [event]: { draftAppId: result.draftAppId, draftTemplateId: result.draftTemplateId } }
    notices.value = { ...notices.value, [event]: '草稿已保存；未核验平台前不会发送消息。' }
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) {
      conflicts.value = { ...conflicts.value, [event]: true }
      errors.value = { ...errors.value, [event]: '草稿已由其他操作修改，请重新读取并核对。' }
    } else if (reason instanceof ApiError &&
        ([400, 401, 403, 404, 422].includes(reason.status) ||
          (reason.status === 429 && reason.code === 'RATE_LIMITED'))) {
      errors.value = { ...errors.value, [event]: reason.message }
    } else {
      uncertain.value = { ...uncertain.value, [event]: { draftAppId: submitted.draftAppId, draftTemplateId: submitted.draftTemplateId } }
      errors.value = { ...errors.value, [event]: '保存结果未知，请重新读取核实；在此之前不能再次提交。' }
    }
  } finally { busy.value = null }
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (hasPending()) { event.preventDefault(); event.returnValue = '' }
}
onMounted(() => { void load(true); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => { generation += 1; window.removeEventListener('beforeunload', beforeUnload) })
onBeforeRouteLeave(async () => !hasPending() || await confirmAction('消息模板草稿有未保存或未核实的操作，确定离开？'))
</script>

<template>
  <section class="page-content subscription-page">
    <header class="page-heading">
      <div><h1>订阅消息</h1><p>维护支付、发货与退款通知的模板配置。模板经平台核验后才能发送。</p></div>
      <button type="button" class="secondary-button" :disabled="loading || !!busy" @click="load()">重新读取</button>
    </header>
    <div class="subscription-gate" role="status"><strong>发送暂未开通</strong><span>目前没有经平台核验的小程序账号和模板。保存下方标识只形成草稿，不会触发授权弹窗或发送。</span></div>
    <p v-if="loading" class="loading-inline" role="status">正在读取消息配置…</p>
    <div v-if="loadError" class="notice" role="alert">{{ loadError }} <button type="button" class="text-button" @click="load(true)">重新读取</button></div>
    <div v-if="!loading && !loadError && rows.length" class="subscription-list">
      <form v-for="row in rows" :key="row.eventType" class="panel subscription-item" @submit.prevent="save(row.eventType)">
        <div class="subscription-item-heading"><div><h2>{{ details[row.eventType].title }}</h2><p>{{ details[row.eventType].description }}</p></div><span class="subscription-state">{{ statusLabel[row.status] }}</span></div>
        <div class="subscription-fields">
          <label :for="`${row.eventType}-app`">小程序 AppID<input :id="`${row.eventType}-app`" v-model="forms[row.eventType].draftAppId" type="text" maxlength="64" autocomplete="off" spellcheck="false" placeholder="取得账号后填写" :disabled="!canManage || busy === row.eventType"></label>
          <label :for="`${row.eventType}-template`">模板 ID<input :id="`${row.eventType}-template`" v-model="forms[row.eventType].draftTemplateId" type="text" maxlength="128" autocomplete="off" spellcheck="false" placeholder="取得模板后填写" :disabled="!canManage || busy === row.eventType"></label>
        </div>
        <div class="subscription-item-footer"><p>修订 {{ row.revision }} · {{ dirty(row.eventType) ? '有未保存修改' : '草稿已同步' }} · 发送关闭</p><button type="submit" class="primary-button" :disabled="!canManage || !!busy || !dirty(row.eventType) || !!conflicts[row.eventType] || !!uncertain[row.eventType] || !!draftIssue(forms[row.eventType])">{{ busy === row.eventType ? '保存中…' : '保存草稿' }}</button></div>
        <p v-if="draftIssue(forms[row.eventType])" class="subscription-error" role="alert">{{ draftIssue(forms[row.eventType]) }}</p>
        <p v-if="errors[row.eventType]" class="subscription-error" role="alert">{{ errors[row.eventType] }} <button v-if="conflicts[row.eventType] || uncertain[row.eventType]" type="button" class="text-button" @click="load(true)">核实服务端状态</button></p>
        <p v-if="dirty(row.eventType) && (conflicts[row.eventType] || notices[row.eventType])" class="subscription-server-value">{{ conflicts[row.eventType] ? '上次读取' : '当前读取' }}的服务端值：AppID {{ row.draftAppId || '空' }}；模板 ID {{ row.draftTemplateId || '空' }}。重新读取时本地输入会保留。</p>
        <p v-if="notices[row.eventType]" class="subscription-success" role="status">{{ notices[row.eventType] }}</p>
      </form>
    </div>
    <p v-if="!canManage && !loading" class="subscription-readonly">当前账号仅有查看权限。如需编辑草稿，请由主账号授权消息配置权限。</p>
  </section>
</template>
