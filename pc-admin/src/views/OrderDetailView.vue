<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { api, type Account, type Confirmation } from '../api'
import { amountFen, reconciliationError, sameEvidence, paymentStatus, type ReconciliationForm } from './orders/offline-payment.mjs'
import { money, time, outcomeLabel, anomalyLabel, type Order, type Reconciliation } from './orders/types'
import { fulfillmentLabel, remaining, shippingQuantity } from './orders/fulfillment.mjs'
import OrderBenefitsSummary from '../shared/OrderBenefitsSummary.vue'
import FulfillmentPanel from './orders/FulfillmentPanel.vue'
import {isPointsOrder,orderSettlementValue,paymentMethodLabel} from '../shared/order-settlement.mjs'
import './orders/orders.css'
const props = defineProps<{ account: Account }>()
const route = useRoute()
const order = ref<Order | null>(null)
let loadGeneration = 0
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const resultAnomaly = ref(false)
const form = ref<ReconciliationForm>({ merchantAccountId: '', externalTradeNo: '', amount: '', paidAt: '', note: '', verified: false })
const intent = ref<Reconciliation | null>(null)
const requestKey = ref('')
const requestBody = ref<Record<string, unknown> | null>(null)
const confirmOpen = ref(false)
const password = ref('')
const confirmationRequested = ref(false)
const canConfirm = computed(() => props.account.permissionCodes.includes('payment.offline.confirm'))
const dirty = computed(() => !!(form.value.externalTradeNo || form.value.amount || form.value.paidAt || form.value.note || form.value.verified))
const isPoints = computed(()=>isPointsOrder(order.value))
const isOffline = computed(() => order.value?.paymentMethod === 'OFFLINE')
const validation = computed(() => order.value ? reconciliationError(form.value, order.value.payableFen) : '请先读取订单。')
function message(reason: unknown) { return reason instanceof Error ? reason.message : '操作失败，请重新读取订单后重试。' }
async function loadId(id: string) {
  const generation = ++loadGeneration
  loading.value = true; error.value = ''
  try { const result = await api<Order>(`/orders/${encodeURIComponent(id)}`); if (generation !== loadGeneration) return; order.value = result; if (!dirty.value && !form.value.merchantAccountId) form.value = { ...form.value, merchantAccountId: result.paymentInstructions?.merchantAccountId || '' } }
  catch (reason) { if (generation === loadGeneration) { order.value = null; error.value = message(reason) } }
  finally { if (generation === loadGeneration) loading.value = false }
}
async function load() { await loadId(String(route.params.orderId)) }
function resume(record: Reconciliation) { intent.value = { ...record, confirmationObjectId: record.reconciliationId, confirmationRevision: 1 }; password.value = ''; confirmOpen.value = true; confirmationRequested.value = true }
function evidence(): Record<string, unknown> {
  return { expectedRevision: order.value!.revision, merchantAccountId: form.value.merchantAccountId.trim(), externalTradeNo: form.value.externalTradeNo.trim(), amountFen: amountFen(form.value.amount), paidAt: new Date(form.value.paidAt).toISOString(), note: form.value.note.trim(), verified: true }
}
async function prepare() {
  if (busy.value || !canConfirm.value || (!confirmationRequested.value && validation.value) || !order.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    if (confirmationRequested.value && intent.value) { password.value = ''; confirmOpen.value = true; return }
    const body = evidence()
    if (!requestBody.value || !sameEvidence(requestBody.value, body)) {
      if (confirmationRequested.value) { error.value = '上次确认结果未知，请先重试该记录或查询订单；核对记录暂不可更改。'; return }
      requestKey.value = crypto.randomUUID(); requestBody.value = body; intent.value = null
    }
    if (!intent.value) intent.value = await api<Reconciliation>(`/orders/${order.value.orderId}/offline-reconciliations`, { method: 'POST', headers: { 'Idempotency-Key': requestKey.value }, body: JSON.stringify(requestBody.value) })
    password.value = ''; confirmOpen.value = true
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
function closeConfirm() { if (busy.value) return; confirmOpen.value = false; password.value = '' }
async function confirmReceipt() {
  const prepared = intent.value
  if (!prepared || busy.value || !password.value || !canConfirm.value) return
  busy.value = true; error.value = ''
  try {
    const token = await api<Confirmation>('/auth/confirm', { method: 'POST', body: JSON.stringify({ action: 'payment.offline.confirm', password: password.value, objectId: prepared.confirmationObjectId, revision: prepared.confirmationRevision }) })
    password.value = ''; confirmationRequested.value = true
    const result = await api<{ outcome: string; receiptId: string | null; orderStatus: string; order: Order }>(`/payments/offline-reconciliations/${prepared.reconciliationId}/confirm`, { method: 'POST', headers: { 'X-Action-Confirmation': token.confirmationToken }, body: '{}' })
    order.value = result.order; confirmOpen.value = false
    resultAnomaly.value = result.outcome !== 'PAID'
    notice.value = result.outcome === 'PAID' ? '实际到账已核实，订单已付款。库存、券与积分已由服务端结算。' : '实际到账已登记，订单未确认收款。请查看异常原因并按资金异常流程处理。'
    if (result.outcome === 'PENDING') { confirmationRequested.value = true; notice.value = '到账已登记，结算仍待处理。请重试此记录或查询最新订单。'; return }
    form.value = { merchantAccountId: form.value.merchantAccountId, externalTradeNo: '', amount: '', paidAt: '', note: '', verified: false }
    requestKey.value = ''; requestBody.value = null; intent.value = null; confirmationRequested.value = false
  } catch (reason) { error.value = message(reason); password.value = '' }
  finally { busy.value = false }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || confirmationRequested.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { void load(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => { loadGeneration += 1; window.removeEventListener('beforeunload', beforeUnload) })
async function mayLeaveOrder(message: string) {
  if (busy.value) return false
  const generation = loadGeneration
  const actor = props.account.accountId
  const allowed = !(dirty.value || confirmationRequested.value) || await confirmAction(message)
  return allowed && !busy.value && generation === loadGeneration && actor === props.account.accountId
}
onBeforeRouteUpdate(() => mayLeaveOrder('核对尚未完成，确定切换订单？'))
watch(() => route.params.orderId, id => {
  form.value = { merchantAccountId: '', externalTradeNo: '', amount: '', paidAt: '', note: '', verified: false }
  intent.value = null; requestBody.value = null; requestKey.value = ''; confirmationRequested.value = false
  confirmOpen.value = false; password.value = ''; notice.value = ''; resultAnomaly.value = false
  void loadId(String(id))
})
onBeforeRouteLeave(() => mayLeaveOrder('核对记录尚未完成，离开后请通过订单详情核查实际处理结果。确定离开？'))
</script>
<template>
  <section class="page-content orders-page">
    <header class="page-heading"><div><RouterLink to="/orders" class="text-link">返回订单列表</RouterLink><h1 style="margin-top:16px">订单详情</h1><p>{{isPoints?'积分兑换成功后直接结算；每个订单项独立履约。':'订单收款与每项履约独立记录；确认收款后才能发货或核销。'}}</p></div><button class="secondary-button" :disabled="loading || busy" @click="load">刷新订单状态</button></header>
    <p v-if="loading" role="status" class="loading-inline">正在读取订单详情…</p>
    <div v-if="error" class="notice" role="alert">{{ error }} <button v-if="!order" class="text-button" @click="load">重新加载</button></div>
    <p v-if="notice" role="status" :class="resultAnomaly ? 'order-warning' : 'order-success'">{{ notice }}</p>
    <template v-if="order && !loading">
      <section class="panel order-section"><h2>{{ isPoints ? paymentStatus(order) : order.payableFen===0 && order.status==='PAID' && !order.receipts?.some(r => r.anomaly?.status==='OPEN') ? '零现金订单已结算' : paymentStatus({ ...order, paymentReviewStatus: order.receipts?.some(r => r.anomaly?.status === 'OPEN') ? 'ANOMALY' : order.paymentReviewStatus }) }}</h2><dl class="order-facts"><div><dt>订单号</dt><dd>{{ order.orderNo }}</dd></div><div><dt>支付方式</dt><dd>{{isPoints?paymentMethodLabel(order):order.payableFen === 0 ? '零现金结算' : paymentMethodLabel(order)}}</dd></div><div><dt>履约进度</dt><dd>{{ order.status === 'PAID' ? fulfillmentLabel(order.fulfillmentStatus) : '确认收款后开始' }}</dd></div><div><dt>{{isPoints?'已扣兑换积分':'应付金额'}}</dt><dd class="order-money">{{orderSettlementValue(order)}}</dd></div><div><dt>创建时间</dt><dd>{{ time(order.createdAt) }}</dd></div><div v-if="!isPoints"><dt>付款截止</dt><dd>{{ time(order.expiresAt) }}</dd></div><div><dt>{{ order.status === 'CLOSED' ? '关闭时间' : isPoints ? '积分结算时间' : order.payableFen===0 ? '零现金结算时间' : '确认收款时间' }}</dt><dd>{{ time(order.status === 'CLOSED' ? order.closedAt : order.paidAt) }}</dd></div></dl><p v-if="order.status === 'CLOSED' && !isPoints" class="order-warning">原单已关闭。若发现到账，只登记异常资金，不恢复订单；按异常流程处理。</p></section>
      <section class="panel order-section"><h2>{{isPoints?'订单项与兑换积分':'订单项与金额'}}</h2><div class="table-wrap" tabindex="0" aria-label="订单项，可横向滚动"><table><thead><tr><th>商品 / SKU</th><th>履约方式</th><th>数量</th><th>履约进度</th><th>{{isPoints?'积分单价':'商品金额'}}</th><th>{{isPoints?'兑换积分':'优惠后金额'}}</th></tr></thead><tbody><tr v-for="line in order.items" :key="line.orderLineId"><td>{{ line.name }}<small class="order-muted">{{ line.skuCode }}</small></td><td>{{ line.fulfillmentKind === 'SHIP' ? '快递' : '到店核销' }}</td><td>{{ line.quantity }} {{ line.saleUnit }}<small v-if="line.fulfillmentKind==='SHIP'" class="order-muted"><template v-if="shippingQuantity(line)">已完成售后退回 {{shippingQuantity(line)!.refunded}} {{line.saleUnit}}<template v-if="!order.shipment"> · {{line.fulfillment?.status==='AFTER_SALE'?'未发':'待发'}} {{shippingQuantity(line)!.remaining}} {{line.saleUnit}}</template></template><span v-else>发货数量待核查</span></small></td><td><strong>{{ line.fulfillment?.status ? fulfillmentLabel(line.fulfillment.status) : '待履约' }}</strong><small v-if="line.fulfillmentKind === 'REDEEM'" class="order-muted">已核销 {{ line.fulfillment?.redeemedQuantity || 0 }} · 剩余 {{ remaining(line) }}<br>有效至 {{ line.fulfillment?.validUntil || '待确认' }}</small></td><td>{{isPoints?`${line.pointsUnitPrice??'待核查'} 积分`:money(line.goodsAmountFen)}}</td><td>{{isPoints?`${line.pointsTotal??'待核查'} 积分`:money(line.payableFen)}}</td></tr></tbody></table></div><p v-if="isPoints">本单已扣 {{order.exchangePoints}} 积分，现金结算为 0；快递配送已含，不产生消费金额或消费奖励积分。</p><p v-else>商品 {{ money(order.goodsTotalFen) }} + 运费 {{ money(order.shippingFeeFen) }} − 券 {{ money(order.couponDiscountFen) }} − 积分 {{ money(order.pointsDiscountFen) }} = 应付 {{ money(order.payableFen) }}</p><p v-if="order.address">收货：{{ order.address.recipientName }} · {{ order.address.phone }}<br>{{ order.address.province }}{{ order.address.city }}{{ order.address.district }}{{ order.address.detail }}</p><p v-else>本单无需快递地址。</p></section>
      <section v-if="account.permissionCodes.includes('aftersale.read')" class="panel order-section"><div class="fulfillment-heading"><div><h2>售后申请与退款</h2><p>{{isPoints?'按订单项审核退积分申请，经授权确认数量与权益；取消申请沿售后审核处理。':'按订单项审核申请、核对实际退款凭证并由另一名授权人员确认。'}}</p></div><RouterLink :to="{ path: '/aftersales', query: { orderNo: order.orderNo } }" class="secondary-button">查看本单售后</RouterLink></div></section>
      <OrderBenefitsSummary v-if="order.orderBenefits" :summary="order.orderBenefits" :order-kind="order.orderKind" />
      <FulfillmentPanel :account="account" :order="order" @updated="order = $event" />
      <section v-if="order.items.some(line => line.fulfillmentKind === 'REDEEM')" class="panel order-section"><div class="fulfillment-heading"><div><h2>到店核销</h2><p>每个核销商品有独立凭证；PC 使用扫码枪或手工输码，不读取用户端凭证明文。</p></div><RouterLink v-if="account.permissionCodes.includes('fulfillment.redeem')" to="/redemptions" class="primary-button">打开核销台</RouterLink></div><article v-for="line in order.items.filter(item => item.fulfillmentKind === 'REDEEM')" :key="line.orderLineId" class="order-report"><strong>{{ line.name }}</strong><p>购买 {{ line.quantity }} · 已核销 {{ line.fulfillment?.redeemedQuantity || 0 }} · 剩余 {{ remaining(line) }} · {{ fulfillmentLabel(line.fulfillment?.status) }}</p><p>有效至 {{ line.fulfillment?.validUntil || '待确认' }}</p><small class="order-muted">凭证须由顾客在小程序订单详情出示；店员输入核销台后实时核验。</small></article></section>
      <section v-if="isOffline && order.payableFen > 0" class="panel order-section"><h2>付款说明与用户报告</h2><p class="order-instructions">{{ order.paymentInstructions?.instructions || '下单时未配置付款说明。' }}</p><p v-if="!order.paymentReports?.length">用户尚未报告付款。</p><article v-for="report in order.paymentReports" :key="report.reportId" class="order-report"><strong>用户报告 · {{ time(report.reportedAt) }}</strong><p>{{ report.note }}</p><small class="order-muted">此记录为用户自述，不能代替实际到账流水。</small></article></section>
      <section v-if="!isPoints" class="panel order-section"><h2>实际到账记录</h2><p v-if="(order.receiptCount || 0) > 50" class="order-note">共 {{ order.receiptCount }} 条，当前展示最近 50 条。更早记录需由有权限的人员查询审计日志和资金存档核查。</p><p v-if="!order.receipts?.length">{{order.payableFen===0 ? '零现金订单由系统结算，不产生资金到账凭证；履约以服务端结算状态为准。' : '尚无实际到账凭证。确认收款前不能履约。'}}</p><article v-for="receipt in order.receipts" :key="receipt.receiptId" class="order-report"><dl class="order-facts"><div><dt>收款账户</dt><dd>{{ receipt.merchantAccountId }}</dd></div><div><dt>外部流水号</dt><dd>{{ receipt.externalTradeNo }}</dd></div><div><dt>实际金额</dt><dd>{{ money(receipt.amountFen) }}</dd></div><div><dt>实际到账时间</dt><dd>{{ time(receipt.paidAt) }}</dd></div><div><dt>结算时间</dt><dd>{{ time(receipt.appliedAt) }}</dd></div><div><dt>处理状态</dt><dd>{{ receipt.anomaly?.status === 'OPEN' ? `异常 · ${anomalyLabel(receipt.anomaly.reason)}` : receipt.appliedAt ? '已结算' : '待处理' }}</dd></div></dl></article><h3 v-if="order.confirmations?.length">操作员核对记录</h3><p v-if="(order.confirmationCount || 0) > 50" class="order-note">共 {{ order.confirmationCount }} 条，当前展示最近 50 条。</p><article v-for="record in order.confirmations" :key="record.reconciliationId" class="order-report"><p>{{ record.actorName || '授权操作员' }} · {{ time(record.authorizedAt || record.createdAt) }} · {{ outcomeLabel(record.outcome) }}</p><p>{{ record.externalTradeNo }} · {{ money(record.amountFen) }}<br>{{ record.note || '未填写备注' }}</p><button v-if="canConfirm && record.actorId === props.account.accountId && record.authorizedAt && ['AUTHORIZED', 'FAILED', 'PENDING'].includes(record.outcome || '')" class="secondary-button" :disabled="busy" @click="resume(record)">重试这条核对记录</button></article></section>
      <section v-if="isOffline && canConfirm" class="panel order-section"><h2>{{ order.status === 'PENDING_PAYMENT' ? '核对实际流水并确认收款' : '登记额外或迟到到账' }}</h2><p>从银行或收款渠道查询实际入账，核对账户、流水号、金额与时间。用户报告不作为到账依据。</p><p v-if="confirmationRequested" class="order-warning">上次确认结果未知。请查询当前订单，或使用下方按钮重试同一条记录；暂不更改流水内容。</p><form class="order-form" @submit.prevent="prepare"><label>收款账户标识<input v-model="form.merchantAccountId" maxlength="80" autocomplete="off" :disabled="busy || confirmationRequested" placeholder="对应实际入账账户的内部标识"></label><label>实际到账流水号<input v-model="form.externalTradeNo" maxlength="128" autocomplete="off" :disabled="busy || confirmationRequested" placeholder="从银行或渠道账单核对"></label><label>实际到账金额（元）<input v-model="form.amount" inputmode="decimal" autocomplete="off" :disabled="busy || confirmationRequested" placeholder="0.00"></label><label>实际到账时间<input v-model="form.paidAt" type="datetime-local" :disabled="busy || confirmationRequested"></label><label class="wide">核对备注 / 金额差异原因<textarea v-model="form.note" rows="3" maxlength="500" :disabled="busy || confirmationRequested" placeholder="金额不符时必须记录原因"></textarea></label><label class="wide check-row"><input v-model="form.verified" type="checkbox" :disabled="busy || confirmationRequested"><span>已核对实际入账账户、流水号、金额与时间，确认这笔到账对应本订单。</span></label><p v-if="dirty && validation" class="order-note wide">{{ validation }}</p><div class="wide"><button type="submit" class="primary-button" :disabled="busy || (!confirmationRequested && Boolean(validation))">{{ busy ? '处理中…' : confirmationRequested ? '重试同一条核对记录' : '核对完成，进入二次确认' }}</button></div></form></section>
      <p v-else-if="isOffline" class="order-note">当前账号可查看订单，没有线下收款确认权限。</p>
    </template>
    <el-dialog v-model="confirmOpen" title="确认这笔实际到账" class="order-confirm-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @closed="password = ''">
      <template v-if="intent"><p>订单 {{ order?.orderNo }}</p><p>账户：{{ intent.merchantAccountId }}<br>流水：{{ intent.externalTradeNo }}<br>实际金额：<strong>{{ money(intent.amountFen) }}</strong><br>到账：{{ time(intent.paidAt) }}</p><p>{{ intent.amountFen === order?.payableFen && order?.status === 'PENDING_PAYMENT' ? '服务端重新校验后确认付款，并结算库存、券与积分。' : '金额不符、已关闭或额外到账将进入异常处理，不能恢复原单或重复扣减库存。' }}</p></template>
      <label for="receipt-password">当前账号密码<input id="receipt-password" v-model="password" type="password" autocomplete="current-password" :disabled="busy" @keyup.enter="confirmReceipt"></label><p v-if="error" class="error" role="alert">{{ error }}</p><template #footer><div class="dialog-footer"><button class="secondary-button" :disabled="busy" @click="closeConfirm">返回核对</button><button class="primary-button" :disabled="busy || !password" @click="confirmReceipt">{{ busy ? '确认中…' : '授权确认实际到账' }}</button></div></template>
    </el-dialog>
  </section>
</template>
