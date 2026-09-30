<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, ApiError } from '../api'
import { type PaymentPolicy } from './orders/types'
import './orders/orders.css'
const data = ref<PaymentPolicy | null>(null)
const form = ref({ instructions: '', merchantAccountId: '', wechatTimeoutMinutes: 30, offlineTimeoutMinutes: 1440, offlineEnabled: false, wechatEnabled: false })
const saved = ref('')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const conflict = ref(false)
const dirty = computed(() => !!data.value && JSON.stringify(form.value) !== saved.value)
const validation = computed(() => {
  if (form.value.instructions.length > 4000) return '付款说明不能超过 4000 字。'
  if (form.value.merchantAccountId.length > 80) return '收款账户标识不能超过 80 字。'
  if (Boolean(form.value.instructions.trim()) !== Boolean(form.value.merchantAccountId.trim())) return '付款说明与收款账户标识需要一起配置。'
  if (![form.value.wechatTimeoutMinutes, form.value.offlineTimeoutMinutes].every(value => Number.isInteger(value) && value >= 1 && value <= 10080)) return '待付款时限请输入 1 至 10080 分钟的整数。'
  return ''
})
async function load() {
  if (dirty.value && !window.confirm('放弃未保存的付款配置并重新读取？')) return
  loading.value = true; error.value = ''; notice.value = ''
  try {
    data.value = await api<PaymentPolicy>('/payments/offline-policy')
    form.value = { instructions: data.value.instructions, merchantAccountId: data.value.merchantAccountId, wechatTimeoutMinutes: data.value.wechatTimeoutMinutes, offlineTimeoutMinutes: data.value.offlineTimeoutMinutes, offlineEnabled: data.value.offlineEnabled ?? data.value.availablePaymentMethods?.includes('OFFLINE') ?? false, wechatEnabled: data.value.wechatEnabled ?? data.value.availablePaymentMethods?.includes('WECHAT') ?? false }
    saved.value = JSON.stringify(form.value); conflict.value = false
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '付款配置读取失败。' }
  finally { loading.value = false }
}
async function save() {
  if (!data.value || busy.value || validation.value || conflict.value || !dirty.value) return
  busy.value = true; error.value = ''; notice.value = ''
  const snapshot = { ...form.value }
  try {
    const result = await api<PaymentPolicy>('/payments/offline-policy', { method: 'PUT', body: JSON.stringify({ ...snapshot, expectedRevision: data.value.revision }) })
    data.value = result; saved.value = JSON.stringify(snapshot)
    notice.value = '付款配置已保存，支付方式和付款资料对新订单生效。'
  } catch (reason) { conflict.value = reason instanceof ApiError && reason.status === 409; error.value = reason instanceof Error ? reason.message : '保存失败，请重试。' }
  finally { busy.value = false }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { void load(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => !dirty.value || window.confirm('付款配置尚未保存，确定离开？'))
</script>
<template>
  <section class="page-content orders-page order-settings">
    <header class="page-heading"><div><h1>付款配置</h1><p>选择可用支付方式，配置付款说明和时限。新订单保存当时的付款资料。</p></div><button class="secondary-button" :disabled="loading || busy" @click="load">重新读取</button></header>
    <p v-if="loading" role="status" class="loading-inline">正在读取付款配置…</p>
    <div v-if="error" class="notice" role="alert">{{ error }} <button v-if="!data || conflict" class="text-button" :disabled="busy" @click="load">重新读取配置</button></div>
    <p v-if="notice" class="order-success" role="status">{{ notice }}</p>
    <form v-if="data && !loading" class="panel order-section" @submit.prevent="save">
      <fieldset class="payment-modes"><legend>启用支付方式</legend>
        <label><input v-model="form.offlineEnabled" type="checkbox" data-payment-method="OFFLINE" :disabled="busy">线下支付</label>
        <label><input v-model="form.wechatEnabled" type="checkbox" data-payment-method="WECHAT" :disabled="busy">微信支付</label>
      </fieldset>
      <p class="order-note">可以同时启用两种方式。取消勾选即停用；两项均未勾选时暂停现金订单提交。</p>
      <p class="order-note">微信支付：{{ data.wechatConfigurationStatus === 'PENDING_VERIFICATION' ? '商户资料已配置，待真实交易验证' : '未完成商户配置，实际支付暂不可用' }}。商户证书和密钥由服务器安全配置，启用选项不会代替商户配置与到账核验。</p>
      <h2>线下付款说明</h2><p class="order-note">请由运营人员填写真实、已核实的付款资料，安排到账核对岗位。勾选线下支付后，请确认收款资料完整且准确。</p>
      <label class="policy-field">用户可见付款说明<textarea v-model="form.instructions" rows="7" maxlength="4000" :disabled="busy" placeholder="尚未配置。请填写真实收款资料、付款备注要求与核实说明。"></textarea></label>
      <label class="policy-field">收款账户标识<input v-model="form.merchantAccountId" maxlength="80" autocomplete="off" :disabled="busy" placeholder="与银行或收款渠道账户对应的内部标识"></label>
      <h2>待付款时限</h2><div class="order-form"><label>线下支付（分钟）<input v-model.number="form.offlineTimeoutMinutes" type="number" min="1" max="10080" step="1" :disabled="busy"></label><label>微信支付（分钟）<input v-model.number="form.wechatTimeoutMinutes" type="number" min="1" max="10080" step="1" :disabled="busy"></label></div>
      <p class="order-note">默认线下 1440 分钟（24 小时），微信 30 分钟。修改只影响新订单，已有订单付款截止时间保持下单快照。</p>
      <p>线下资料：{{ data.configured ? '已配置' : '未配置' }} · 公开下单：{{ data.availablePaymentMethods?.length ? data.availablePaymentMethods.map(value => value === 'OFFLINE' ? '线下' : '微信').join('、') : '各支付方式均未开放' }} · 修订 {{ data.revision }}<span v-if="dirty"> · 有未保存修改</span></p>
      <p v-if="validation" class="error" role="alert">{{ validation }}</p><button type="submit" class="primary-button" :disabled="busy || !dirty || conflict || Boolean(validation)">{{ busy ? '保存中…' : '保存付款配置' }}</button>
    </form>
  </section>
</template>

<style scoped>
.payment-modes { display: flex; flex-wrap: wrap; gap: 16px 32px; border: 1px solid var(--mall-color-border); border-radius: 10px; padding: 16px; margin: 0 0 12px; }
.payment-modes legend { font-weight: 650; padding: 0 6px; }
.payment-modes label { display: inline-flex; align-items: center; gap: 10px; min-height: 40px; cursor: pointer; }
.payment-modes input { width: 18px; height: 18px; min-height: 0; margin: 0; accent-color: var(--mall-color-brand); }
</style>
