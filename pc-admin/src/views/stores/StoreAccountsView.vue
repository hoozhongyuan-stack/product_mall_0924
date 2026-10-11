<script setup lang="ts">
import {ref,watch,computed} from 'vue'
import {ElDialog} from 'element-plus'
import {api,type Account} from '../../api'
import {amount,type StoreAccount} from './types'
import StoreAccountDetailView from './StoreAccountDetailView.vue'
import AdminListPager from '../../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../../shared/list/AdminSelectedRowsDialog.vue'
import {useCurrentPageSelection} from '../../shared/list/useCurrentPageSelection'
import './stores.css'
const props=defineProps<{account:Account}>(),page=ref(1),pageSize=ref(20),total=ref(0),search=ref('')
const items=ref<StoreAccount[]>([]),loading=ref(false),error=ref(''),detailId=ref(''),comparison=ref(false)
const selection=useCurrentPageSelection(()=>items.value,r=>r.storeId)
const comparisonRows=computed(()=>selection.selectedRows.value.map(r=>({name:r.storeName,pending:amount(r.balance.pendingFen),available:amount(r.balance.availableFen),frozen:amount(r.balance.frozenFen),paid:amount(r.balance.paidFen)})))
const detail=ref<{canClose:()=>Promise<boolean>}|null>(null)
async function closeDetail(done?:()=>void){if(detail.value&&!await detail.value.canClose())return;detailId.value='';done?.();load()}
let generation=0
async function load(reset=false){if(reset)page.value=1;const current=++generation;loading.value=true;items.value=[];comparison.value=false;error.value='';try{const result=await api<{items:StoreAccount[];total:number}>(`/stores/accounts?${new URLSearchParams({search:search.value.trim(),page:String(page.value),pageSize:String(pageSize.value)})}`);if(current===generation){items.value=result.items;total.value=result.total}}catch(e){if(current===generation)error.value=e instanceof Error?e.message:'读取前置仓账户失败。'}finally{if(current===generation)loading.value=false}}
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes]),()=>{detailId.value='';load(true)},{immediate:true})
</script>
<template><section class="page-content stores-page admin-list-page"><header class="page-heading"><div><h1>前置仓账户</h1><p>按前置仓查看结算余额、收入明细与提现申请，平台审核后登记线下发放。</p></div><button class="secondary-button" :disabled="loading" @click="load()">刷新账户</button></header><form class="store-search" @submit.prevent="load(true)"><label>前置仓名称<input v-model="search" type="search" maxlength="120"></label><button class="primary-button" :disabled="loading">查询</button><button class="secondary-button" type="button" @click="search='';load(true)">重置</button></form><p v-if="loading" role="status">正在读取账户…</p><p v-else-if="error" role="alert">{{error}} <button class="text-button" @click="load()">重试</button></p>
<AdminSelectionBar :count="selection.selectedRows.value.length" unit="家" :loading="loading" @clear="selection.clear"><button class="secondary-button" :disabled="!selection.selectedRows.value.length" @click="comparison=true">对比所选账户</button></AdminSelectionBar>
<div class="panel table-wrap admin-list-table" tabindex="0" aria-label="前置仓账户列表，可横向滚动"><table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部账户" :checked="selection.allSelected.value" :indeterminate="selection.indeterminate.value" :disabled="loading" @change="selection.togglePage(($event.target as HTMLInputElement).checked)"></th><th>前置仓</th><th>待结算</th><th>可提现</th><th>冻结中</th><th>已提现</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in items" :key="item.storeId"><td><input type="checkbox" :aria-label="`选择 ${item.storeName}`" :checked="selection.selectedIds.value.includes(item.storeId)" @change="selection.toggle(item.storeId,($event.target as HTMLInputElement).checked)"></td><td>{{item.storeName}}</td><td>{{amount(item.balance.pendingFen)}}</td><td>{{amount(item.balance.availableFen)}}</td><td>{{amount(item.balance.frozenFen)}}</td><td>{{amount(item.balance.paidFen)}}</td><td><AdminStatusBadge :status="item.settlementReady?'CONFIGURED':'UNCONFIGURED'" :label="item.settlementReady?'已配置':'待配置'"/></td><td><button class="text-button" @click="detailId=item.storeId">账户详情</button></td></tr><tr v-if="!loading&&!items.length"><td colspan="8">暂无符合条件的账户。</td></tr></tbody></table></div>
<AdminListPager :page="page" :page-size="pageSize" :total="total" :loading="loading" @change="(p,s)=>{page=p;pageSize=s;load()}"/>
<ElDialog :model-value="!!detailId" title="前置仓账户详情" width="min(1200px,96vw)" :close-on-click-modal="false" :before-close="closeDetail"><StoreAccountDetailView ref="detail" v-if="detailId" :key="detailId" :record-id="detailId" :account="account" embedded/></ElDialog>
<AdminSelectedRowsDialog v-model="comparison" title="前置仓账户对比" :rows="comparisonRows" :columns="[{key:'name',label:'前置仓'},{key:'pending',label:'待结算'},{key:'available',label:'可提现'},{key:'frozen',label:'冻结中'},{key:'paid',label:'已提现'}]"/>
</section></template>
