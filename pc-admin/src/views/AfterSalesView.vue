<script setup lang="ts">
import AfterSaleDetailView from './AfterSaleDetailView.vue'
import AdminListPager from '../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../shared/list/AdminSelectedRowsDialog.vue'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Account } from '../api'
import { caseStatus } from './aftersales/form.mjs'
import { money, time, type AfterSaleList } from './aftersales/types'
import {isPointsOrder} from '../shared/order-settlement.mjs'
import {pointRefundState} from './aftersales/point-refund-state.mjs'
import './orders/orders.css'
defineProps<{account:Account}>()
const route = useRoute()
const data = ref<AfterSaleList | null>(null)
const status = ref('')
const orderNo = ref(String(route.query.orderNo || ''))
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref('')
let generation = 0
async function load(reset = false) {
  selectedIds.value = []
  if (reset) page.value = 1
  const ticket = ++generation
  loading.value = true; error.value = ''; data.value = null
  try {
    const query = new URLSearchParams({ page: String(page.value), pageSize: String(pageSize.value) })
    if (status.value) query.set('status', status.value)
    if (orderNo.value.trim()) query.set('orderNo', orderNo.value.trim())
    const result = await api<AfterSaleList>(`/aftersales?${query}`)
    if (ticket === generation) data.value = result
  } catch (failure) { if (ticket === generation) error.value = failure instanceof Error ? failure.message : '售后读取失败，请重试。' }
  finally { if (ticket === generation) loading.value = false }
}
function changePage(delta: number) { page.value += delta; void load() }
onMounted(() => { void load() })
onUnmounted(() => { generation += 1 })


const detailTarget = ref('')
const detailView = ref<{mayClose?: () => Promise<boolean>} | null>(null)
function openDetail(id:string) { detailTarget.value=id }
async function closeDetail(done:() => void) { if (detailView.value?.mayClose && !await detailView.value.mayClose()) return; detailTarget.value=''; done(); void load() }
const compareRows = computed(() => currentRows.value.filter(row => selectedIds.value.includes(row.caseId)).map(row => ({...row, displayStatus:caseStatus(row.status), amount:isPointsOrder(row)?`${row.requestedRefundPoints??'待核查'} 积分`:money(row.amountFen) })))

