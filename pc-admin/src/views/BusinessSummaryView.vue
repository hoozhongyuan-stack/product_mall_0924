<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

interface Day { date: string; paidOrderCount: number; paidAmountFen: number; pointsExchangeCount: number; refundCount: number; refundAmountFen: number; netAmountFen: number }
interface Summary { from: string; to: string; timeZone: string; totals: Omit<Day, 'date'>; days: Day[] }
const from = ref('')
const to = ref('')
const data = ref<Summary | null>(null)
const loading = ref(true)
const error = ref('')
let generation = 0

async function load() {
  const current = ++generation
  const query = new URLSearchParams()
  if (from.value) query.set('from', from.value)
  if (to.value) query.set('to', to.value)
  loading.value = true
  error.value = ''
  data.value = null
  try {
    const result = await api<Summary>(`/business-summary?${query}`)
    if (current === generation) data.value = result
  } catch (reason) {
    if (current === generation) error.value = reason instanceof Error ? reason.message : '经营统计读取失败。'
  } finally {
    if (current === generation) loading.value = false
  }
}
function reset() { from.value = ''; to.value = ''; void load() }
function yuan(fen: number) { return (fen / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }
onMounted(() => { void load() })
</script>

<template>
  <section class="page-content business-page">
    <header class="page-heading"><div><h1>经营统计</h1><p>按支付确认与退款成功时间归属。默认近 90 个自然日，金额单位为元。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">重新读取</button></header>
    <form class="panel business-filters" aria-label="筛选经营统计" @submit.prevent="load">
      <label>开始日期<input v-model="from" type="date" /></label><label>结束日期<input v-model="to" type="date" /></label>
      <button class="primary-button" type="submit" :disabled="loading">查询</button><button class="secondary-button" type="button" :disabled="loading" @click="reset">近 90 天</button>
    </form>
    <p class="business-hint">现金订单确认支付计笔数，零元现金订单计笔数但金额为 0；积分兑换单独计笔数。退款按成功日计入，跨期退款可使当日净额为负。</p>
    <p v-if="error" class="error notice" role="alert">{{ error }} <button class="text-button" type="button" @click="load">重试</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在汇总经营数据…</p>
    <template v-else-if="data">
      <p class="business-range">{{ data.from }} 至 {{ data.to }} · {{ data.timeZone }}</p>
      <div class="panel table-wrap" tabindex="0" aria-label="经营汇总，可横向滚动">
        <table><thead><tr><th scope="col">口径</th><th scope="col">支付笔数</th><th scope="col">支付金额</th><th scope="col">退款笔数</th><th scope="col">退款金额</th><th scope="col">净成交</th><th scope="col">积分兑换</th></tr></thead>
          <tbody><tr><th scope="row">区间合计</th><td>{{ data.totals.paidOrderCount }}</td><td>{{ yuan(data.totals.paidAmountFen) }}</td><td>{{ data.totals.refundCount }}</td><td>{{ yuan(data.totals.refundAmountFen) }}</td><td class="strong-cell">{{ yuan(data.totals.netAmountFen) }}</td><td>{{ data.totals.pointsExchangeCount }}</td></tr></tbody></table>
      </div>
      <p v-if="!data.totals.paidOrderCount && !data.totals.refundCount && !data.totals.pointsExchangeCount" class="empty-state">当前区间暂无支付、退款或积分兑换记录。可调整日期后再查询。</p>
      <h2 v-else class="business-subhead">每日明细</h2>
      <div v-if="data.totals.paidOrderCount || data.totals.refundCount || data.totals.pointsExchangeCount" class="panel table-wrap" tabindex="0" aria-label="每日经营明细，可横向滚动">
        <table><thead><tr><th scope="col">日期</th><th scope="col">支付笔数</th><th scope="col">支付金额</th><th scope="col">退款笔数</th><th scope="col">退款金额</th><th scope="col">净成交</th><th scope="col">积分兑换</th></tr></thead>
          <tbody><tr v-for="day in data.days" :key="day.date"><th scope="row">{{ day.date }}</th><td>{{ day.paidOrderCount }}</td><td>{{ yuan(day.paidAmountFen) }}</td><td>{{ day.refundCount }}</td><td>{{ yuan(day.refundAmountFen) }}</td><td>{{ yuan(day.netAmountFen) }}</td><td>{{ day.pointsExchangeCount }}</td></tr></tbody></table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.business-filters{display:flex;align-items:end;flex-wrap:wrap;gap:12px;padding:20px}.business-filters label{display:grid;gap:6px;min-width:170px;flex:1;font-size:13px;font-weight:650;color:#51695a}
.business-filters input{width:100%;min-height:42px;border:1px solid #cbd8ce;border-radius:9px;padding:8px 10px;background:#fff;color:#243329}
.business-filters input:focus-visible{outline:2px solid #328055;outline-offset:2px}.business-hint{max-width:78ch;font-size:13px}.business-range{font-size:13px;margin:18px 0}.business-subhead{font-size:20px;margin:28px 0 14px}.business-page td{font-variant-numeric:tabular-nums}
@media(max-width:600px){.business-filters{display:grid;grid-template-columns:1fr 1fr}.business-filters label{grid-column:1/-1;min-width:0}}
</style>
