<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Account } from '../api'
import ExportPanel from './exports/ExportPanel.vue'
import type { ExportFilters } from './exports/client'

interface Day { date: string; paidOrderCount: number; paidAmountFen: number; pointsExchangeCount: number; refundCount: number; refundAmountFen: number; netAmountFen: number }
interface Summary { from: string; to: string; timeZone: string; totals: Omit<Day, 'date'>; days: Day[] }
const from = ref('')
const to = ref('')
const data = ref<Summary | null>(null)
const loading = ref(true)
const error = ref('')
const props = defineProps<{ account: Account; embedded?: boolean }>()
const canExport = computed(() => props.account.permissionCodes.includes('business.report.export'))
const exportFilters = computed<ExportFilters | null>(() => data.value && !loading.value && !error.value ? { from: data.value.from, to: data.value.to } : null)
const detailPage = ref(1)
const detailPageSize = 10
const sortedDays = computed(() => [...(data.value?.days || [])].sort((a, b) => b.date.localeCompare(a.date)))
const pageCount = computed(() => Math.max(1, Math.ceil(sortedDays.value.length / detailPageSize)))
const visibleDays = computed(() => sortedDays.value.slice((detailPage.value - 1) * detailPageSize, detailPage.value * detailPageSize))
const today = computed(() => {
  if (!data.value) return null
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: data.value.timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const part = (type: string) => parts.find(value => value.type === type)?.value
  const date = `${part('year')}-${part('month')}-${part('day')}`
  return data.value.days.find(day => day.date === date) || null
})
let generation = 0