const selectedIds = ref<string[]>([])
const comparisonOpen = ref(false)
const currentRows = computed(() => data.value?.items || [])
const allSelected = computed(() => currentRows.value.length > 0 && currentRows.value.every(row => selectedIds.value.includes(row.caseId)))
function toggleRow(id: string) { selectedIds.value = selectedIds.value.includes(id) ? selectedIds.value.filter(value => value !== id) : [...selectedIds.value, id] }
function selectPage() { selectedIds.value = allSelected.value ? [] : currentRows.value.map(row => row.caseId) }
function pageChanged(value:number,size:number) { page.value=value; pageSize.value=size; void load() }
function resetFilters(){status.value='';orderNo.value='';void load(true)}
watch([status,orderNo],()=>{selectedIds.value = []})
</script>
<template>
  <section class="page-content admin-list-page orders-page">
    <header class="page-heading"><div><h1>售后管理</h1><p>按订单项审核售后；退款凭证登记与授权复核分别处理。</p></div><RouterLink to="/orders" class="secondary-button">返回订单管理</RouterLink></header>
    <form class="order-filters" @submit.prevent="load(true)">
      <label>售后状态<select v-model="status"><option value="">全部状态</option><option v-for="value in ['PENDING_REVIEW','WAITING_REFUND','WAITING_RETURN','COMPLETED','REJECTED','WITHDRAWN']" :key="value" :value="value">{{ caseStatus(value) }}</option></select></label>
      <label>订单号<input v-model="orderNo" maxlength="80" placeholder="输入订单号查询"></label>
      <button class="primary-button" :disabled="loading">查询售后</button><button type="button" class="secondary-button" :disabled="loading" @click="resetFilters">重置</button>
    </form>
    <p v-if="loading" role="status">正在读取售后申请…</p>
    <div v-else-if="error" class="notice" role="alert">{{ error }} <button class="text-button" @click="load()">重新加载</button></div>
    <template v-else-if="data"><AdminSelectionBar :count="selectedIds.length" unit="条" :loading="loading" @clear="selectedIds=[]"><button class="secondary-button" :disabled="!selectedIds.length" @click="comparisonOpen=true">批量查看</button></AdminSelectionBar>
      <p v-if="!data.items.length" class="empty-state">当前条件下暂无售后申请，可调整筛选条件后查询。</p>
      <div v-else class="panel table-wrap admin-list-table" tabindex="0" aria-label="售后列表，可横向滚动"><table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部记录" :checked="allSelected" @change="selectPage" /></th><th>订单 / 商品</th><th>申请类型</th><th>数量</th><th>申请退回金额 / 积分</th><th>状态</th><th>申请时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in data.items" :key="row.caseId"><td><input type="checkbox" :aria-label="`选择记录 ${row.caseId}`" :checked="selectedIds.includes(row.caseId)" @change="toggleRow(row.caseId)" /></td><td><strong class="order-number">{{ row.orderNo }}</strong><small v-if="row.caseNo" class="order-muted">售后 {{ row.caseNo }}</small><small class="order-muted">{{ row.title }}</small></td><td>{{ isPointsOrder(row) ? (row.kind === 'RETURN_REFUND' ? '退货退积分' : '仅退积分') : (row.kind === 'RETURN_REFUND' ? '退货退款' : '仅退款') }}<small v-if="row.redemptionScope === 'USED'" class="order-muted">已核销部分</small></td><td>{{ row.quantity }}</td><td class="order-money">{{isPointsOrder(row)?`${row.requestedRefundPoints??'待核查'} 积分`:money(row.amountFen)}}<template v-if="isPointsOrder(row)"><small class="order-muted">{{pointRefundState(row).label}} · {{pointRefundState(row).value}}</small><small class="order-muted">该项累计已退 {{row.returnedPoints??'待核查'}} 积分</small></template><template v-else><small class="order-muted">批准商品 {{ money(row.goodsRefundAmountFen ?? row.effectiveRefundAmountFen ?? row.amountFen) }}</small><small class="order-muted">运费 {{ money(row.shippingRefundAmountFen ?? 0) }} · 合计 {{ money(row.totalRefundAmountFen ?? row.effectiveRefundAmountFen ?? row.amountFen) }}</small></template></td><td><AdminStatusBadge :status="row.status" :label="caseStatus(row.status)" :tone="row.status==='COMPLETED'?'success':row.status==='REJECTED'?'danger':row.status==='PENDING_REVIEW'?'pending':row.status==='WITHDRAWN'?'neutral':'processing'" /></td><td>{{ time(row.createdAt) }}</td><td><a :href="`/aftersales/${row.caseId}`" @click.prevent="openDetail(row.caseId)" class="text-link">查看处理</a></td></tr></tbody></table></div>
<AdminListPager :page="page" :page-size="pageSize" :total="data.total" :loading="loading" @change="pageChanged" />    </template>
  <AdminSelectedRowsDialog v-model="comparisonOpen" title="所选记录对比" :rows="compareRows" :columns="[{key:'orderNo',label:'订单号'},{key:'title',label:'商品'},{key:'quantity',label:'数量'},{key:'amount',label:'申请金额 / 积分'},{key:'displayStatus',label:'状态'}]" />
<el-dialog :model-value="Boolean(detailTarget)" title="售后处理" width="min(1120px, calc(100vw - 32px))" :close-on-click-modal="false" :before-close="closeDetail" destroy-on-close><AfterSaleDetailView v-if="detailTarget" ref="detailView" :account="account" :case-id="detailTarget" embedded /></el-dialog>
</section>
</template>
