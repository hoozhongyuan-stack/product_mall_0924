<script setup lang="ts">
import {ElButton,ElPagination,ElTable,ElTableColumn} from 'element-plus'
import {ref,onMounted} from 'vue'
import {api,type Account} from '../../api'
import {base,reasonText,money} from './types'
import './dining.css'
defineProps<{account:Account}>()
const items=ref<{storeName:string;productName:string;skuCode:string;purchaseCostFen:number|null}[]>([])
const page=ref(1),total=ref(0)
const error=ref(''),loading=ref(false)
async function load(){loading.value=true;error.value='';try{const result=await api<{items:typeof items.value;total:number}>(`${base}/purchase-costs?pageSize=20&page=${page.value}`);items.value=result.items;total.value=result.total}catch(e){error.value=reasonText(e)}finally{loading.value=false}}
onMounted(load)
</script>
<template><section class="page-content dining-page"><header class="page-heading"><div><h1>历史餐饮进货价档案</h1><p>餐饮独立进货价配置已停用。新订单直接使用前置仓商品进货价；历史订单保留原结算快照。</p></div></header><section class="panel"><RouterLink v-if="account.permissionCodes.includes('stores.read')" to="/stores/profit-rules"><el-button>前往前置仓进货价配置</el-button></RouterLink><p v-if="loading" role="status">正在读取历史档案…</p><p v-if="error" role="alert">{{error}} <el-button @click="load">重试</el-button></p><el-table :data="items" empty-text="暂无历史独立进货价档案"><el-table-column prop="storeName" label="前置仓"/><el-table-column prop="productName" label="商品"/><el-table-column prop="skuCode" label="SKU"/><el-table-column label="历史配置单价"><template #default="{row}">{{row.purchaseCostFen===null?'未配置':money(row.purchaseCostFen)}}</template></el-table-column></el-table><el-pagination :current-page="page" :total="total" :page-size="20" layout="prev,pager,next,total" @current-change="page=$event;load()"/></section></section></template>
