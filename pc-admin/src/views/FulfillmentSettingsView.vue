<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, type Account } from '../api'
import type { Carrier } from './orders/types'
import './orders/orders.css'

defineProps<{ account: Account }>()
interface Policy { autoConfirmDays: number; revision: number }
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const carriers = ref<Carrier[]>([])
const policy = ref<Policy | null>(null)
const policyDays = ref('10')
const draft = ref<Record<string, { name: string; enabled: boolean }>>({})
const newCode = ref('')
const newName = ref('')
const newEnabled = ref(true)
const savedSnapshot = ref('')
const dirty = computed(() => savedSnapshot.value !== JSON.stringify({ draft: draft.value, policyDays: policyDays.value, newCode: newCode.value, newName: newName.value, newEnabled: newEnabled.value }))
const policyError = computed(() => /^[1-9]\d?$/.test(policyDays.value) && Number(policyDays.value) <= 30 ? '' : '自动确认收货天数须为 1—30 天。')
const newCarrierError = computed(() => {
  const code = newCode.value.trim()
  const name = newName.value.trim()
  if (!/^[A-Z0-9_]{2,24}$/.test(code)) return '代码使用 2—24 位大写字母、数字或下划线。'
  if (carriers.value.some(carrier => carrier.code === code)) return '此快递公司代码已存在，请编辑现有记录。'
  if (name.length < 2 || name.length > 80) return '请输入 2—80 字的快递公司名称。'
  return ''
})
function snapshot() { savedSnapshot.value = JSON.stringify({ draft: Object.fromEntries(carriers.value.map(item => [item.code, { name: item.name, enabled: item.enabled }])), policyDays: String(policy.value?.autoConfirmDays || ''), newCode: '', newName: '', newEnabled: true }) }
function message(failure: unknown) { return failure instanceof Error ? failure.message : '保存失败，请重新读取配置后重试。' }
async function load() {
  if (busy.value) return
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const [carrierResult, policyResult] = await Promise.all([api<{ items: Carrier[] }>('/fulfillment/carriers'), api<Policy>('/fulfillment/policy')])
    carriers.value = carrierResult.items
    draft.value = Object.fromEntries(carrierResult.items.map(item => [item.code, { name: item.name, enabled: item.enabled }]))
    policy.value = policyResult
    policyDays.value = String(policyResult.autoConfirmDays)
    newCode.value = ''; newName.value = ''; newEnabled.value = true
    snapshot()
  } catch (failure) { error.value = message(failure) }
  finally { loading.value = false }
}
async function saveCarrier(carrier: Carrier | null) {
  if (busy.value || loading.value) return
  const code = carrier?.code || newCode.value.trim()
  const values = carrier ? draft.value[carrier.code] : { name: newName.value, enabled: newEnabled.value }
  if (!carrier && newCarrierError.value) return
  if (values.name.trim().length < 2 || values.name.trim().length > 80) { error.value = '快递公司名称须为 2—80 字。'; return }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const updated = await api<Carrier>('/fulfillment/carriers', { method: 'PUT', body: JSON.stringify({ code, name: values.name.trim(), enabled: values.enabled, expectedRevision: carrier?.revision || 0 }) })
    carriers.value = [...carriers.value.filter(item => item.code !== updated.code), updated].sort((a, b) => a.code.localeCompare(b.code))
    draft.value = { ...draft.value, [updated.code]: { name: updated.name, enabled: updated.enabled } }
    if (!carrier) { newCode.value = ''; newName.value = ''; newEnabled.value = true }
    snapshot()
    notice.value = carrier ? '快递公司已更新。' : '快递公司已创建。'
  } catch (failure) { error.value = `${message(failure)} 如遇修订冲突，请刷新配置后重新核对。` }
  finally { busy.value = false }
}
async function savePolicy() {
  if (!policy.value || busy.value || policyError.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    policy.value = await api<Policy>('/fulfillment/policy', { method: 'PUT', body: JSON.stringify({ autoConfirmDays: Number(policyDays.value), expectedRevision: policy.value!.revision }) })
    policyDays.value = String(policy.value.autoConfirmDays)
    snapshot()
    notice.value = '自动确认收货天数已更新；已有订单保留原下单快照。'
  } catch (failure) { error.value = `${message(failure)} 如遇修订冲突，请刷新配置后重新核对。` }
  finally { busy.value = false }
}
onMounted(() => void load())
onBeforeRouteLeave(async () => !busy.value && (!dirty.value || await confirmAction('履约设置有未保存的修改，确定离开？')))
</script>

<template>
  <section class="page-content orders-page fulfillment-settings"><header class="page-heading"><div><RouterLink to="/orders" class="text-link">返回订单管理</RouterLink><h1 style="margin-top:16px">履约设置</h1><p>维护实际可用的快递公司与收货时限。只有启用的承运商可用于发货。</p></div><button class="secondary-button" :disabled="busy || loading" @click="load">刷新配置</button></header>
    <p v-if="loading" role="status">正在读取履约设置…</p><p v-if="error" role="alert" class="order-warning">{{ error }}</p><p v-if="notice" role="status" class="order-success">{{ notice }}</p>
    <template v-if="!loading && policy"><section class="panel order-section"><h2>自动确认收货</h2><p>发货后到期自动确认收货；每笔订单保存下单时的天数快照，修改只作用于后续订单。</p><div class="fulfillment-policy-row"><label for="auto-confirm-days">发货后天数<input id="auto-confirm-days" v-model="policyDays" type="number" min="1" max="30" step="1" :disabled="busy"></label><button class="primary-button" :disabled="busy || Boolean(policyError) || Number(policyDays) === policy.autoConfirmDays" @click="savePolicy">{{ busy ? '保存中…' : '保存天数' }}</button></div><p v-if="policyError" class="order-note">{{ policyError }}</p></section>
      <section class="panel order-section"><h2>快递公司名单</h2><p>请选择已签约或实际常用承运商；禁用后不能用于新发货，历史运单仍保留原名称。</p><p v-if="!carriers.length" class="order-warning">暂无快递公司。请先添加并启用至少一家，之后才能发货。</p><article v-for="carrier in carriers" :key="carrier.code" class="fulfillment-carrier-row"><strong>{{ carrier.code }}</strong><label>显示名称<input v-model="draft[carrier.code].name" maxlength="80" :disabled="busy"></label><label class="check-row"><input v-model="draft[carrier.code].enabled" type="checkbox" :disabled="busy">启用发货</label><button class="secondary-button" :disabled="busy || draft[carrier.code].name.trim().length < 2 || draft[carrier.code].name.trim().length > 80 || (draft[carrier.code].name === carrier.name && draft[carrier.code].enabled === carrier.enabled)" @click="saveCarrier(carrier)">保存此项</button></article><h3>添加快递公司</h3><div class="fulfillment-carrier-row"><label>公司代码<input v-model="newCode" maxlength="24" autocomplete="off" :disabled="busy" placeholder="例如 SF_EXPRESS"></label><label>显示名称<input v-model="newName" maxlength="80" :disabled="busy" placeholder="快递公司名称"></label><label class="check-row"><input v-model="newEnabled" type="checkbox" :disabled="busy">立即启用</label><button class="primary-button" :disabled="busy || Boolean(newCarrierError)" @click="saveCarrier(null)">添加承运商</button></div><p v-if="newCode || newName" class="order-note">{{ newCarrierError }}</p></section></template>
  </section>
</template>
