<script setup lang="ts">
import {ElButton,ElInput,ElPagination,ElTable,ElTableColumn} from 'element-plus'
import {computed,ref,watch} from 'vue'
import {api,type Account} from '../../api'
import {base,reasonText,money} from './types'
import './dining.css'
import AdminListPager from '../../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../../shared/list/AdminStatusBadge.vue'
import AdminSelectedRowsDialog from '../../shared/list/AdminSelectedRowsDialog.vue'
import AdminSelectionBar from '../../shared/list/AdminSelectionBar.vue'
const props=defineProps<{account:Account}>()
const selectionVersion=ref(0)
const pageSize=ref(20)
const items=ref<{storeName:string;productName:string;skuCode:string;purchaseCostFen:number|null}[]>([])
const page=ref(1),total=ref(0)
const search=ref(''),selected=ref<typeof items.value>([]),comparison=ref(false)
const comparisonRows=computed(()=>selected.value.map(r=>({store:r.storeName,product:r.productName,sku:r.skuCode,cost:r.purchaseCostFen===null?'未配置':money(r.purchaseCostFen)})))
const error=ref(''),loading=ref(false)
let generation=0
async function load(){const current=++generation;loading.value=true;items.value=[];selected.value=[];comparison.value=false;error.value='';try{const result=await api<{items:typeof items.value;total:number}>(`${base}/purchase-costs?pageSize=${pageSize.value}&page=${page.value}&search=${encodeURIComponent(search.value.trim())}`);if(current===generation){items.value=result.items;total.value=result.total}}catch(e){error.value=reasonText(e)}finally{loading.value=false}}
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes]),()=>{page.value=1;search.value='';load()},{immediate:true})
</script>
<template><section class="page-content dining-page admin-list-page"><header class="page-heading"><div><h1>历史餐饮进货价档案</h1><p>餐饮独立进货价配置已停用。新订单直接使用前置仓商品进货价；历史订单保留原结算快照。</p></div></header><section class="panel admin-list-page"><RouterLink v-if="account.permissionCodes.includes('stores.read')" to="/stores/profit-rules"><el-button>前往前置仓进货价配置</el-button></RouterLink><p v-if="loading" role="status">正在读取历史档案…</p><p v-if="error" role="alert">{{error}} <el-button @click="load">重试</el-button></p><form class="toolbar" @submit.prevent="page=1;load()"><label>商品名称 / SKU / 前置仓<el-input v-model="search" aria-label="历史档案关键词" maxlength="120"/></label><el-button native-type="submit" type="primary" :disabled="loading">查询</el-button><el-button @click="search='';page=1;load()">重置</el-button></form><AdminSelectionBar :count="selected.length" :loading="loading" @clear="selected=[];selectionVersion++"><el-button :disabled="!selected.length" @click="comparison=true">对比所选档案</el-button></AdminSelectionBar><el-table :key="selectionVersion" class="admin-list-table" :data="items" @selection-change="selected=$event" empty-text="暂无历史独立进货价档案"><el-table-column type="selection" width="50"/><el-table-column prop="storeName" label="前置仓"/><el-table-column prop="productName" label="商品"/><el-table-column prop="skuCode" label="SKU"/><el-table-column label="历史配置单价"><template #default="{row}">{{row.purchaseCostFen===null?'未配置':money(row.purchaseCostFen)}}</template></el-table-column></el-table><AdminListPager :page="page" :page-size="pageSize" :total="total" :loading="loading" @change="(p,s)=>{page=p;pageSize=s;load()}"/></section><AdminSelectedRowsDialog v-model="comparison" title="历史进货价档案对比" :rows="comparisonRows" :columns="[{key:'store',label:'前置仓'},{key:'product',label:'商品'},{key:'sku',label:'SKU'},{key:'cost',label:'历史成本'}]"/></section></template>
