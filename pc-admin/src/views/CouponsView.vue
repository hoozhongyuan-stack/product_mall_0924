<script setup lang="ts">
import CouponDetailView from './CouponDetailView.vue'
import AdminListPager from '../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../shared/list/AdminSelectedRowsDialog.vue'

import {computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { api, type Account } from '../api'
import { type Campaign, type Page, money, date, mode } from './coupons/types'
import './coupons/coupons.css'
import { campaignLifecycle } from './coupons/lifecycle.mjs'
import { useCampaignClock } from './coupons/clock'
const now = useCampaignClock()
const props = defineProps<{ account: Account }>()
const data = ref<Page<Campaign> | null>(null), loading = ref(false), error = ref('')
const q = ref(''), filter = ref(''), page = ref(1), pageSize = ref(20)
let sequence = 0
async function load(reset = false) {
  selectedIds.value = []
  if (reset) page.value = 1
  const seq = ++sequence
  loading.value = true; error.value = ''; data.value = null
  try {
    const query = new URLSearchParams({ q: q.value.trim(), status: filter.value, page: String(page.value), pageSize: String(pageSize.value) })
    const result = await api<Page<Campaign>>(`/coupon-campaigns?${query}`)
    if (seq === sequence) data.value = result
  } catch (e) { if (seq === sequence) error.value = e instanceof Error ? e.message : '活动读取失败。' }
  finally { if (seq === sequence) loading.value = false }
}
function resetFilters() { q.value = ''; filter.value = ''; void load(true) }
function changePage(value: number) { page.value = value; void load() }
function changeSize(value: number) { pageSize.value = value; void load(true) }
onMounted(() => load())
onUnmounted(() => sequence++)
watch(() => `${props.account.accountId}:${props.account.permissionCodes.join(',')}`, resetFilters)


const detailTarget = ref('')
const detailView = ref<{mayClose?: () => Promise<boolean>} | null>(null)
function openDetail(id:string) { detailTarget.value=id }
async function closeDetail(done:() => void) { if (detailView.value?.mayClose && !await detailView.value.mayClose()) return; detailTarget.value=''; done(); void load() }
const compareRows = computed(() => currentRows.value.filter(row => selectedIds.value.includes(row.id)).map(row => ({...row, displayStatus:campaignLifecycle(row, now.value).label, rule:money(row.discountFen), issued:`${row.issuedQuantity} / ${row.totalQuantity}` })))

const selectedIds = ref<string[]>([])
const comparisonOpen = ref(false)
const currentRows = computed(() => data.value?.items || [])
const allSelected = computed(() => currentRows.value.length > 0 && currentRows.value.every(row => selectedIds.value.includes(row.id)))
function toggleRow(id: string) { selectedIds.value = selectedIds.value.includes(id) ? selectedIds.value.filter(value => value !== id) : [...selectedIds.value, id] }
function selectPage() { selectedIds.value = allSelected.value ? [] : currentRows.value.map(row => row.id) }
function pageChanged(value:number,size:number) { page.value=value; pageSize.value=size; void load() }
watch([q,filter],()=>{selectedIds.value = []})
</script>
<template>
  <section class="page-content admin-list-page coupon-page">
    <header class="page-heading">
      <div><h1>优惠券活动</h1><p>设置券规则、查看发放进度；发布后金额、期限和适用范围固定。</p></div>
      <a v-if="account.permissionCodes.includes('coupon.manage')" href="/coupons/new" @click.prevent="openDetail('new')" class="primary-button">创建活动</a>
    </header>
    <form class="coupon-filters" @submit.prevent="load(true)">
      <label>活动名称或编码<el-input v-model="q" maxlength="100" clearable placeholder="查询优惠券活动" /></label>
      <label>发布状态<el-select v-model="filter"><el-option label="全部状态" value="" /><el-option label="草稿" value="DRAFT" /><el-option label="已发布" value="PUBLISHED" /><el-option label="历史活动" value="LEGACY" /></el-select></label>
      <el-button native-type="submit" type="primary" :loading="loading">查询</el-button>
      <el-button :disabled="loading" @click="resetFilters">重置</el-button>
    </form>
    <p v-if="loading" role="status">正在读取活动…</p>
    <div v-else-if="error" class="notice error" role="alert">{{ error }} <el-button @click="load()">重新读取</el-button></div>
    <template v-else-if="data"><AdminSelectionBar :count="selectedIds.length" unit="条" :loading="loading" @clear="selectedIds=[]"><button class="secondary-button" :disabled="!selectedIds.length" @click="comparisonOpen=true">批量查看</button></AdminSelectionBar>
      <div class="panel table-wrap admin-list-table coupon-campaign-table">
        <table><colgroup><col style="width:44px" /><col class="coupon-col-name" /><col class="coupon-col-rule" /><col class="coupon-col-date" /><col class="coupon-col-progress" /><col class="coupon-col-mode" /><col class="coupon-col-status" /><col class="coupon-col-action" /></colgroup>
          <thead><tr><th><input type="checkbox" aria-label="选择当前页全部记录" :checked="allSelected" @change="selectPage" /></th><th>活动</th><th>优惠规则</th><th>有效期</th><th>发放进度</th><th>领取方式</th><th>状态</th><th class="coupon-operation">操作</th></tr></thead>
          <tbody>
            <tr v-for="c in data.items" :key="c.id"><td><input type="checkbox" :aria-label="`选择记录 ${c.id}`" :checked="selectedIds.includes(c.id)" @change="toggleRow(c.id)" /></td>
              <td><strong class="coupon-title">{{ c.title }}</strong><small>{{ c.code }}</small></td>
              <td><strong class="coupon-rule-value">{{ c.kind === 'CASH' ? `现金券 ${money(c.discountFen)}` : `满 ${money(c.minGoodsFen)} 减 ${money(c.discountFen)}` }}</strong><small>{{ c.productIds.length ? `指定 ${c.productIds.length} 个商品` : '全场商品' }}</small><small>{{ c.redeemEligible ? '可用于核销商品' : '不用于核销商品' }}</small></td>
              <td class="coupon-date-cell"><time :datetime="c.validFrom">{{ date(c.validFrom) }}</time><small>至 {{ date(c.validUntil) }}</small></td>
              <td><span v-if="c.status === 'LEGACY'" class="coupon-note">历史发放额度未登记</span><template v-else><span>已发 <strong>{{ c.issuedQuantity }}</strong> / {{ c.totalQuantity }}</span><progress v-if="c.totalQuantity > 0" :value="c.issuedQuantity" :max="c.totalQuantity" :aria-label="`${c.title}发放进度`" /><small>剩余 {{ c.remainingQuantity }} 张</small></template></td>
              <td>{{ mode(c.claimMode) }}</td>
              <td><AdminStatusBadge :status="c.status" :label="campaignLifecycle(c, now).label" :tone="campaignLifecycle(c,now).active?'processing':campaignLifecycle(c,now).label==='未开始'?'pending':['时间异常','有效期异常'].includes(campaignLifecycle(c,now).label)?'danger':'neutral'" /><small v-if="c.status === 'PUBLISHED'">{{ campaignLifecycle(c, now).reason || (c.claimMode === 'SELF' ? '仅支持用户领取' : '允许领取与发放') }}</small></td>
              <td class="coupon-operation"><a :href="`/coupons/${c.id}`" @click.prevent="openDetail(c.id)" class="text-link">查看活动</a></td>
            </tr>
            <tr v-if="!data.items.length"><td colspan="8" class="empty-state">暂无匹配活动。可调整筛选条件。</td></tr>
          </tbody>
        </table>
      </div>
<AdminListPager :page="page" :page-size="pageSize" :total="data.pagination.total" :loading="loading" @change="pageChanged" />    </template>
  <AdminSelectedRowsDialog v-model="comparisonOpen" title="所选记录对比" :rows="compareRows" :columns="[{key:'title',label:'活动'},{key:'code',label:'编码'},{key:'displayStatus',label:'状态'},{key:'rule',label:'优惠金额'},{key:'issued',label:'发放进度'}]" />
<el-dialog :model-value="Boolean(detailTarget)" title="优惠券活动" width="min(1120px, calc(100vw - 32px))" :close-on-click-modal="false" :before-close="closeDetail" destroy-on-close><CouponDetailView v-if="detailTarget" ref="detailView" :account="account" :campaign-id="detailTarget" embedded /></el-dialog>
</section>
</template>
