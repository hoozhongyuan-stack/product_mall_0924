<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { api, confirmedWrite, ApiError, type Account } from '../api'
import { canConfirmTransfer, caseStatus, refundOutcome, refundMethodLabel, reviewError, transferError } from './aftersales/form.mjs'
import { money, time, type AfterSaleCase, type OfflineRefund } from './aftersales/types'
import OrderBenefitsSummary from '../shared/OrderBenefitsSummary.vue'
import BenefitSettlementPanel from './aftersales/BenefitSettlementPanel.vue'
import { refundAmounts } from './aftersales/settlement.mjs'
import ReturnAcceptancePanel from './aftersales/ReturnAcceptancePanel.vue'
import WechatRefundPanel from './aftersales/WechatRefundPanel.vue'
import './orders/orders.css'
import {isPointsOrder} from '../shared/order-settlement.mjs'
import {pointRefundState} from './aftersales/point-refund-state.mjs'
import './aftersales/aftersales.css'
const props = defineProps<{ account: Account }>()
const route = useRoute()
const data = ref<AfterSaleCase | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const reviewDecision = ref<boolean | null>(null)
const reviewReason = ref('')
const dialog = ref<'review' | 'refund' | ''>('')
const password = ref('')
const verifiedAgain = ref(false)
const transfer = ref({ externalRefundNo: '', amount: '', refundedAt: '', refundMethod: 'BANK_TRANSFER', proofReference: '', note: '', verified: false })
const requestKey = ref('')
const pendingBody = ref<Record<string, unknown> | null>(null)
const outcomeUnknown = ref(false)
let generation = 0
const permission = (code: string) => props.account.permissionCodes.includes(code)
const canReview = computed(() => !outcomeUnknown.value && data.value?.status === 'PENDING_REVIEW' && permission('aftersale.review'))
const canPrepare = computed(() => data.value?.status === 'WAITING_REFUND' && data.value.paymentMethod === 'OFFLINE' && (data.value.totalRefundAmountFen ?? data.value.effectiveRefundAmountFen ?? data.value.amountFen) > 0 && !data.value.offlineRefund && permission('refund.prepare'))
const canConfirm = computed(() => !outcomeUnknown.value && canConfirmTransfer(data.value?.offlineRefund, props.account.accountId, props.account.permissionCodes))
const reviewValidation = computed(() => reviewError(reviewDecision.value, reviewReason.value))
const amounts = computed(() => data.value ? refundAmounts(data.value) : {goods:0,shipping:0,total:0,zeroCash:false})
const approvedAmount = computed(() => amounts.value.total)
const transferValidation = computed(() => transferError(transfer.value, approvedAmount.value))
const dirty = computed(() => Boolean(reviewReason.value || transfer.value.externalRefundNo || transfer.value.note || transfer.value.proofReference || pendingBody.value || outcomeUnknown.value))
const failure = (value: unknown) => value instanceof Error ? value.message : '请求结果未知，请刷新查询或重试同一条记录。'
async function loadId(id: string) {
  const ticket = ++generation
  loading.value = true; error.value = ''; data.value = null
  try {
    const result = await api<AfterSaleCase>(`/aftersales/${id}`)
    if (ticket !== generation) return
    refundAmounts(result)
    data.value = result
    outcomeUnknown.value = false
    if (result.offlineRefund) { pendingBody.value = null; requestKey.value = '' }
    if (result.status !== 'PENDING_REVIEW') reviewReason.value = ''
    if (result.status === 'COMPLETED') outcomeUnknown.value = false
  } catch (reason) { if (ticket === generation) error.value = failure(reason) }
  finally { if (ticket === generation) loading.value = false }
}
function load() { return loadId(String(route.params.caseId)) }
function resetForm() {
  reviewReason.value = ''; reviewDecision.value = null
  transfer.value = { externalRefundNo: '', amount: '', refundedAt: '', refundMethod: 'BANK_TRANSFER', proofReference: '', note: '', verified: false }
  pendingBody.value = null; requestKey.value = ''; outcomeUnknown.value = false; notice.value = ''; dialog.value = ''; password.value = ''; verifiedAgain.value = false
}
function toggleDialog(value: boolean) { if (!value && !busy.value) dialog.value = '' }
function openDialog(value: 'review' | 'refund') {
  password.value = ''; verifiedAgain.value = false; error.value = ''
  if (value === 'review' && canReview.value && !reviewValidation.value) dialog.value = value
  if (value === 'refund' && canConfirm.value) dialog.value = value
}
async function prepareTransfer() {
  if (busy.value || !data.value || !canPrepare.value || (!pendingBody.value && transferValidation.value)) return
  if (!pendingBody.value) {
    if (!data.value.refundSource?.merchantAccountId) { error.value = '未取得原收款账户，请刷新后核查原收款凭证。'; return }
    requestKey.value = crypto.randomUUID()
    pendingBody.value = { expectedRevision: data.value.revision, merchantAccountId: data.value.refundSource.merchantAccountId,
      externalRefundNo: transfer.value.externalRefundNo.trim(), amountFen: approvedAmount.value,
      refundedAt: new Date(transfer.value.refundedAt).toISOString(), refundMethod: transfer.value.refundMethod.trim(), proofReference: transfer.value.proofReference.trim(), note: transfer.value.note.trim(), verified: true }
  }
  busy.value = true; error.value = ''
  try {
    const row = await api<OfflineRefund>(`/aftersales/${data.value.caseId}/offline-refunds`, { method: 'POST', headers: { 'Idempotency-Key': requestKey.value }, body: JSON.stringify(pendingBody.value) })
    data.value = { ...data.value, offlineRefund: row }
    pendingBody.value = null; requestKey.value = ''; transfer.value.externalRefundNo = ''; transfer.value.note = ''; transfer.value.proofReference = ''
    notice.value = '退款流水已登记，尚未确认完成。请另一名有权限的人员独立核对实际转账记录。'
    await load()
  } catch (reason) {
    if (reason instanceof ApiError && reason.status < 500) {
      pendingBody.value = null; requestKey.value = ''
      if (reason.status === 409) await load()
      error.value = `${failure(reason)} 请核查最新状态与登记内容。`
    } else error.value = `${failure(reason)} 登记内容已固定，请重试同一记录或刷新核查。`
  }
  finally { busy.value = false }
}
async function confirm() {
  if (busy.value || !data.value || !password.value) return
  const current = data.value
  if (dialog.value === 'review' && (!canReview.value || reviewValidation.value)) return
  if (dialog.value === 'refund' && (!canConfirm.value || !verifiedAgain.value || !current.offlineRefund)) return
  busy.value = true; error.value = ''; outcomeUnknown.value = true
  try {
    if (dialog.value === 'review') {
      await confirmedWrite('aftersale.review', password.value, `/aftersales/${current.caseId}/review`, 'POST',
        { approve: reviewDecision.value, reason: reviewReason.value.trim(), expectedRevision: current.revision }, current.caseId, current.revision)
      reviewReason.value = ''; outcomeUnknown.value = false; notice.value = '审核结果已保存，审核同意不代表退款已完成。'
    } else {
      const row = current.offlineRefund!
      const result = await confirmedWrite<{ outcome: string }>('refund.offline.confirm', password.value,
        `/refunds/offline-reconciliations/${row.reconciliationId}/confirm`, 'POST', {}, row.confirmationObjectId, row.confirmationRevision)
      outcomeUnknown.value = false; notice.value = refundOutcome(result.outcome)
    }
    dialog.value = ''; password.value = ''; verifiedAgain.value = false
    await load()
  } catch (reason) {
    outcomeUnknown.value = !(reason instanceof ApiError && reason.status < 500)
    error.value = `${failure(reason)} ${outcomeUnknown.value ? '请先刷新查询最新状态，再处理这条已保存记录。' : '请核查输入、权限与最新处理状态。'}`
    if (outcomeUnknown.value) dialog.value = ''
    password.value = ''
  }
  finally { busy.value = false }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || busy.value) { event.preventDefault(); event.returnValue = '' } }