async function load() {
  const current = ++generation
  const query = new URLSearchParams()
  if (from.value) query.set('from', from.value)
  if (to.value) query.set('to', to.value)
  detailPage.value = 1
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
  <section class="business-page" :class="embedded ? 'business-embedded' : 'page-content'">
    <header class="page-heading"><div><component :is="embedded ? 'h2' : 'h1'">{{ embedded ? '今日经营' : '经营统计' }}</component><p>按支付确认与退款成功时间归属，金额单位为元。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">重新读取</button></header>
    <p v-if="error" class="error notice" role="alert">{{ error }} <button class="text-button" type="button" @click="load">重试</button></p>
    <p v-if="loading" class="loading-inline" role="status">正在汇总经营数据…</p>
    <template v-if="embedded && !loading && data">
      <p v-if="today" class="business-range">{{ today.date }} · {{ data.timeZone }}</p>
      <dl v-if="today" class="panel business-totals" aria-label="今日经营">
        <div><dt>支付金额</dt><dd>{{ yuan(today.paidAmountFen) }}<small>元</small></dd></div>
        <div><dt>净成交金额</dt><dd>{{ yuan(today.netAmountFen) }}<small>元</small></dd></div>
        <div><dt>支付笔数</dt><dd>{{ today.paidOrderCount }}<small>笔</small></dd></div>
        <div><dt>退款金额</dt><dd>{{ yuan(today.refundAmountFen) }}<small>元</small></dd></div>
        <div><dt>退款笔数</dt><dd>{{ today.refundCount }}<small>笔</small></dd></div>
        <div><dt>积分兑换</dt><dd>{{ today.pointsExchangeCount }}<small>笔</small></dd></div>
      </dl>
      <p v-else class="empty-state">当前数据未包含今日明细，请点击“近 90 天”恢复默认区间。</p>
    </template>
    <component :is="embedded ? 'details' : 'div'" :class="{ 'business-history': embedded }">
      <summary v-if="embedded">区间经营统计与每日明细</summary>
    <form class="panel business-filters" aria-label="筛选经营统计" @submit.prevent="load">
      <label>开始日期<input v-model="from" type="date" /></label><label>结束日期<input v-model="to" type="date" /></label>
      <button class="primary-button" type="submit" :disabled="loading">查询</button><button class="secondary-button" type="button" :disabled="loading" @click="reset">近 90 天</button>
    </form>
    <p class="business-hint">现金订单确认支付计笔数，零元现金订单计笔数但金额为 0；积分兑换单独计笔数。退款按成功日计入，跨期退款可使当日净额为负。</p>
    <template v-if="!loading && data">
      <p class="business-range">{{ data.from }} 至 {{ data.to }} · {{ data.timeZone }}</p>
      <dl class="panel business-totals" aria-label="区间经营合计">
        <div><dt>支付金额</dt><dd>{{ yuan(data.totals.paidAmountFen) }}<small>元</small></dd></div>
        <div><dt>退款金额</dt><dd>{{ yuan(data.totals.refundAmountFen) }}<small>元</small></dd></div>
        <div><dt>净成交金额</dt><dd class="business-net">{{ yuan(data.totals.netAmountFen) }}<small>元</small></dd></div>
        <div><dt>支付笔数</dt><dd>{{ data.totals.paidOrderCount }}<small>笔</small></dd></div>
        <div><dt>退款笔数</dt><dd>{{ data.totals.refundCount }}<small>笔</small></dd></div>
        <div><dt>积分兑换</dt><dd>{{ data.totals.pointsExchangeCount }}<small>笔</small></dd></div>
      </dl>
      <p v-if="!data.totals.paidOrderCount && !data.totals.refundCount && !data.totals.pointsExchangeCount" class="empty-state">当前区间暂无支付、退款或积分兑换记录。可调整日期后再查询。</p>
      <h2 v-else class="business-subhead">每日明细</h2>
      <div v-if="data.totals.paidOrderCount || data.totals.refundCount || data.totals.pointsExchangeCount" class="panel table-wrap" tabindex="0" aria-label="每日经营明细，可横向滚动">
        <table><thead><tr><th scope="col">日期</th><th scope="col">支付笔数</th><th scope="col">支付金额</th><th scope="col">退款笔数</th><th scope="col">退款金额</th><th scope="col">净成交</th><th scope="col">积分兑换</th></tr></thead>
          <tbody><tr v-for="day in visibleDays" :key="day.date"><th scope="row">{{ day.date }}</th><td>{{ day.paidOrderCount }}</td><td>{{ yuan(day.paidAmountFen) }}</td><td>{{ day.refundCount }}</td><td>{{ yuan(day.refundAmountFen) }}</td><td>{{ yuan(day.netAmountFen) }}</td><td>{{ day.pointsExchangeCount }}</td></tr></tbody></table>
      </div>
      <nav v-if="data.totals.paidOrderCount || data.totals.refundCount || data.totals.pointsExchangeCount" class="business-pagination" aria-label="每日明细分页">
        <span>最新日期在前 · 共 {{ sortedDays.length }} 天 · 第 {{ detailPage }} / {{ pageCount }} 页</span>
        <button class="secondary-button" type="button" aria-label="上一页每日明细" :disabled="detailPage === 1" @click="detailPage--">上一页</button>
        <button class="secondary-button" type="button" aria-label="下一页每日明细" :disabled="detailPage === pageCount" @click="detailPage++">下一页</button>
      </nav>
    </template>
    <ExportPanel v-if="canExport" kind="BUSINESS" :filters="exportFilters" :actor-id="account.accountId" />
    </component>
  </section>
</template>

<style scoped>
.business-history{margin-top:20px}.business-history summary{cursor:pointer;font-weight:600;padding:12px 0}.business-history summary:focus-visible{outline:2px solid var(--mall-color-focus);outline-offset:3px}.business-pagination{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin:16px 0}.business-pagination span{margin-right:auto;font-size:13px;color:var(--mall-color-muted)}
.business-filters{display:flex;align-items:end;flex-wrap:wrap;gap:12px;padding:20px}.business-filters label{display:grid;gap:6px;min-width:170px;flex:1;font-size:13px;font-weight:650;color:var(--mall-color-muted)}
.business-filters input{width:100%;min-height:42px;border:1px solid var(--mall-color-border);border-radius:9px;padding:8px 10px;background:#fff;color:var(--mall-color-text)}
.business-filters input:focus-visible{outline:2px solid var(--mall-color-brand);outline-offset:2px}.business-hint{max-width:78ch;font-size:13px}.business-range{font-size:13px;margin:18px 0}.business-subhead{font-size:20px;margin:28px 0 14px}.business-page td{font-variant-numeric:tabular-nums}
@media(max-width:600px){.business-filters{display:grid;grid-template-columns:1fr 1fr}.business-filters label{grid-column:1/-1;min-width:0}}
</style>
