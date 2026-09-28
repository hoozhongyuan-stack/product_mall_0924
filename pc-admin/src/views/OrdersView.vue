<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Account } from '../api'
import { paymentStatus } from './orders/offline-payment.mjs'
import { fulfillmentLabel, shipmentEligible, shipmentError } from './orders/fulfillment.mjs'
import { money, time, type Carrier, type OrderList } from './orders/types'
import {isPointsOrder,orderSettlementValue,paymentMethodLabel} from '../shared/order-settlement.mjs'
import './orders/orders.css'
const props = defineProps<{ account: Account }>()
const loading = ref(false)
const error = ref('')
const data = ref<OrderList | null>(null)
const page = ref(1)
const status = ref('')
const method = ref('')
const review = ref('')
const fulfillment = ref('')
const search = ref('')
const canShip = computed(() => props.account.permissionCodes.includes('fulfillment.ship'))
const carriers = ref<Carrier[]>([])
const carriersLoading = ref(false)
const carriersError = ref('')
const selected = ref<string[]>([])
const bulkOpen = ref(false)
const bulkBusy = ref(false)
const bulkError = ref('')
const bulkRows = ref<Record<string, { carrierCode: string; trackingNo: string }>>({})
const bulkKeys = ref<Record<string, string>>({})
const bulkResults = ref<{ orderId: string; orderNo?: string; success: boolean; message: string }[]>([])
const eligible = computed(() => data.value?.items.filter(shipmentEligible) || [])
const currentPageSelected = computed(() => eligible.value.length > 0 && eligible.value.every(order => selected.value.includes(order.orderId)))
const activeCarriers = computed(() => carriers.value.filter(carrier => carrier.enabled !== false))
const selectedOrders = computed(() => eligible.value.filter(order => selected.value.includes(order.orderId)))
const bulkValidation = computed(() => selectedOrders.value.map(order => {
  const input = bulkRows.value[order.orderId] || { carrierCode: '', trackingNo: '' }
  return shipmentError(input.carrierCode, input.trackingNo, activeCarriers.value)
}).find(Boolean) || '')
let generation = 0
async function load(reset = false) {
  if (reset) page.value = 1
  const request = ++generation
  loading.value = true; error.value = ''
  try {
    const query = new URLSearchParams({ page: String(page.value), pageSize: '20' })
    if (status.value) query.set('status', status.value)
    if (method.value) query.set('paymentMethod', method.value)
    if (review.value) query.set('reported', review.value)
    if (fulfillment.value) query.set('fulfillment', fulfillment.value)
    if (search.value.trim()) query.set('search', search.value.trim())
    const result = await api<OrderList>(`/orders?${query}`)
    if (request === generation) { data.value = result; selected.value = []; bulkOpen.value = false }
  } catch (reason) { if (request === generation) { data.value = null; error.value = reason instanceof Error ? reason.message : '订单读取失败，请重试。' } }
  finally { if (request === generation) loading.value = false }
}
function resetFilters() { status.value = ''; method.value = ''; review.value = ''; fulfillment.value = ''; search.value = ''; void load(true) }
function changePage(delta: number) { page.value += delta; void load() }
function selectOrder(id: string, checked: boolean) { selected.value = checked ? [...selected.value, id] : selected.value.filter(value => value !== id) }
function selectCurrentPage(checked: boolean) { selected.value = checked ? eligible.value.map(order => order.orderId) : [] }
async function loadCarriers() {
  if (!canShip.value) return
  carriersLoading.value = true; carriersError.value = ''
  try { carriers.value = (await api<{ items: Carrier[] }>('/fulfillment/carriers')).items || [] }
  catch (failure) { carriers.value = []; carriersError.value = failure instanceof Error ? failure.message : '快递公司读取失败。' }
  finally { carriersLoading.value = false }
}
async function openBatch() {
  bulkRows.value = Object.fromEntries(selectedOrders.value.map(order => [order.orderId, { carrierCode: '', trackingNo: '' }]))
  bulkKeys.value = Object.fromEntries(selectedOrders.value.map(order => [order.orderId, crypto.randomUUID()]))
  bulkError.value = ''; bulkResults.value = []; bulkOpen.value = true
  await loadCarriers()
}
async function submitBatch() {
  if (bulkBusy.value || !canShip.value || !selectedOrders.value.length || bulkValidation.value) return
  bulkBusy.value = true; bulkError.value = ''; bulkResults.value = []
  const orders = [...selectedOrders.value]
  try {
    const response = await api<{ results: { orderId: string; success: boolean; message?: string; error?: { message: string } }[] }>('/shipments/batch', {
      method: 'POST', body: JSON.stringify({ items: orders.map(order => ({ orderId: order.orderId, expectedRevision: order.revision, carrierCode: bulkRows.value[order.orderId].carrierCode, trackingNo: bulkRows.value[order.orderId].trackingNo.trim(), requestKey: bulkKeys.value[order.orderId] })) }),
    })
    bulkResults.value = response.results.map(result => ({ orderId: result.orderId, orderNo: orders.find(order => order.orderId === result.orderId)?.orderNo, success: result.success, message: result.message || result.error?.message || (result.success ? '发货成功' : '处理失败') }))
    if (bulkResults.value.length !== orders.length) bulkError.value = '返回结果数量与提交订单不一致，请逐单打开详情核对。'
  } catch (failure) { bulkError.value = `${failure instanceof Error ? failure.message : '批量发货结果未知。'} 请刷新列表或逐单查看详情，避免重复发货。` }
  finally { bulkBusy.value = false }
}
onMounted(() => { void load(); void loadCarriers() })
</script>
<template>
  <section class="page-content orders-page">
    <header class="page-heading"><div><h1>订单管理</h1><p>查看收款与履约进度；批量发货仅处理当前页已选订单，逐单显示结果。</p></div><div class="fulfillment-actions"><RouterLink v-if="account.permissionCodes.includes('aftersale.read')" to="/aftersales" class="secondary-button">售后管理</RouterLink><RouterLink v-if="account.permissionCodes.includes('fulfillment.redeem')" to="/redemptions" class="secondary-button">到店核销</RouterLink><RouterLink v-if="account.permissionCodes.includes('fulfillment.settings.manage')" to="/fulfillment/settings" class="secondary-button">履约设置</RouterLink><button class="secondary-button" :disabled="loading" @click="load()">刷新订单</button></div></header>
    <form class="order-filters" aria-label="筛选订单" @submit.prevent="load(true)">
      <label>订单号<input v-model="search" type="search" placeholder="搜索订单号" /></label>
      <label>履约待办<select v-model="fulfillment" aria-label="履约待办"><option value="">全部待办</option><option value="WAITING_SHIPMENT">待发货</option><option value="IN_TRANSIT">待收货</option><option value="WAITING_REDEMPTION">待核销</option><option value="AFTER_SALE">售后中</option></select></label>
      <label>订单状态<select v-model="status"><option value="">全部状态</option><option value="PENDING_PAYMENT">待付款</option><option value="PAID">已结算（付款 / 扣积分）</option><option value="CLOSED">已关闭</option></select></label>
      <label>结算方式<select v-model="method"><option value="">全部方式</option><option value="OFFLINE">线下支付</option><option value="WECHAT">微信支付</option><option value="POINTS">纯积分兑换</option></select></label>
      <label>核实状态<select v-model="review"><option value="">全部</option><option value="true">用户已报告付款</option><option value="false">未报告付款</option></select></label>
      <button class="primary-button" type="submit" :disabled="loading">查询订单</button>
      <button class="secondary-button" type="button" :disabled="loading" @click="resetFilters">重置</button>
    </form>
    <p v-if="loading" role="status" class="loading-inline">正在读取订单…</p>
    <div v-else-if="error" class="notice" role="alert">{{ error }} <button class="text-button" @click="load()">重新加载</button></div>
    <template v-else-if="data">
      <div v-if="canShip && eligible.length" class="order-bulk-toolbar"><label class="check-row"><input type="checkbox" :checked="currentPageSelected" :disabled="loading" @change="selectCurrentPage(($event.target as HTMLInputElement).checked)">选择当前页待发货订单（{{ eligible.length }} 单）</label><button class="primary-button" :disabled="!selected.length || carriersLoading || !activeCarriers.length" @click="openBatch">批量发货（{{ selected.length }}）</button><p v-if="carriersError" role="alert">{{ carriersError }} <button class="text-button" @click="loadCarriers">重试</button></p><p v-else-if="!carriersLoading && !activeCarriers.length">暂无可用快递公司，请先在履约设置中维护。</p></div>
      <p v-if="!data.items.length" class="empty-state">当前条件下暂无订单。可调整筛选条件后查询。</p>
      <div v-else class="panel table-wrap" tabindex="0" aria-label="订单列表，可横向滚动">
        <table><thead><tr><th v-if="canShip">选择</th><th>订单</th><th>付款截止</th><th>支付方式</th><th>结算状态</th><th>履约状态</th><th>结算金额 / 积分</th><th>操作</th></tr></thead>
          <tbody><tr v-for="order in data.items" :key="order.orderId"><td v-if="canShip"><input v-if="shipmentEligible(order)" type="checkbox" :checked="selected.includes(order.orderId)" :aria-label="`选择订单 ${order.orderNo}`" @change="selectOrder(order.orderId, ($event.target as HTMLInputElement).checked)"><span v-else>—</span></td><td><strong class="order-number">{{ order.orderNo }}</strong><small class="order-muted">{{ time(order.createdAt) }}</small></td><td>{{ isPointsOrder(order) ? '无需付款' : time(order.expiresAt) }}</td><td>{{paymentMethodLabel(order)}}</td><td><span class="badge" :class="order.status === 'PAID' ? 'badge-good' : 'badge-muted'">{{ paymentStatus(order) }}</span></td><td>{{ order.status === 'PAID' ? fulfillmentLabel(order.fulfillmentStatus) : '—' }}</td><td class="order-money">{{orderSettlementValue(order)}}</td><td><RouterLink :to="`/orders/${order.orderId}`" class="text-link">查看详情</RouterLink></td></tr></tbody>
        </table>
      </div>
      <div class="order-pagination"><span>共 {{ data.total }} 单 · 第 {{ page }} 页</span><div><button class="secondary-button" :disabled="page <= 1" @click="changePage(-1)">上一页</button><button class="secondary-button" :disabled="page * data.pageSize >= data.total" @click="changePage(1)">下一页</button></div></div>
    </template>
    <el-dialog v-model="bulkOpen" title="批量发货" class="fulfillment-batch-dialog" :close-on-click-modal="false" :close-on-press-escape="!bulkBusy" :show-close="!bulkBusy"><p>仅提交当前页所选订单。每单填写实际快递公司和运单号；服务端逐单重新检查权限和订单状态。</p><p v-if="carriersLoading" role="status">正在读取可用快递公司…</p><p v-if="!carriersLoading && !activeCarriers.length" class="order-warning">暂无可用快递公司，暂不能发货。</p><div v-for="order in selectedOrders" :key="order.orderId" class="order-bulk-row"><strong>{{ order.orderNo }}</strong><label>快递公司<select v-model="bulkRows[order.orderId].carrierCode" :disabled="bulkBusy || Boolean(bulkResults.length)"><option value="">请选择</option><option v-for="carrier in activeCarriers" :key="carrier.code" :value="carrier.code">{{ carrier.name }}</option></select></label><label>运单号<input v-model="bulkRows[order.orderId].trackingNo" maxlength="80" autocomplete="off" :disabled="bulkBusy || Boolean(bulkResults.length)" placeholder="此单运单号"></label></div><p v-if="bulkValidation && !bulkResults.length" class="order-note">{{ bulkValidation }}</p><div v-if="bulkError" class="order-warning" role="alert">{{ bulkError }}</div><div v-if="bulkResults.length" role="status"><h3>逐单处理结果</h3><p v-for="item in bulkResults" :key="item.orderId" :class="item.success ? 'order-success' : 'order-warning'">{{ item.orderNo || item.orderId }}：{{ item.message }}</p><button class="secondary-button" @click="bulkOpen = false; load()">刷新订单列表</button></div><template #footer><div class="dialog-footer"><button class="secondary-button" :disabled="bulkBusy" @click="bulkOpen = false">{{ bulkResults.length ? '关闭' : '返回' }}</button><button v-if="!bulkResults.length" class="primary-button" :disabled="bulkBusy || !selectedOrders.length || Boolean(bulkValidation)" @click="submitBatch">{{ bulkBusy ? '逐单处理中…' : `确认发货 ${selectedOrders.length} 单` }}</button></div></template></el-dialog>
  </section>
</template>
