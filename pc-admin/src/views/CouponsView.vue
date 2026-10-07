<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { api, type Account } from '../api'
import { type Campaign, type Page, money, date, status, mode } from './coupons/types'
import './coupons/coupons.css'
const props = defineProps<{ account: Account }>()
const data = ref<Page<Campaign> | null>(null), loading = ref(false), error = ref('')
const q = ref(''), filter = ref(''), page = ref(1), pageSize = ref(20)
let sequence = 0
async function load(reset = false) {
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
</script>
<template>
  <section class="page-content coupon-page">
    <header class="page-heading">
      <div><h1>优惠券活动</h1><p>设置券规则、查看发放进度；发布后金额、期限和适用范围固定。</p></div>
      <RouterLink v-if="account.permissionCodes.includes('coupon.manage')" to="/coupons/new" class="primary-button">创建活动</RouterLink>
    </header>
    <form class="coupon-filters" @submit.prevent="load(true)">
      <label>活动名称或编码<el-input v-model="q" maxlength="100" clearable placeholder="查询优惠券活动" /></label>
      <label>活动状态<el-select v-model="filter"><el-option label="全部状态" value="" /><el-option label="草稿" value="DRAFT" /><el-option label="已发布" value="PUBLISHED" /><el-option label="历史活动" value="LEGACY" /></el-select></label>
      <el-button native-type="submit" type="primary" :loading="loading">查询</el-button>
      <el-button :disabled="loading" @click="resetFilters">重置</el-button>
    </form>
    <p v-if="loading" role="status">正在读取活动…</p>
    <div v-else-if="error" class="notice error" role="alert">{{ error }} <el-button @click="load()">重新读取</el-button></div>
    <template v-else-if="data">
      <div class="panel table-wrap coupon-campaign-table">
        <table><colgroup><col class="coupon-col-name" /><col class="coupon-col-rule" /><col class="coupon-col-date" /><col class="coupon-col-progress" /><col class="coupon-col-mode" /><col class="coupon-col-status" /><col class="coupon-col-action" /></colgroup>
          <thead><tr><th>活动</th><th>优惠规则</th><th>有效期</th><th>发放进度</th><th>领取方式</th><th>状态</th><th class="coupon-operation">操作</th></tr></thead>
          <tbody>
            <tr v-for="c in data.items" :key="c.id">
              <td><strong class="coupon-title">{{ c.title }}</strong><small>{{ c.code }}</small></td>
              <td><strong class="coupon-rule-value">{{ c.kind === 'CASH' ? `现金券 ${money(c.discountFen)}` : `满 ${money(c.minGoodsFen)} 减 ${money(c.discountFen)}` }}</strong><small>{{ c.productIds.length ? `指定 ${c.productIds.length} 个商品` : '全场商品' }}</small><small>{{ c.redeemEligible ? '可用于核销商品' : '不用于核销商品' }}</small></td>
              <td class="coupon-date-cell"><time :datetime="c.validFrom">{{ date(c.validFrom) }}</time><small>至 {{ date(c.validUntil) }}</small></td>
              <td><span v-if="c.status === 'LEGACY'" class="coupon-note">历史发放额度未登记</span><template v-else><span>已发 <strong>{{ c.issuedQuantity }}</strong> / {{ c.totalQuantity }}</span><progress v-if="c.totalQuantity > 0" :value="c.issuedQuantity" :max="c.totalQuantity" :aria-label="`${c.title}发放进度`" /><small>剩余 {{ c.remainingQuantity }} 张</small></template></td>
              <td>{{ mode(c.claimMode) }}</td>
              <td><span class="badge" :class="c.status === 'PUBLISHED' ? 'badge-good' : 'badge-muted'">{{ status(c.status) }}</span><small v-if="c.status === 'PUBLISHED'">{{ c.issuanceEnabled ? '发放开启' : '发放暂停' }}</small></td>
              <td class="coupon-operation"><RouterLink :to="`/coupons/${c.id}`" class="text-link">查看活动</RouterLink></td>
            </tr>
            <tr v-if="!data.items.length"><td colspan="7" class="empty-state">暂无匹配活动。可调整筛选条件。</td></tr>
          </tbody>
        </table>
      </div>
      <div class="coupon-pagination"><el-pagination :current-page="page" :page-size="pageSize" :total="data.pagination.total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next, jumper" :disabled="loading" @current-change="changePage" @size-change="changeSize" /></div>
    </template>
  </section>
</template>
