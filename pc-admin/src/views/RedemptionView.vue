<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, type Account } from '../api'
import OrderActionDialog from './orders/OrderActionDialog.vue'
import {pendingActions} from './orders/order-actions.mjs'
import {redemptionScopeLabel} from '../shared/fulfillment-policy'
import { fulfillmentLabel, redemptionError } from './orders/fulfillment.mjs'
import { time, type RedemptionLookup, type RedemptionRecord } from './orders/types'
import './orders/orders.css'

const props = defineProps<{ account: Account }>()
const code = ref('')
const lookup = ref<RedemptionLookup | null>(null)
const amount = ref('1')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const actionOpen = ref(false)
let epoch=0
onUnmounted(()=>epoch++)
const hasPending=computed(()=>lookup.value && pendingActions(props.account.accountId,lookup.value.orderId,sessionStorage).includes('redeem'))
const reverseTarget = ref<RedemptionRecord | null>(null)
const reverseReason = ref('')
const canRedeem = computed(() => props.account.permissionCodes.includes('fulfillment.redeem'))
const canReverse = computed(() => props.account.permissionCodes.includes('fulfillment.redeem.reverse'))
const validation = computed(() => lookup.value ? redemptionError(amount.value, lookup.value.availableQuantity) : '请先查询凭证。')
const usable = computed(() => lookup.value?.status === 'READY' && lookup.value.canRedeemPlatform !== false && ['PLATFORM','BOTH'].includes(lookup.value.redeemScope || ''))
function message(failure: unknown) { return failure instanceof Error ? failure.message : '操作失败，请核对服务端最新状态。' }

watch(()=>JSON.stringify([code.value,props.account.accountId]), () => { epoch++;lookup.value = null;actionOpen.value=false;loading.value=false;error.value='';notice.value='' })
async function inspect() {
  if (loading.value || busy.value || !code.value.trim()) return
  const current=epoch;loading.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api<RedemptionLookup>('/redemptions/lookup', { method: 'POST', body: JSON.stringify({ code: code.value.trim() }) })
    if(current!==epoch)return;lookup.value=result;amount.value = result.availableQuantity > 0 ? '1' : '0'
  } catch (failure) { if(current===epoch){lookup.value = null; error.value = message(failure)} }
  finally { if(current===epoch)loading.value = false }
}
function redeem() {
  if (!canRedeem.value || !lookup.value || busy.value || (!hasPending.value && (!usable.value || validation.value))) return
  actionOpen.value=true
}
async function refreshAfterWrite() {
  try { lookup.value = await api<RedemptionLookup>('/redemptions/lookup', { method: 'POST', body: JSON.stringify({ code: code.value.trim() }) }) }
  catch (failure) { error.value = `${message(failure)} 操作结果请稍后重新查询。` }
}
function openReverse(record: RedemptionRecord) { reverseTarget.value = record; reverseReason.value = ''; error.value = '' }
function closeReverse() { if (!busy.value) { reverseTarget.value = null; reverseReason.value = '' } }
async function reverse() {
  if (!canReverse.value || !lookup.value || !reverseTarget.value || !reverseReason.value.trim() || busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await api(`/redemptions/events/${encodeURIComponent(reverseTarget.value.eventId)}/reverse`, { method: 'POST', body: JSON.stringify({ reason: reverseReason.value.trim(), expectedRevision: lookup.value.revision }) })
    reverseTarget.value = null; reverseReason.value = ''
    notice.value = '核销撤销已记录；原核销流水保留。'
    await refreshAfterWrite()
  } catch (failure) { error.value = `${message(failure)} 请重新查询核销记录，避免重复撤销。` }
  finally { busy.value = false }
}
</script>

