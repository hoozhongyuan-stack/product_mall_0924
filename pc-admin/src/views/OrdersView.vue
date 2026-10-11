<script setup lang="ts">
import {csvTable} from '../shared/csv.mjs'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, type Account } from '../api'
import { paymentStatus } from './orders/offline-payment.mjs'
import { fulfillmentLabel, shipmentEligible, shipmentError } from './orders/fulfillment.mjs'
import { time, type Carrier, type OrderList } from './orders/types'
import {isPointsOrder,orderSettlementValue,paymentMethodLabel} from '../shared/order-settlement.mjs'
import './orders/orders.css'
import OrderActionDialog from './orders/OrderActionDialog.vue'
import {orderActions,actionLabels,pendingActions,actionPermission,type OrderAction} from './orders/order-actions.mjs'
import type {Order} from './orders/types'
import {confirmAction} from '../shared/confirm'
import AdminListColumns from '../shared/list/AdminListColumns.vue'
import AdminListPager from '../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import OrderDetailDialog from './orders/OrderDetailDialog.vue'
const props = defineProps<{ account: Account }>()
const action=ref<OrderAction|null>(null),actionOrderId=ref(''),actionNotice=ref('')
function recoveries(order:Order){return pendingActions(props.account.accountId,order.orderId,sessionStorage).filter(a=>props.account.permissionCodes.includes(actionPermission(a)))}
function openAction(order:Order,chosen:OrderAction){actionOrderId.value=order.orderId;action.value=chosen;actionNotice.value=''}
function updatedOrder(updated:Order){if(data.value)data.value={...data.value,items:data.value.items.map(row=>row.orderId===updated.orderId?updated:row)}}
watch(()=>props.account.accountId,()=>{action.value=null;actionOrderId.value='';detailOrderId.value='';exportOpen.value=false;bulkGeneration++;bulkBusy.value=false;bulkOpen.value=false;bulkSnapshot.value=[];data.value=null;selected.value=[];void load()})
const loading = ref(false)
const error = ref('')
const data = ref<OrderList | null>(null)
const page = ref(1)
const pageSize = ref(20)
const detailOrderId = ref('')
const exportOpen = ref(false)
const hiddenColumns = ref<string[]>([])
const listColumns = [{id:'identity',label:'订单',required:true},{id:'ownership',label:'订单归属'},{id:'warehouse',label:'前置仓 / 仓库'},{id:'expiry',label:'付款截止'},{id:'payment',label:'支付方式'},{id:'settlement',label:'结算状态'},{id:'fulfillment',label:'履约状态'},{id:'amount',label:'结算金额 / 积分'},{id:'actions',label:'操作',required:true}]
const visibleColumn = (id: string) => !hiddenColumns.value.includes(id)
const status = ref('')
const method = ref('')
const review = ref('')
const fulfillment = ref('')
const search = ref('')
const storeId = ref('')
const source = ref('')
const restaurantId = ref('')
const restaurants = ref<{id:string;name:string}[]>([])
const restaurantError = ref('')
async function loadRestaurants(){if(!props.account.permissionCodes.includes('dining.read'))return;try{restaurants.value=(await api<{items:{id:string;name:string}[]}>('/dining-alliance/restaurants?pageSize=100')).items;restaurantError.value=''}catch(e){restaurantError.value=e instanceof Error?e.message:'餐饮店筛选读取失败。'}}
const stores = ref<{id:string;name:string}[]>([])
const storeError = ref('')
async function loadStores(){if(!props.account.permissionCodes.includes('stores.read'))return;try{stores.value=(await api<{items:{id:string;name:string}[]}>('/stores?pageSize=100')).items;storeError.value=''}catch(e){storeError.value=e instanceof Error?e.message:'前置仓筛选读取失败。'}}
const canShip = computed(() => props.account.permissionCodes.includes('fulfillment.ship'))
const carriers = ref<Carrier[]>([])
const carriersLoading = ref(false)
const carriersError = ref('')
const selected = ref<string[]>([])
const bulkOpen = ref(false)
const bulkBusy = ref(false)
const bulkError = ref('')
const bulkSubmitted = ref(false)
const bulkSnapshot = ref<Order[]>([])
const bulkEligibleIds = ref<string[]>([])
const bulkRows = ref<Record<string, { carrierCode: string; trackingNo: string }>>({})
const bulkKeys = ref<Record<string, string>>({})
const bulkResults = ref<{ orderId: string; orderNo?: string; success: boolean; message: string }[]>([])
const currentPageSelected = computed(() => Boolean(data.value?.items.length) && data.value!.items.every(order => selected.value.includes(order.orderId)))
const activeCarriers = computed(() => carriers.value.filter(carrier => carrier.enabled !== false))
const selectedOrders = computed(() => data.value?.items.filter(order => selected.value.includes(order.orderId)) || [])
function canBatchShip(order: Order) { return shipmentEligible(order) && !pendingActions(props.account.accountId, order.orderId, sessionStorage).includes('ship') }
const bulkEligible = computed(() => bulkSnapshot.value.filter(order => bulkEligibleIds.value.includes(order.orderId)))
const bulkExcluded = computed(() => bulkSnapshot.value.filter(order => !bulkEligibleIds.value.includes(order.orderId)))
function exclusionReason(order: Order) {
  if (pendingActions(props.account.accountId, order.orderId, sessionStorage).includes('ship')) return '上次发货结果待核查，请先核查原请求'
  if (order.status !== 'PAID') return order.status === 'CLOSED' ? '订单已关闭' : '待收款，不能发货'
  if (order.deliveryMode === 'DELIVERY') return '前置仓配送，请逐单处理交付'
  if (order.deliveryMode === 'PICKUP') return '到店自提，请逐单确认交付'
  if (['AFTER_SALE', 'REFUNDED'].includes(order.fulfillmentStatus || '')) return '售后或退款处理中，不能发货'
  return '当前订单没有可发货项目，请查看详情核对'
}
const bulkValidation = computed(() => bulkEligible.value.map(order => {
  const input = bulkRows.value[order.orderId] || { carrierCode: '', trackingNo: '' }
  return shipmentError(input.carrierCode, input.trackingNo, activeCarriers.value)
}).find(Boolean) || '')
let generation = 0
let bulkGeneration = 0
async function closeBatchDialog(done: () => void) {
  if (bulkBusy.value) return
  const closingGeneration = bulkGeneration
  const dirty = !bulkSubmitted.value && Object.values(bulkRows.value).some(row => row.carrierCode || row.trackingNo.trim())
  if (dirty && !await confirmAction('发货资料尚未提交，关闭将丢弃本次填写，是否继续？')) return
  if (!bulkBusy.value && closingGeneration === bulkGeneration) { done(); if (bulkSubmitted.value) void load() }
}
async function load(reset = false) {
  if (reset) page.value = 1
  const request = ++generation
  selected.value = []
  loading.value = true; error.value = ''
  try {
    const query = new URLSearchParams({ page: String(page.value), pageSize: String(pageSize.value) })
    if (source.value) query.set('source',source.value)
    if (restaurantId.value) query.set('restaurantId',restaurantId.value)
    if (storeId.value) query.set('storeId',storeId.value)
    if (status.value) query.set('status', status.value)
    if (method.value) query.set('paymentMethod', method.value)
    if (review.value) query.set('reported', review.value)
    if (fulfillment.value) query.set('fulfillment', fulfillment.value)
    if (search.value.trim()) query.set('search', search.value.trim())
    const result = await api<OrderList>(`/orders?${query}`)
    if (request === generation) { data.value = result; if (Number.isInteger(result.page)) page.value = result.page; selected.value = []; bulkOpen.value = false }
  } catch (reason) { if (request === generation) { data.value = null; error.value = reason instanceof Error ? reason.message : '订单读取失败，请重试。' } }
  finally { if (request === generation) loading.value = false }
}
function resetFilters() { source.value='';restaurantId.value='';storeId.value = ''; status.value = ''; method.value = ''; review.value = ''; fulfillment.value = ''; search.value = ''; void load(true) }
function changePage(next: number, size: number) { page.value = next; pageSize.value = size; void load() }
watch([status, method, review, fulfillment, search, storeId, source, restaurantId], () => { selected.value = [] })
function exportSelected() {
  if (!selectedOrders.value.length) return
  const text = csvTable([['订单号','创建时间','订单来源','餐饮店','前置仓','支付方式','结算状态','履约状态','结算金额 / 积分'], ...selectedOrders.value.map(order => [order.orderNo, time(order.createdAt), order.source === 'DINING' || order.dining ? '餐饮联盟' : '普通商城', order.dining?.name || '', order.storeName || '平台订单', paymentMethodLabel(order), paymentStatus(order), order.status === 'PAID' ? fulfillmentLabel(order.fulfillmentStatus) : '—', orderSettlementValue(order)])])
  const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}))
  const link=document.createElement('a');link.href=url;link.download=`orders-selected-${new Date().toISOString().slice(0,10)}.csv`;document.body.append(link);link.click();link.remove();window.setTimeout(()=>URL.revokeObjectURL(url),0);exportOpen.value=false
}
function selectOrder(id: string, checked: boolean) { selected.value = checked ? [...selected.value, id] : selected.value.filter(value => value !== id) }
function selectCurrentPage(checked: boolean) { selected.value = checked ? (data.value?.items.map(order => order.orderId) || []) : [] }
async function loadCarriers() {
  if (!canShip.value) return
  carriersLoading.value = true; carriersError.value = ''
  try { carriers.value = (await api<{ items: Carrier[] }>('/fulfillment/carriers')).items || [] }
  catch (failure) { carriers.value = []; carriersError.value = failure instanceof Error ? failure.message : '快递公司读取失败。' }
  finally { carriersLoading.value = false }
}
async function openBatch() {
  if (bulkBusy.value) return
  bulkGeneration++
  bulkSnapshot.value = [...selectedOrders.value]
  bulkEligibleIds.value = bulkSnapshot.value.filter(canBatchShip).map(order => order.orderId)
  bulkSubmitted.value = false
  bulkRows.value = Object.fromEntries(bulkEligible.value.map(order => [order.orderId, { carrierCode: '', trackingNo: '' }]))
  bulkKeys.value = Object.fromEntries(bulkEligible.value.map(order => [order.orderId, crypto.randomUUID()]))
  bulkError.value = ''; bulkResults.value = []; bulkOpen.value = true
  await loadCarriers()
}
async function submitBatch() {
  if (bulkBusy.value || bulkSubmitted.value || !canShip.value || !bulkEligible.value.length || bulkValidation.value) return
  if (bulkEligible.value.some(order => !canBatchShip(order))) { bulkError.value = '存在上次发货结果待核查的订单，请关闭后重新选择。'; return }
  bulkBusy.value = true; bulkError.value = ''; bulkResults.value = []
  const orders = [...bulkEligible.value]
  const excluded = bulkExcluded.value.map(order => ({ orderId: order.orderId, orderNo: order.orderNo, success: false, message: `未执行：${exclusionReason(order)}` }))
  const owner = props.account.accountId
  const batchRequest = bulkGeneration
  type ShipmentResult = { orderId: string; success: boolean; message?: string; error?: { message: string } }
  let acknowledged: ShipmentResult[] = []
  try {
    // Share the existing single-order recovery record so an unknown batch item
    // can be checked and retried with its original idempotency key.
    const records = orders.map(order => ({ key: `mall-order-action:${owner}:${order.orderId}:ship`, value: {
      actor: owner, orderId: order.orderId, action: 'ship', key: bulkKeys.value[order.orderId],
      path: `/orders/${order.orderId}/shipment`, phase: 'write', body: {
        expectedRevision: order.revision, carrierCode: bulkRows.value[order.orderId]!.carrierCode,
        trackingNo: bulkRows.value[order.orderId]!.trackingNo.trim(),
      },
    } }))
    try { records.forEach(record => sessionStorage.setItem(record.key, JSON.stringify(record.value))) }
    catch { records.forEach(record => { try { sessionStorage.removeItem(record.key) } catch { /* browser storage remains unavailable */ } }); bulkError.value = '浏览器存储不可用，暂不能提交；请恢复存储后重试。'; return }
    bulkSubmitted.value = true
    // The service accepts at most 50 items; process a 100-row page as two
    // bounded batches, keeping per-order identities and acknowledged results.
    for (let offset = 0; offset < orders.length; offset += 50) {
      const chunk = orders.slice(offset, offset + 50)
      const response = await api<{ results: ShipmentResult[] }>('/shipments/batch', {
        method: 'POST', body: JSON.stringify({ items: chunk.map(order => ({ orderId: order.orderId, expectedRevision: order.revision, carrierCode: bulkRows.value[order.orderId]!.carrierCode, trackingNo: bulkRows.value[order.orderId]!.trackingNo.trim(), requestKey: bulkKeys.value[order.orderId] })) }),
      })
      acknowledged = [...acknowledged, ...response.results]
      records.filter(record => chunk.some(order => order.orderId === record.value.orderId)).forEach(record => {
        const matches = response.results.filter(result => result.orderId === record.value.orderId)
        if (matches.length === 1) { try { sessionStorage.removeItem(record.key) } catch { /* retain recovery identity if storage is unavailable */ } }
      })
      if (owner !== props.account.accountId || batchRequest !== bulkGeneration) return
    }
    bulkResults.value = [...orders.map(order => {
      const matches = acknowledged.filter(result => result.orderId === order.orderId)
      const result = matches.length === 1 ? matches[0] : undefined
      return { orderId: order.orderId, orderNo: order.orderNo, success: Boolean(result?.success), message: result ? result.message || result.error?.message || (result.success ? '发货成功' : '处理失败') : '结果未知，请逐单查看详情核对' }
    }), ...excluded]
    if (acknowledged.length !== orders.length || bulkResults.value.some(result => result.message.includes('未知'))) bulkError.value = '返回结果与提交订单不一致，请逐单打开详情核对；不能直接重发。'
  } catch (failure) { if (owner !== props.account.accountId || batchRequest !== bulkGeneration) return; bulkResults.value = [...orders.map(order => { const matches=acknowledged.filter(result=>result.orderId===order.orderId); const result=matches.length===1?matches[0]:undefined; return {orderId: order.orderId, orderNo: order.orderNo, success:Boolean(result?.success), message:result ? result.message || result.error?.message || (result.success?'发货成功':'处理失败') : '发货结果未知，请查看详情核对'} }), ...excluded]; bulkError.value = `${failure instanceof Error ? failure.message : '批量发货结果未知。'} 请刷新列表或逐单查看详情，避免重复发货。` }
  finally { if (batchRequest === bulkGeneration) bulkBusy.value = false }
}
onUnmounted(() => { generation++; bulkGeneration++ })
onMounted(() => { void load(); void loadCarriers(); void loadStores(); void loadRestaurants() })
</script>
<template>
  <section class="page-content orders-page admin-list-page">
    <header class="page-heading"><div><h1>订单管理</h1><p>查看收款与履约进度；批量发货仅处理当前页已选订单，逐单显示结果。</p></div><div class="fulfillment-actions"><RouterLink v-if="account.permissionCodes.includes('aftersale.read')" to="/aftersales" class="secondary-button">售后管理</RouterLink><RouterLink v-if="account.permissionCodes.includes('fulfillment.redeem')" to="/redemptions" class="secondary-button">到店核销</RouterLink><RouterLink v-if="account.permissionCodes.includes('fulfillment.settings.manage')" to="/fulfillment/settings" class="secondary-button">履约设置</RouterLink><button class="secondary-button" :disabled="loading" @click="load()">刷新订单</button></div></header>
    <form class="order-filters" aria-label="筛选订单" @submit.prevent="load(true)">
      <label v-if="account.permissionCodes.includes('stores.read')">履约前置仓<select v-model="storeId"><option value="">全部前置仓</option><option v-for="store in stores" :key="store.id" :value="store.id">{{store.name}}</option></select></label>
      <label>订单来源<select v-model="source" aria-label="订单来源" @change="restaurantId='' "><option value="">全部来源</option><option value="REGULAR">普通商城</option><option value="DINING">餐饮联盟</option></select></label>
      <label v-if="account.permissionCodes.includes('dining.read') && source !== 'REGULAR'">归属餐饮店<select v-model="restaurantId" aria-label="归属餐饮店"><option value="">全部餐饮店</option><option v-for="restaurant in restaurants" :key="restaurant.id" :value="restaurant.id">{{restaurant.name}}</option></select></label>
      <label>订单号<input v-model="search" type="search" placeholder="搜索订单号" /></label>
      <label>履约待办<select v-model="fulfillment" aria-label="履约待办"><option value="">全部待办</option><option value="WAITING_PREPARATION">待备货</option><option value="WAITING_PICKUP">待自提</option><option value="WAITING_DELIVERY">待配送</option><option value="DELIVERING">配送中</option><option value="WAITING_SHIPMENT">待发货</option><option value="IN_TRANSIT">待收货</option><option value="WAITING_REDEMPTION">待核销</option><option value="AFTER_SALE">售后中</option></select></label>
      <label>订单状态<select v-model="status"><option value="">全部状态</option><option value="PENDING_PAYMENT">待付款</option><option value="PAID">已结算（付款 / 扣积分）</option><option value="CLOSED">已关闭</option></select></label>
      <label>结算方式<select v-model="method"><option value="">全部方式</option><option value="OFFLINE">线下支付</option><option value="WECHAT">微信支付</option><option value="POINTS">纯积分兑换</option></select></label>
      <label>核实状态<select v-model="review"><option value="">全部</option><option value="true">用户已报告付款</option><option value="false">未报告付款</option></select></label>
      <button class="primary-button" type="submit" :disabled="loading">查询订单</button>
      <button class="secondary-button" type="button" :disabled="loading" @click="resetFilters">重置</button>
    </form>
    <p v-if="restaurantError" role="alert">{{restaurantError}} <button class="text-button" @click="loadRestaurants">重新读取餐饮店</button></p>
    <p v-if="storeError" role="alert">{{storeError}} <button class="text-button" @click="loadStores">重新读取前置仓</button></p>
    <p v-if="actionNotice" class="order-success" role="status">{{actionNotice}}</p>
    <p v-if="loading" role="status" class="loading-inline">正在读取订单…</p>
    <div v-else-if="error" class="notice" role="alert">{{ error }} <button class="text-button" @click="load()">重新加载</button></div>
    <template v-else-if="data">
      <AdminListColumns v-model="hiddenColumns" :columns="listColumns" />
      <AdminSelectionBar :count="selected.length" unit="单" :loading="loading || bulkBusy" @clear="selected=[]">
        <button class="secondary-button" :disabled="!selected.length || loading || bulkBusy" @click="exportOpen=true">导出所选订单</button>
        <button v-if="canShip" class="secondary-button" :disabled="!selected.length || loading || bulkBusy" @click="openBatch">批量发货</button>
      </AdminSelectionBar>
      <p v-if="carriersError" role="alert">{{ carriersError }} <button class="text-button" @click="loadCarriers">重试</button></p>
      <p v-if="!data.items.length" class="empty-state">当前条件下暂无订单。可调整筛选条件后查询。</p>
      <div v-else class="panel table-wrap admin-list-table" tabindex="0" aria-label="订单列表，可横向滚动">
        <table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部订单" :checked="currentPageSelected" :indeterminate="selected.length>0 && !currentPageSelected" :disabled="loading" @change="selectCurrentPage(($event.target as HTMLInputElement).checked)"></th><th>订单</th><th v-if="visibleColumn('ownership')">订单归属</th><th v-if="visibleColumn('warehouse')">履约前置仓 / 仓库</th><th v-if="visibleColumn('expiry')">付款截止</th><th v-if="visibleColumn('payment')">支付方式</th><th v-if="visibleColumn('settlement')">结算状态</th><th v-if="visibleColumn('fulfillment')">履约状态</th><th v-if="visibleColumn('amount')">结算金额 / 积分</th><th>操作</th></tr></thead>
          <tbody><tr v-for="order in data.items" :key="order.orderId"><td><input type="checkbox" :checked="selected.includes(order.orderId)" :aria-label="`选择订单 ${order.orderNo}`" @change="selectOrder(order.orderId, ($event.target as HTMLInputElement).checked)"></td><td><strong class="order-number">{{ order.orderNo }}</strong><small class="order-muted">{{ time(order.createdAt) }}</small></td><td v-if="visibleColumn('ownership')">{{order.source==='DINING'||order.dining?'餐饮联盟':'普通商城'}}<small v-if="order.dining" class="order-muted">餐饮店：{{order.dining.name}} · 桌号 {{order.dining.tableNumber}}</small></td><td v-if="visibleColumn('warehouse')">{{order.storeName||'平台订单'}}<small v-if="order.fulfillmentWarehouse" class="order-muted">仓库：{{order.fulfillmentWarehouse.name}}</small><small class="order-muted">{{({PICKUP:'到店自提',DELIVERY:'配送到家',EXPRESS:'快递发货'} as Record<string,string>)[order.deliveryMode||'']||'按商品履约'}}</small></td><td v-if="visibleColumn('expiry')">{{ isPointsOrder(order) ? '无需付款' : time(order.expiresAt) }}</td><td v-if="visibleColumn('payment')">{{paymentMethodLabel(order)}}</td><td v-if="visibleColumn('settlement')"><AdminStatusBadge :status="order.status" :label="paymentStatus(order)" :tone="order.paymentReviewStatus==='ANOMALY' ? 'danger' : order.status==='PAID' ? 'success' : order.status==='CLOSED' ? 'neutral' : 'pending'" /></td><td v-if="visibleColumn('fulfillment')"><AdminStatusBadge v-if="order.status==='PAID'" :status="order.fulfillmentStatus || 'PENDING'" :label="fulfillmentLabel(order.fulfillmentStatus)" /><span v-else>—</span></td><td v-if="visibleColumn('amount')" class="order-money">{{orderSettlementValue(order)}}</td><td><div class="fulfillment-actions"><button v-for="a in recoveries(order)" :key="a" class="text-button" @click="openAction(order,a)">核查{{actionLabels[a]}}结果</button><button class="text-button" @click="detailOrderId=order.orderId">查看详情</button><button v-if="orderActions(order,account.permissionCodes)[0]" class="text-button" @click="openAction(order,orderActions(order,account.permissionCodes)[0]!)">{{actionLabels[orderActions(order,account.permissionCodes)[0]!]}}</button><select v-if="orderActions(order,account.permissionCodes).length>1" aria-label="更多订单操作" @change="openAction(order,($event.target as HTMLSelectElement).value as OrderAction);($event.target as HTMLSelectElement).value=''"><option value="">更多</option><option v-for="a in orderActions(order,account.permissionCodes).slice(1)" :key="a" :value="a">{{actionLabels[a]}}</option></select></div></td></tr></tbody>
        </table>
      </div>
      <AdminListPager :page="page" :page-size="pageSize" :total="data.total" :loading="loading" @change="changePage" />
    </template>
    <el-dialog v-if="exportOpen" v-model="exportOpen" title="导出所选订单" width="520px" :close-on-click-modal="false"><p>导出当前页已选 {{selectedOrders.length}} 单，仅包含当前列表中的订单、归属、结算与履约信息。</p><template #footer><button class="secondary-button" @click="exportOpen=false">取消</button><button class="primary-button" :disabled="!selectedOrders.length" @click="exportSelected">确认导出</button></template></el-dialog>
    <OrderDetailDialog :account="account" :order-id="detailOrderId" @close="detailOrderId=''" />
    <OrderActionDialog :account="account" :order-id="actionOrderId" :action="action" @close="action=null" @updated="updatedOrder" @notice="actionNotice=$event"/>
    <el-dialog v-model="bulkOpen" title="批量发货" class="fulfillment-batch-dialog" :before-close="closeBatchDialog" :close-on-click-modal="false" :close-on-press-escape="!bulkBusy" :show-close="!bulkBusy"><p>已选 {{bulkSnapshot.length}} 单，可发货 {{bulkEligible.length}} 单，排除 {{bulkExcluded.length}} 单。</p><ul v-if="bulkExcluded.length" class="order-bulk-excluded"><li v-for="order in bulkExcluded" :key="order.orderId">{{order.orderNo}}：{{exclusionReason(order)}}</li></ul><p>仅提交当前页所选的可发货订单。每单填写实际快递公司和运单号；服务端逐单重新检查权限和订单状态。</p><p v-if="carriersLoading" role="status">正在读取可用快递公司…</p><p v-if="!carriersLoading && !activeCarriers.length" class="order-warning">暂无可用快递公司，暂不能发货。</p><div v-for="order in bulkEligible" :key="order.orderId" class="order-bulk-row"><strong>{{ order.orderNo }}</strong><label>快递公司<select v-model="bulkRows[order.orderId].carrierCode" :disabled="bulkBusy || Boolean(bulkResults.length)"><option value="">请选择</option><option v-for="carrier in activeCarriers" :key="carrier.code" :value="carrier.code">{{ carrier.name }}</option></select></label><label>运单号<input v-model="bulkRows[order.orderId].trackingNo" maxlength="80" autocomplete="off" :disabled="bulkBusy || Boolean(bulkResults.length)" placeholder="此单运单号"></label></div><p v-if="bulkValidation && !bulkResults.length" class="order-note">{{ bulkValidation }}</p><div v-if="bulkError" class="order-warning" role="alert">{{ bulkError }}</div><div v-if="bulkResults.length" role="status"><h3>逐单处理结果</h3><p v-for="item in bulkResults" :key="item.orderId" :class="item.success ? 'order-success' : 'order-warning'"><AdminStatusBadge :status="item.success?'SUCCESS':'FAILED'" :label="item.success?'成功':item.message.includes('未知')?'待核查':item.message.startsWith('未执行')?'未执行':'失败'" :tone="item.message.includes('未知')?'pending':item.message.startsWith('未执行')?'neutral':undefined" /> {{ item.orderNo || item.orderId }}：{{ item.message }} <button class="text-button" @click="detailOrderId=item.orderId">核查详情</button></p><button class="secondary-button" @click="bulkOpen = false; load()">刷新订单列表</button></div><template #footer><div class="dialog-footer"><button class="secondary-button" :disabled="bulkBusy" @click="closeBatchDialog(() => {bulkOpen=false})">{{ bulkResults.length ? '关闭' : '返回' }}</button><button v-if="!bulkSubmitted" class="primary-button" :disabled="bulkBusy || !bulkEligible.length || Boolean(bulkValidation)" @click="submitBatch">{{ bulkBusy ? '逐单处理中…' : `确认发货 ${bulkEligible.length} 单` }}</button></div></template></el-dialog>
  </section>
</template>
