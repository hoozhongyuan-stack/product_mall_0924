<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, confirmedWrite, type Account } from '../../api'
interface SettlementSettings { receivedWindowDays: number; revision: number; appliesTo: 'NEW_ORDERS' }
const props = defineProps<{ account: Account }>()
const config = ref<SettlementSettings | null>(null), days = ref(''), password = ref('')
const loading = ref(false), busy = ref(false), confirming = ref(false), needsRead = ref(false)
const error = ref(''), notice = ref('')
let epoch = 0
const permitted = computed(() => props.account.permissionCodes.includes('stores.manage'))
const valid = computed(() => /^\d+$/.test(days.value) && Number(days.value) >= 1 && Number(days.value) <= 365)
const saveAllowed = computed(() => permitted.value && config.value && valid.value && Number(days.value) !== config.value.receivedWindowDays && !loading.value && !busy.value && !needsRead.value)
function clearConfirmation() { password.value = ''; confirming.value = false }
async function load() {
  if (!permitted.value || busy.value) return
  const current = ++epoch
  loading.value = true; error.value = ''; notice.value = ''; clearConfirmation()
  try {
    const result = await api<SettlementSettings>('/stores/settlement-settings')
    if (current === epoch) { config.value = result; days.value = String(result.receivedWindowDays); needsRead.value = false }
  } catch { if (current === epoch) { config.value = null; error.value = '读取售后期失败，请重新读取售后期后重试。' } }
  finally { if (current === epoch) loading.value = false }
}
function begin() { if (saveAllowed.value) { confirming.value = true; password.value = ''; error.value = ''; notice.value = '' } }
async function save() {
  if (!saveAllowed.value || !confirming.value || !password.value || !config.value) return
  const current = ++epoch, revision = config.value.revision, requestedDays = Number(days.value)
  busy.value = true
  try {
    const result = await confirmedWrite<SettlementSettings>('stores.settlement.configure', password.value, '/stores/settlement-settings', 'PUT', { expectedRevision: revision, receivedWindowDays: requestedDays }, 'aftersale-policy', revision)
    if (current === epoch) {
      if (result.revision !== revision + 1 || result.receivedWindowDays !== requestedDays || result.appliesTo !== 'NEW_ORDERS') throw new Error('Unexpected result')
      config.value = result; days.value = String(result.receivedWindowDays); notice.value = '售后期已保存，仅新订单生效。'
    }
  } catch { if (current === epoch) { error.value = '保存未确认，请重新读取售后期，核对当前状态后再操作。'; needsRead.value = true } }
  finally { if (current === epoch) { busy.value = false; clearConfirmation() } }
}
watch(() => JSON.stringify([props.account.accountId, props.account.permissionCodes]), () => {
  epoch++; clearConfirmation(); config.value = null; days.value = ''; busy.value = false; loading.value = false; error.value = ''; notice.value = ''; void load()
})
onMounted(load)
onUnmounted(() => { epoch++; clearConfirmation() })
</script>
<template>
  <section v-if="permitted" class="panel store-section" aria-labelledby="store-settlement-title">
    <h2 id="store-settlement-title">售后期与结算条件</h2>
    <p>订单完成并超过售后期后才具备结算条件。新订单下单时保存期限快照，旧订单保持原期限。</p>
    <p>此处使用平台统一售后期，影响新订单的售后申请期限。设置售后期不会提前入账；结算还需订单完成、分润规则快照齐备且无处理中售后。</p>
    <button class="secondary-button" :disabled="loading || busy" @click="load">重新读取售后期</button>
    <p v-if="loading" role="status">正在读取售后期…</p><p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <form v-if="config" class="store-form" aria-label="售后期设置" @submit.prevent="begin">
      <label class="wide" for="store-aftersale-days">订单完成后的售后期（天）
        <input id="store-aftersale-days" v-model="days" type="number" inputmode="numeric" min="1" max="365" step="1" :disabled="busy || loading || confirming" />
        <small>填写 1–365 的整数。当前修订 {{ config.revision }}。</small>
      </label>
      <p v-if="!valid" role="status">售后期必须是 1–365 天的整数。</p>
      <div class="store-actions wide"><button class="primary-button" type="submit" :disabled="!saveAllowed || confirming">保存售后期</button></div>
    </form>
    <form v-if="confirming" aria-label="确认保存售后期" @submit.prevent="save">
      <h3>确认保存售后期</h3><p>本次修改影响新订单，请输入当前账号密码确认。</p>
      <label for="store-settlement-password">当前账号密码 <input id="store-settlement-password" v-model="password" type="password" autocomplete="current-password" :disabled="busy" /></label>
      <div class="store-actions"><button class="primary-button" type="submit" :disabled="busy || !password">{{ busy ? '正在保存…' : '确认保存售后期' }}</button><button class="secondary-button" type="button" :disabled="busy" @click="clearConfirmation">取消</button></div>
    </form>
  </section>
</template>
