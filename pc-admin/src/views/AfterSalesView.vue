<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { caseStatus } from './aftersales/form.mjs'
import { money, time, type AfterSaleList } from './aftersales/types'
import {isPointsOrder} from '../shared/order-settlement.mjs'
import {pointRefundState} from './aftersales/point-refund-state.mjs'
import './orders/orders.css'
const route = useRoute()
const data = ref<AfterSaleList | null>(null)
const status = ref('')
const orderNo = ref(String(route.query.orderNo || ''))
const page = ref(1)
const loading = ref(false)
const error = ref('')
let generation = 0
async function load(reset = false) {
  if (reset) page.value = 1
  const ticket = ++generation
  loading.value = true; error.value = ''; data.value = null
  try {
    const query = new URLSearchParams({ page: String(page.value), pageSize: '20' })
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
</script>
<template>
  <section class="page-content orders-page">
    <header class="page-heading"><div><h1>售后管理</h1><p>按订单项审核售后；退款凭证登记与授权复核分别处理。</p></div><RouterLink to="/orders" class="secondary-button">返回订单管理</RouterLink></header>
    <form class="order-filters" @submit.prevent="load(true)">
      <label>售后状态<select v-model="status"><option value="">全部状态</option><option v-for="value in ['PENDING_REVIEW','WAITING_REFUND','WAITING_RETURN','COMPLETED','REJECTED','WITHDRAWN']" :key="value" :value="value">{{ caseStatus(value) }}</option></select></label>
      <label>订单号<input v-model="orderNo" maxlength="80" placeholder="输入订单号查询"></label>
      <button class="primary-button" :disabled="loading">查询售后</button>
    </form>
    <p v-if="loading" role="status">正在读取售后申请…</p>
    <div v-else-if="error" class="notice" role="alert">{{ error }} <button class="text-button" @click="load()">重新加载</button></div>
    <template v-else-if="data">
      <p v-if="!data.items.length" class="empty-state">当前条件下暂无售后申请，可调整筛选条件后查询。</p>
      <div v-else class="panel table-wrap" tabindex="0" aria-label="售后列表，可横向滚动"><table><thead><tr><th>订单 / 商品</th><th>申请类型</th><th>数量</th><th>申请退回金额 / 积分</th><th>状态</th><th>申请时间</th><th>操作</th></tr></thead><tbody><tr v-for="row in data.items" :key="row.caseId"><td><strong class="order-number">{{ row.orderNo }}</strong><small class="order-muted">{{ row.title }}</small></td><td>{{ isPointsOrder(row) ? (row.kind === 'RETURN_REFUND' ? '退货退积分' : '仅退积分') : (row.kind === 'RETURN_REFUND' ? '退货退款' : '仅退款') }}<small v-if="row.redemptionScope === 'USED'" class="order-muted">已核销部分</small></td><td>{{ row.quantity }}</td><td class="order-money">{{isPointsOrder(row)?`${row.requestedRefundPoints??'待核查'} 积分`:money(row.amountFen)}}<template v-if="isPointsOrder(row)"><small class="order-muted">{{pointRefundState(row).label}} · {{pointRefundState(row).value}}</small><small class="order-muted">该项累计已退 {{row.returnedPoints??'待核查'}} 积分</small></template><template v-else><small class="order-muted">批准商品 {{ money(row.goodsRefundAmountFen ?? row.effectiveRefundAmountFen ?? row.amountFen) }}</small><small class="order-muted">运费 {{ money(row.shippingRefundAmountFen ?? 0) }} · 合计 {{ money(row.totalRefundAmountFen ?? row.effectiveRefundAmountFen ?? row.amountFen) }}</small></template></td><td>{{ caseStatus(row.status) }}</td><td>{{ time(row.createdAt) }}</td><td><RouterLink :to="`/aftersales/${row.caseId}`" class="text-link">查看处理</RouterLink></td></tr></tbody></table></div>
      <div class="order-pagination"><span>共 {{ data.total }} 笔 · 第 {{ page }} 页</span><div><button class="secondary-button" :disabled="page <= 1" @click="changePage(-1)">上一页</button><button class="secondary-button" :disabled="page * data.pageSize >= data.total" @click="changePage(1)">下一页</button></div></div>
    </template>
  </section>
</template>
