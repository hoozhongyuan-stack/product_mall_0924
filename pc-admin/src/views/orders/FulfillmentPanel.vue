<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, type Account } from '../../api'
import { shipmentError, shipmentEligible, fulfillmentLabel, shippingQuantity, shipmentQuantityError } from './fulfillment.mjs'
import { time, type Carrier, type Order } from './types'

const props = defineProps<{ account: Account; order: Order }>()
const emit = defineEmits<{ updated: [order: Order] }>()
const hasShipLines = computed(() => props.order.items.some(line => line.fulfillmentKind === 'SHIP'))
const shipLines=computed(()=>props.order.items.filter(line=>line.fulfillmentKind==='SHIP'))
const quantityError=computed(()=>shipmentQuantityError(props.order.items))
const canShip = computed(() => props.account.permissionCodes.includes('fulfillment.ship'))
const carriers = ref<Carrier[]>([])
const carrierLoading = ref(false)
const carrierError = ref('')
const carrierCode = ref('')
const trackingNo = ref('')
const reason = ref('')
const action = ref<'ship' | 'correct' | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const retryKey = ref('')
const retryBody = ref<Record<string, unknown> | null>(null)
const validCarriers = computed(() => carriers.value.filter(carrier => carrier.enabled !== false))
const validation = computed(() => shipmentError(carrierCode.value, trackingNo.value, validCarriers.value))
const canCorrect = computed(() => canShip.value && Boolean(props.order.shipment))

async function loadCarriers() {
  if (!hasShipLines.value || !canShip.value) return
  carrierLoading.value = true; carrierError.value = ''
  try {
    const data = await api<{ items: Carrier[] }>('/fulfillment/carriers')
    carriers.value = data.items || []
  } catch (failure) {
    carriers.value = []
    carrierError.value = failure instanceof Error ? failure.message : '快递公司读取失败，请重试。'
  } finally { carrierLoading.value = false }
}
function openShip() { if(quantityError.value){error.value=quantityError.value;return} carrierCode.value = ''; trackingNo.value = ''; reason.value = ''; error.value = ''; retryKey.value = ''; retryBody.value = null; action.value = 'ship' }
function openCorrect() {
  carrierCode.value = props.order.shipment?.carrierCode || ''
  trackingNo.value = props.order.shipment?.trackingNo || ''
  reason.value = ''; error.value = ''; retryKey.value = ''; retryBody.value = null; action.value = 'correct'
}
function closeDialog() { if (!busy.value) { action.value = null; error.value = ''; retryKey.value = ''; retryBody.value = null } }
async function submit() {
  if (!action.value || busy.value || !canShip.value || validation.value) return
  if (action.value === 'ship' && (!shipmentEligible(props.order)||quantityError.value)) {error.value=quantityError.value||'订单当前不可发货，请刷新核查。';return}
  if (action.value === 'correct' && (!props.order.shipment || !reason.value.trim())) return
  const body = retryBody.value || {
    carrierCode: carrierCode.value,
    trackingNo: trackingNo.value.trim(),
    expectedRevision: props.order.revision,
    ...(action.value === 'correct' ? { reason: reason.value.trim() } : {}),
  }
  if (action.value === 'ship' && !retryKey.value) retryKey.value = crypto.randomUUID()
  retryBody.value = body
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const path = `/orders/${encodeURIComponent(props.order.orderId)}/shipment${action.value === 'correct' ? '/correct' : ''}`
    const result = await api<Order | { order: Order }>(path, {
      method: 'POST',
      headers: action.value === 'ship' ? { 'Idempotency-Key': retryKey.value } : {},
      body: JSON.stringify(body),
    })
    const updated = 'order' in result ? result.order : result
    emit('updated', updated)
    notice.value = action.value === 'ship' ? '发货已登记，库存不会再次扣减。' : '运单已更正，原信息和更正原因已留存。'
    action.value = null; retryBody.value = null; retryKey.value = ''
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '提交结果未知，请先刷新订单。'
  } finally { busy.value = false }
}
watch(() => props.order.orderId, () => { action.value = null; notice.value = ''; error.value = ''; void loadCarriers() })
onMounted(() => void loadCarriers())
</script>