async function mayLeave() { return !busy.value && (!dirty.value || await confirmAction('处理尚未结束。离开后请从售后详情核查最新记录，确定离开？')) }
onBeforeRouteLeave(mayLeave)
onBeforeRouteUpdate(mayLeave)
watch(() => route.params.caseId, id => { resetForm(); void loadId(String(id)) })
onMounted(() => { void load(); window.addEventListener('beforeunload', beforeUnload) })
onUnmounted(() => { generation += 1; password.value = ''; window.removeEventListener('beforeunload', beforeUnload) })
</script>
<template>
  <section class="page-content orders-page aftersales-page">
    <header class="page-heading"><div><RouterLink to="/aftersales" class="text-link">返回售后列表</RouterLink><h1>售后详情</h1><p>申请、审核和退款各自留痕；有现金退款须核实资金，零现金售后须确认数量与权益处理。</p></div><button class="secondary-button" :disabled="loading || busy" @click="load">刷新处理状态</button></header>
    <p v-if="loading" role="status">正在读取售后详情…</p>
    <div v-if="error" class="notice" role="alert">{{ error }} <button v-if="!data" class="text-button" @click="load">重新加载</button></div>
    <p v-if="notice" class="order-success" role="status">{{ notice }}</p>
    <template v-if="data && !loading">
      <section class="panel order-section"><div class="fulfillment-heading"><h2>{{ caseStatus(data.status) }}</h2><RouterLink :to="`/orders/${data.orderId}`" class="text-link">查看原订单</RouterLink></div><dl class="order-facts"><div><dt>订单号</dt><dd>{{ data.orderNo }}</dd></div><div><dt>申请商品</dt><dd>{{ data.title }}</dd></div><div><dt>申请类型 / 范围</dt><dd>{{ isPointsOrder(data) ? (data.kind === 'RETURN_REFUND' ? '退货退积分' : '仅退积分') : (data.kind === 'RETURN_REFUND' ? '退货退款' : '仅退款') }} · {{ data.kind === 'RETURN_REFUND' ? '已发货部分' : data.redemptionScope === 'USED' ? '已核销部分' : '未履约部分' }}</dd></div><div><dt>申请数量</dt><dd>{{ data.quantity }}</dd></div><div><dt>{{isPointsOrder(data)?'申请退回积分':'申请商品退款金额'}}</dt><dd class="order-money">{{isPointsOrder(data)?`${data.requestedRefundPoints??'待核查'} 积分`:money(data.amountFen)}}</dd></div><div><dt>申请时间</dt><dd>{{ time(data.createdAt) }}</dd></div></dl><h3>申请原因与说明</h3><p class="order-instructions">{{ data.reason }}</p><dl v-if="isPointsOrder(data)" class="order-facts"><div><dt>原订单项兑换积分</dt><dd>{{data.exchangePoints??'待核查'}} 积分</dd></div><div><dt>{{pointRefundState(data).label}}</dt><dd>{{pointRefundState(data).value}}</dd></div><div><dt>剩余可申请积分</dt><dd>{{data.refundablePoints??'待核查'}} 积分</dd></div><div><dt>此订单项累计已退积分</dt><dd>{{data.returnedPoints??'待核查'}} 积分</dd></div></dl><dl v-else class="order-facts"><div><dt>本笔商品退款金额</dt><dd>{{ money(amounts.goods) }}</dd></div><div><dt>独立运费退款</dt><dd>{{ money(amounts.shipping) }}</dd></div><div><dt>本笔现金退款合计</dt><dd class="order-money">{{ money(amounts.total) }}</dd></div></dl><p class="order-note">{{isPointsOrder(data)?'纯积分兑换售后没有现金转出；批准或验收后，经授权确认数量与权益才完成退积分。':'商品和运费分别核算，资金凭证须与本笔现金退款合计一致。零现金售后没有现金转出。'}}</p></section>
      <section v-if="data.storeNotes?.length" class="panel order-section"><h2>门店处理记录</h2><p class="order-note">门店提供的收货数量与处理意见供平台审核，退款和库存回补以平台验收结果为准。</p><article v-for="record in data.storeNotes" :key="record.id" class="order-instructions"><h3>{{ record.kind === 'RECEIPT' ? '退货收货记录' : '售后处理意见' }}</h3><p>{{ record.note }}</p><p v-if="record.kind === 'RECEIPT'">已收货 {{ record.receivedQuantity }} 件 · 门店检查可售 {{ record.salableQuantity }} 件 · 待平台审核</p><small>{{ time(record.occurredAt) }}</small></article></section>
      <section v-if="canReview" class="panel order-section"><h2>审核申请</h2><p>核对购买数量、履约记录与申请依据。同意后进入退款或待退货处理，{{isPointsOrder(data)?'当前不会自动退积分。':'当前不会自动退钱。'}}</p><form class="order-form" @submit.prevent="openDialog('review')"><label>审核决定<select v-model="reviewDecision" :disabled="busy"><option :value="null">请选择</option><option :value="true">同意申请</option><option :value="false">拒绝申请</option></select></label><label class="wide">审核依据<textarea v-model="reviewReason" :disabled="busy" rows="3" maxlength="500" placeholder="填写 5 至 500 字，将向顾客展示"></textarea></label><p class="wide order-note">{{ reviewValidation || '确认时需输入当前账号密码。' }}</p><div class="wide"><button class="primary-button" :disabled="busy || Boolean(reviewValidation)">核对完成，确认审核</button></div></form></section>
      <p v-else-if="data.status === 'PENDING_REVIEW'" class="order-note">当前账号没有售后审核权限。</p>
      <ReturnAcceptancePanel v-if="data.kind === 'RETURN_REFUND'" :key="data.caseId" :item="data" :account="account" @refresh="load" @busy="busy = $event" />
      <BenefitSettlementPanel v-if="amounts.zeroCash" :key="data.caseId" :item="data" :account="account" @refresh="load" @busy="busy = $event" />
      <WechatRefundPanel v-if="data.paymentMethod === 'WECHAT' && !amounts.zeroCash" :key="data.caseId" :item="data" :account="account" @refresh="load" @busy="busy = $event" />
      <section v-if="canPrepare" class="panel order-section"><h2>登记实际线下退款凭证</h2><p>请先执行并核对实际转账，再登记渠道流水和可追溯凭证。登记不会改变售后完成状态，另一名授权人员复核后才能完成。</p><dl class="order-facts"><div><dt>原收款账户标识</dt><dd>{{ data.refundSource?.merchantAccountId || '未取得，请刷新核查' }}</dd></div><div><dt>原收款流水</dt><dd>{{ data.refundSource?.originalTradeNo || '未取得，请刷新核查' }}</dd></div><div><dt>本笔现金退款合计</dt><dd>{{ money(approvedAmount) }}</dd></div></dl><form class="order-form aftersale-transfer" @submit.prevent="prepareTransfer"><label>实际退款流水号<input v-model="transfer.externalRefundNo" maxlength="128" :disabled="busy || Boolean(pendingBody)" autocomplete="off"></label><label>实际退款金额（元）<input v-model="transfer.amount" inputmode="decimal" :disabled="busy || Boolean(pendingBody)" placeholder="须与本笔现金退款合计一致"></label><label>实际转账时间<input v-model="transfer.refundedAt" type="datetime-local" :disabled="busy || Boolean(pendingBody)"></label><label>退款方式<select v-model="transfer.refundMethod" :disabled="busy || Boolean(pendingBody)"><option value="BANK_TRANSFER">银行转账</option><option value="WECHAT_TRANSFER">微信转账</option><option value="ALIPAY_TRANSFER">支付宝转账</option><option value="CASH">现金</option><option value="OTHER">其他</option></select></label><label class="wide">转账凭证档案号<input v-model="transfer.proofReference" maxlength="128" :disabled="busy || Boolean(pendingBody)" placeholder="店铺留存的银行回单号或凭证档案号"></label><label class="wide">核对说明<textarea v-model="transfer.note" rows="3" maxlength="500" :disabled="busy || Boolean(pendingBody)" placeholder="填写店铺留存的凭证档案号和核对依据"></textarea></label><label class="wide check-row"><input v-model="transfer.verified" type="checkbox" :disabled="busy || Boolean(pendingBody)"><span>已核对实际转出记录、收退款账户、流水、金额、时间和凭证。</span></label><p class="wide order-note">{{ busy ? '正在保存核对记录…' : pendingBody ? '上次登记结果未知，内容已固定；请重试同一条记录或刷新核查。' : transferValidation }}</p><div class="wide"><button class="primary-button" :disabled="busy || !data.refundSource?.merchantAccountId || (!pendingBody && Boolean(transferValidation))">{{ busy ? '登记中…' : pendingBody ? '重试同一登记记录' : '保存退款凭证，交由另一人复核' }}</button></div></form></section>
      <p v-else-if="data.status === 'WAITING_REFUND' && data.paymentMethod === 'OFFLINE' && !amounts.zeroCash && !data.offlineRefund" class="order-note">当前账号没有线下退款凭证登记权限。</p>
      <section v-if="data.offlineRefund" class="panel order-section"><h2>实际退款核对记录</h2><p class="order-warning">{{ refundOutcome(data.offlineRefund.outcome) }}</p><dl class="order-facts"><div><dt>登记人员</dt><dd>{{ data.offlineRefund.preparedByName }}</dd></div><div><dt>收款账户标识</dt><dd>{{ data.offlineRefund.merchantAccountId }}</dd></div><div><dt>退款流水号</dt><dd>{{ data.offlineRefund.externalRefundNo }}</dd></div><div><dt>实际退款金额</dt><dd>{{ money(data.offlineRefund.amountFen) }}</dd></div><div><dt>转账时间</dt><dd>{{ time(data.offlineRefund.refundedAt) }}</dd></div><div><dt>退款方式</dt><dd>{{ refundMethodLabel(data.offlineRefund.refundMethod) }}</dd></div></dl><h3>转账凭证与核对说明</h3><p>{{ data.offlineRefund.proofReference }}</p><p class="order-instructions">{{ data.offlineRefund.note }}</p><p v-if="data.offlineRefund.authorizedByName">复核：{{ data.offlineRefund.authorizedByName }} · {{ time(data.offlineRefund.authorizedAt) }}</p><p v-if="outcomeUnknown" class="order-warning">上次确认结果未知，请刷新核查最新状态；只可处理这条已保存凭证。</p><button v-if="canConfirm" class="primary-button" :disabled="busy" @click="openDialog('refund')">独立核对完成，授权确认退款</button><p v-else-if="data.offlineRefund.preparedById === account.accountId && data.status !== 'COMPLETED'" class="order-note">你是这条退款凭证的登记人，请另一名有退款确认权限的人员复核。</p><p v-else-if="data.status !== 'COMPLETED'" class="order-note">当前记录不可确认。请核查权限、异常和最新处理结果。</p></section>
      <OrderBenefitsSummary v-if="data.orderBenefits" :summary="data.orderBenefits" :order-kind="data.orderKind" />
      <section class="panel order-section"><h2>处理记录</h2><p v-if="!data.events.length">暂无处理记录。</p><p v-if="(data.eventCount || 0) > data.events.length">当前显示最近 {{ data.events.length }} 条，历史记录保留在审计存档。</p><ol class="aftersale-timeline"><li v-for="(event, index) in data.events" :key="index"><strong>{{ caseStatus(event.status) }}</strong><span>{{ time(event.occurredAt) }}{{ event.actorName ? ` · ${event.actorName}` : '' }}</span><p>{{ event.reason }}</p></li></ol></section>
    </template>
    <el-dialog :model-value="Boolean(dialog)" :title="dialog === 'review' ? '授权确认审核结果' : '授权确认实际退款'" class="order-confirm-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="toggleDialog" @closed="password = ''; verifiedAgain = false">
      <template v-if="data"><p>订单 {{ data.orderNo }} · {{ data.title }}<br>{{ dialog === 'review' ? data.quantity : (data.effectiveRefundQuantity ?? data.quantity) }} 件 · {{isPointsOrder(data)?`${dialog==='review'?data.requestedRefundPoints??'待核查':data.pointsToReturn??'待核查'} 积分`:money(dialog === 'review' ? data.amountFen : approvedAmount)}}</p><template v-if="dialog === 'review'"><p>决定：{{ reviewDecision ? '同意' : '拒绝' }}<br>依据：{{ reviewReason }}</p><p>同意后保持待退款或待退货；拒绝会释放本笔售后占用。</p></template><template v-else-if="data.offlineRefund"><p>退款账户：{{ data.offlineRefund.merchantAccountId }}<br>流水：{{ data.offlineRefund.externalRefundNo }}<br>转账：{{ time(data.offlineRefund.refundedAt) }}</p><label class="check-row"><input v-model="verifiedAgain" type="checkbox" :disabled="busy"><span>已独立核对实际退款记录，确认资金已退回且金额与本笔现金退款合计一致。</span></label><p>确认资金退回后完成售后；未发货库存或未核销额度按规则处理，已验收回库不会再次回库。</p></template></template>
      <label for="refund-password">当前账号密码<input id="refund-password" v-model="password" type="password" autocomplete="current-password" :disabled="busy" @keyup.enter="confirm"></label><p v-if="error" class="error" role="alert">{{ error }}</p><template #footer><div class="dialog-footer"><button class="secondary-button" :disabled="busy" @click="dialog = ''">返回核对</button><button class="primary-button" :disabled="busy || !password || (dialog === 'refund' && !verifiedAgain)" @click="confirm">{{ busy ? '确认中…' : dialog === 'review' ? '授权保存审核结果' : '授权确认资金已退回' }}</button></div></template>
    </el-dialog>
  </section>
</template>
