<script setup lang="ts">
import {ElButton, ElInput, ElTable, ElTableColumn, ElPagination, vLoading} from 'element-plus'
import {computed,ref,watch} from 'vue'
import {api,type Account} from '../../api'
import {base,reasonText,type Restaurant,type PageResult} from './types'
import './dining.css'
const props=defineProps<{account:Account}>()
const canManage=computed(()=>props.account.permissionCodes.includes('dining.manage'))
const items=ref<Restaurant[]>([]),total=ref(0),page=ref(1),search=ref(''),loading=ref(false),error=ref('')
let generation=0
async function load(){const current=++generation;loading.value=true;error.value='';items.value=[];try{const result=await api<PageResult<Restaurant>>(`${base}/restaurants?${new URLSearchParams({page:String(page.value),pageSize:'20',search:search.value.trim()})}`);if(current===generation){items.value=result.items;total.value=result.total}}catch(e){if(current===generation)error.value=reasonText(e)}finally{if(current===generation)loading.value=false}}
function query(){page.value=1;load()}
watch(()=>props.account.accountId,load,{immediate:true})
</script>
<template><section class="page-content dining-page"><header class="page-heading"><div><h1>餐饮联盟店</h1><p>仅限扫码访问，使用系统匹配前置仓的库存与履约；缺货不切仓。</p></div><RouterLink v-if="canManage" to="/dining-alliance/restaurants/new"><el-button type="primary">新增餐饮店</el-button></RouterLink></header><section class="panel"><form class="toolbar" @submit.prevent="query"><el-input v-model="search" aria-label="搜索餐饮店" placeholder="搜索店名" clearable style="width:280px"/><el-button native-type="submit" :loading="loading">查询</el-button></form><p v-if="error" class="error" role="alert">{{error}} <el-button @click="load">重试</el-button></p><div class="table-wrap"><el-table v-loading="loading" :data="items" empty-text="暂无餐饮店"><el-table-column label="餐饮店" min-width="180"><template #default="{row}"><RouterLink :to="`/dining-alliance/restaurants/${row.id}`">{{row.name}}</RouterLink></template></el-table-column><el-table-column prop="address" label="地址" min-width="220"/><el-table-column label="履约前置仓" min-width="150"><template #default="{row}">{{row.fulfillmentStore?.name||'尚未匹配'}}</template></el-table-column><el-table-column prop="tableCount" label="桌数" width="80"/><el-table-column label="状态" width="90"><template #default="{row}">{{row.enabled?'启用':'停用'}}</template></el-table-column></el-table></div><el-pagination :current-page="page" :total="total" :page-size="20" layout="prev,pager,next,total" @current-change="page=$event;load()"/></section></section></template>