<template>
  <section v-if="hasShipLines" class="panel order-section" aria-labelledby="shipping-heading">
    <div class="fulfillment-heading"><div><h2 id="shipping-heading">实物发货</h2><p>按下单锁定的发货仓处理；{{ order.orderKind === 'POINTS' ? '兑换成功' : '收款确认' }}时已扣库存，发货不会再次扣减。</p></div><div class="fulfillment-actions"><button class="secondary-button" :disabled="carrierLoading || busy" @click="loadCarriers">刷新快递公司</button><button v-if="canShip && shipmentEligible(order)" class="primary-button" :disabled="carrierLoading || !validCarriers.length || busy || !!quantityError" @click="openShip">登记发货</button><button v-if="canCorrect" class="secondary-button" :disabled="busy" @click="openCorrect">更正运单</button></div></div>
    <template v-if="!order.shipment"><p v-for="line in shipLines" :key="line.orderLineId" class="order-note"><strong>{{line.name}} · {{line.skuCode}}</strong>：<template v-if="shippingQuantity(line)">原购买 {{line.quantity}} {{line.saleUnit}} · 已完成售后退回 {{shippingQuantity(line)!.refunded}} {{line.saleUnit}} · {{line.fulfillment?.status==='AFTER_SALE'?'未发':'本次待发'}} {{shippingQuantity(line)!.remaining}} {{line.saleUnit}}</template><span v-else>发货数量待核查</span></p><p v-if="quantityError" class="order-warning" role="alert">{{quantityError}}</p></template><p v-if="carrierLoading" role="status">正在读取可用快递公司…</p>
    <p v-if="carrierError" role="alert" class="order-warning">{{ carrierError }}</p>
    <p v-else-if="canShip && !carrierLoading && !validCarriers.length && shipmentEligible(order)" class="order-warning">暂无可用快递公司，请先到履约设置维护；暂不能登记发货。</p>
    <p v-if="notice" role="status" class="order-success">{{ notice }}</p>
    <p v-if="!canShip && shipmentEligible(order)" class="order-note">当前账号可查看履约状态，没有发货权限。</p>
    <template v-if="order.shipment"><dl class="order-facts"><div><dt>发货仓</dt><dd>{{ order.shipment.warehouseName || order.shipment.warehouseId || '下单锁定仓库' }}</dd></div><div><dt>快递公司</dt><dd>{{ order.shipment.carrierName }}</dd></div><div><dt>运单号</dt><dd>{{ order.shipment.trackingNo }}</dd></div><div><dt>发货人 / 时间</dt><dd>{{ order.shipment.shippedByName || '授权操作员' }} · {{ time(order.shipment.shippedAt) }}</dd></div><div><dt>确认收货时间</dt><dd>{{ time(order.shipment.confirmedAt) }}</dd></div></dl><p class="order-note">物流查询暂不可用时，快递公司和运单号仍可查看；发货状态不受影响。</p><p v-if="(order.shipment.correctionCount || 0) > 20" class="order-note">共 {{ order.shipment.correctionCount }} 次更正，当前展示最近 20 次。</p><article v-for="(change, index) in order.shipment.corrections || []" :key="index" class="order-report"><strong>运单更正 · {{ time(change.correctedAt) }}</strong><p>{{ change.oldCarrierName }} {{ change.oldTrackingNo }} → {{ change.newCarrierName }} {{ change.newTrackingNo }}</p><p>原因：{{ change.reason }} · {{ change.actorName || '授权操作员' }}</p></article></template>
    <p v-else-if="order.status === 'PAID'">{{ fulfillmentLabel(order.fulfillmentStatus) }} · 尚未发货。</p>
    <p v-else>确认收款后才可发货。</p>
    <el-dialog :model-value="Boolean(action)" :title="action === 'correct' ? '更正快递信息' : '登记整单实物发货'" class="order-confirm-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="closeDialog">
      <p v-if="action === 'ship'">本次只发下列剩余实物数量，使用下单锁定仓库；已完成售后退回数量不发货。请逐项核对实际交寄数量、承运商和运单号。</p>
      <div v-if="action==='ship'"><p v-for="line in shipLines" :key="line.orderLineId"><strong>{{line.name}} · {{line.skuCode}}</strong><br><template v-if="shippingQuantity(line)">原购买 {{line.quantity}} {{line.saleUnit}} · 已退 {{shippingQuantity(line)!.refunded}} {{line.saleUnit}} · <strong>本次待发 {{shippingQuantity(line)!.remaining}} {{line.saleUnit}}</strong></template><span v-else class="order-warning">数量待核查，不能提交发货</span></p><p v-if="quantityError" class="order-warning" role="alert">{{quantityError}}</p></div><p v-else>原记录：{{ order.shipment?.carrierName }} {{ order.shipment?.trackingNo }}。更正记录会保留原值、原因和操作人。</p>
      <div class="order-form fulfillment-dialog-form"><label>快递公司<select v-model="carrierCode" :disabled="busy || Boolean(retryBody)"><option value="">请选择</option><option v-for="carrier in validCarriers" :key="carrier.code" :value="carrier.code">{{ carrier.name }}</option></select></label><label>运单号<input v-model="trackingNo" maxlength="80" autocomplete="off" :disabled="busy || Boolean(retryBody)" placeholder="按实际运单填写"></label><label v-if="action === 'correct'" class="wide">更正原因<textarea v-model="reason" rows="3" maxlength="500" :disabled="busy || Boolean(retryBody)" placeholder="请说明需要更正的原因"></textarea></label></div>
      <p v-if="validation || (action === 'correct' && !reason.trim())" class="order-note">{{ validation || '请输入更正原因。' }}</p>
      <p v-if="error" class="order-warning" role="alert">{{ error }} 请先刷新订单核对；若原操作未生效，可重试当前相同请求。</p>
      <template #footer><div class="dialog-footer"><button class="secondary-button" :disabled="busy" @click="closeDialog">返回</button><button class="primary-button" :disabled="busy || Boolean(validation) || (action === 'correct' && !reason.trim()) || (action === 'ship' && !!quantityError)" @click="submit">{{ busy ? '提交中…' : retryBody ? '重试相同请求' : action === 'correct' ? '确认更正' : '确认发货' }}</button></div></template>
    </el-dialog>
  </section>
</template>