<template>
  <section class="page-content orders-page redemption-page">
    <header class="page-heading"><div><RouterLink to="/orders" class="text-link">返回订单管理</RouterLink><h1 style="margin-top:16px">权益核销</h1><p>扫码枪可直接输入凭证并回车，也可手工输码。平台和授权前置仓共享同一份剩余权益，核销时实时校验范围、数量和权限。</p></div></header>
    <section class="panel order-section"><h2>查询核销凭证</h2><form class="redeem-lookup-form" @submit.prevent="inspect()"><label for="redemption-code">核销码<input id="redemption-code" v-model="code" maxlength="128" autocomplete="off" inputmode="text" :disabled="loading || busy" placeholder="扫描或输入核销码"></label><button class="primary-button" type="submit" :disabled="loading || busy || !code.trim()">{{ loading ? '查询中…' : '查询凭证' }}</button></form><p class="order-note">凭证属于订单项；查询和核销需联网。请向顾客核对订单项与数量。</p><p v-if="error" class="order-warning" role="alert">{{ error }}</p><p v-if="notice" class="order-success" role="status">{{ notice }}</p><p v-if="!canRedeem && !canReverse" class="order-note">当前账号仅能查询，没有核销或撤销权限。</p></section>
    <section v-if="lookup" class="panel order-section" aria-live="polite"><h2>凭证核对结果</h2><dl class="order-facts"><div><dt>订单</dt><dd><RouterLink v-if="account.permissionCodes.includes('order.read')" :to="`/orders/${lookup.orderId}`" class="text-link">{{ lookup.orderNo }}</RouterLink><span v-else>{{ lookup.orderNo }}</span></dd></div><div><dt>商品 / SKU</dt><dd>{{ lookup.name }}{{ lookup.skuCode ? ` · ${lookup.skuCode}` : '' }}</dd></div><div><dt>凭证状态</dt><dd>{{ fulfillmentLabel(lookup.status) }}</dd></div><div><dt>购买数量</dt><dd>{{ lookup.quantity }}</dd></div><div><dt>已核销</dt><dd>{{ lookup.redeemedQuantity }}</dd></div><div><dt>剩余权益</dt><dd>{{ lookup.remainingQuantity }}</dd></div><div><dt>售后冻结</dt><dd>{{lookup.frozenQuantity||0}}</dd></div><div><dt>当前可核销</dt><dd>{{lookup.availableQuantity}}</dd></div><div><dt>核销范围</dt><dd>{{redemptionScopeLabel(lookup)}}</dd></div><div><dt>凭证有效期</dt><dd>{{ lookup.validUntil || '待确认' }}</dd></div></dl>
      <form v-if="canRedeem && (hasPending || usable && lookup.availableQuantity > 0)" class="redeem-action" @submit.prevent="redeem"><label for="redeem-quantity">本次核销数量<input id="redeem-quantity" v-model="amount" type="number" min="1" :max="lookup.availableQuantity" step="1" :disabled="busy || Boolean(hasPending)"></label><button class="primary-button" :disabled="busy || (!hasPending && Boolean(validation))">{{ busy ? '核销中…' : hasPending ? '核查原核销结果' : '核对并确认核销' }}</button><p v-if="validation" class="order-note">{{ validation }}</p></form><p v-else-if="!usable || lookup.availableQuantity < 1" class="order-warning">凭证当前不可核销；请核对有效期、授权范围、状态和当前可用数量。</p>
      <h3>核销与撤销记录</h3><p v-if="!lookup.events?.length">尚无核销记录。</p><p v-if="(lookup.eventCount || 0) > 20" class="order-note">共 {{ lookup.eventCount }} 条，当前展示最近 20 条。</p><article v-for="record in lookup.events || []" :key="record.eventId" class="order-report"><div class="fulfillment-record"><div><strong>{{ record.kind === 'REVERSE' ? `撤销 ${record.quantity} 件` : record.reversedAt ? '已撤销的核销' : `核销 ${record.quantity} 件` }}</strong><p>{{ time(record.occurredAt) }} · {{record.actorKind==='STORE'?record.storeName||'前置仓中心':'平台后台'}} · {{ record.actorName || '授权操作员' }} · 当时剩余 {{ record.remainingQuantity }}</p><p v-if="record.reason">原因：{{ record.reason }}</p><p v-if="record.reversedAt">撤销：{{ time(record.reversedAt) }}</p></div><button v-if="canReverse && record.kind === 'USE' && !record.reversedAt" class="secondary-button" :disabled="busy" @click="openReverse(record)">撤销这次核销</button></div></article>
    </section>
    <OrderActionDialog :account="account" :order-id="lookup?.orderId||''" :action="actionOpen?'redeem':null" :redemption-code="code" :redemption-quantity="String(amount)" @close="actionOpen=false" @updated="refreshAfterWrite" @notice="notice=$event" />
    <el-dialog :model-value="Boolean(reverseTarget)" title="撤销核销记录" class="order-confirm-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="closeReverse"><p>将撤销 {{ reverseTarget?.quantity }} 件的核销。原记录和撤销原因都会保留，请先核实服务提供情况。</p><label for="reverse-reason">撤销原因<textarea id="reverse-reason" v-model="reverseReason" maxlength="500" rows="3" :disabled="busy" placeholder="填写具体原因"></textarea></label><p v-if="error" class="order-warning" role="alert">{{ error }}</p><template #footer><div class="dialog-footer"><button class="secondary-button" :disabled="busy" @click="closeReverse">返回</button><button class="primary-button" :disabled="busy || !reverseReason.trim()" @click="reverse">{{ busy ? '撤销中…' : '确认撤销' }}</button></div></template></el-dialog>
  </section>
</template>
