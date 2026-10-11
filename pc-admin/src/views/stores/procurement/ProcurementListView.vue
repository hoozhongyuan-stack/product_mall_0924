<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import {ElDialog} from 'element-plus'
import ProcurementDetailView from './ProcurementDetailView.vue'
import AdminListPager from '../../../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../../../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../../../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../../../shared/list/AdminSelectedRowsDialog.vue'
import {useCurrentPageSelection} from '../../../shared/list/useCurrentPageSelection'
import { RouterLink } from 'vue-router'
import { api, type Account } from '../../../api'
import { amount } from '../types'
import { statuses } from './domain.mjs'
import type { ProcurementOrder } from './types'
import '../stores.css'
import './procurement.css'
const props = defineProps<{account:Account}>()
const q=ref(''), status=ref(''), storeId=ref(''), page=ref(1), pageSize=ref(20)
const rows=ref<ProcurementOrder[]>([]), total=ref(0), loading=ref(false), error=ref('')
const canRead=computed(()=>props.account.permissionCodes.includes('stores.procurement.read'))
const selection=useCurrentPageSelection(()=>rows.value,r=>r.id),comparison=ref(false),detailId=ref(''),detail=ref<{canClose:()=>Promise<boolean>}|null>(null)
const comparisonRows=computed(()=>selection.selectedRows.value.map(r=>({orderNo:r.orderNo,store:r.storeName,status:statuses[r.status]||r.status,payable:amount(r.payableFen)})))
async function closeDetail(done?:()=>void){if(detail.value&&!await detail.value.canClose())return;detailId.value='';done?.();load()}
let epoch=0
async function load(reset=false) {
 if(reset) page.value=1
 const seq=++epoch; rows.value=[]; comparison.value=false; error.value=''
 if(!canRead.value) return
 loading.value=true
 try {
  const params=new URLSearchParams({q:q.value.trim(),status:status.value,storeId:storeId.value.trim(),page:String(page.value),pageSize:String(pageSize.value)})
  const data=await api<{items:ProcurementOrder[];total:number}>(`/stores/procurement/orders?${params}`)
  if(seq===epoch) {rows.value=data.items;total.value=data.total}
 } catch(reason) {if(seq===epoch) error.value=reason instanceof Error?reason.message:'进货订单读取失败，请重试。'}
 finally {if(seq===epoch) loading.value=false}
}
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes]),()=>load(true),{immediate:true})
onUnmounted(()=>epoch++)
</script>
<template>
 <section class="stores-page procurement-page admin-list-page panel store-section">
  <div class="store-toolbar"><h2>前置仓进货订单</h2><RouterLink to="/stores/procurement/settings">采购收款说明</RouterLink></div>
  <form class="store-toolbar" @submit.prevent="load(true)">
   <label>订单／前置仓<input v-model="q" maxlength="100" placeholder="输入订单号或前置仓名称"></label>
   <label>状态<select v-model="status"><option value="">全部状态</option><option v-for="(label,key) in statuses" :key="key" :value="key">{{label}}</option></select></label>
   <button :disabled="loading || !canRead">查询</button><button type="button" :disabled="loading" @click="q='';status='';storeId='';load(true)">重置</button>
  </form>
  <p v-if="error" class="error" role="alert">{{error}} <button @click="load()">重试</button></p>
  <p v-if="loading" role="status">正在读取进货订单…</p>
  <AdminSelectionBar :count="selection.selectedRows.value.length" :loading="loading" @clear="selection.clear"><button :disabled="!selection.selectedRows.value.length" @click="comparison=true">对比所选进货订单</button></AdminSelectionBar>
  <div class="table-wrap admin-list-table" tabindex="0" aria-label="进货商品列表，可横向滚动"><table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部采购单" :checked="selection.allSelected.value" :indeterminate="selection.indeterminate.value" @change="selection.togglePage(($event.target as HTMLInputElement).checked)"></th><th>进货订单</th><th>前置仓</th><th>状态</th><th>商品金额</th><th>运费</th><th>应付金额</th><th>操作</th></tr></thead>
   <tbody><tr v-for="row in rows" :key="row.id"><td><input type="checkbox" :aria-label="`选择 ${row.orderNo}`" :checked="selection.selectedIds.value.includes(row.id)" @change="selection.toggle(row.id,($event.target as HTMLInputElement).checked)"></td><td>{{row.orderNo}}<small>{{new Date(row.createdAt).toLocaleString('zh-CN')}}</small></td><td>{{row.storeName}}</td><td><AdminStatusBadge :status="row.status" :label="statuses[row.status]||row.status"/></td><td>{{amount(row.goodsAmountFen)}}</td><td>{{amount(row.freightFen)}}</td><td>{{amount(row.payableFen)}}</td><td><button class="text-button" @click="detailId=row.id">查看详情</button></td></tr><tr v-if="!loading && !rows.length"><td colspan="8">暂无符合条件的进货订单。</td></tr></tbody>
  </table></div>
  <AdminListPager :page="page" :page-size="pageSize" :total="total" :loading="loading" @change="(p,s)=>{page=p;pageSize=s;load()}"/>
  <ElDialog :model-value="!!detailId" title="进货订单详情" width="min(1100px,96vw)" :before-close="closeDetail" :close-on-click-modal="false"><ProcurementDetailView v-if="detailId" ref="detail" :key="detailId" :account="account" :record-id="detailId" embedded/></ElDialog>
  <AdminSelectedRowsDialog v-model="comparison" title="采购单对比" :rows="comparisonRows" :columns="[{key:'orderNo',label:'采购单'},{key:'store',label:'前置仓'},{key:'status',label:'状态'},{key:'payable',label:'应付'}]"/>
 </section>
</template>
<style scoped>small{display:block;color:#737f89;margin-top:5px}select,input{min-height:38px;border:1px solid #d7dce0;border-radius:6px;padding:8px}label{display:grid;gap:6px}</style>
