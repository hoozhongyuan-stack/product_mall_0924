<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import {api,type Account} from '../../api'
import {modeLabel,type Store} from './types'
import './stores.css'
const props=defineProps<{account:Account}>()
const canManage=computed(()=>props.account.permissionCodes.includes('stores.manage'))
const loading=ref(false),error=ref(''),search=ref(''),page=ref(1),total=ref(0),items=ref<Store[]>([])
let generation=0
async function load(reset=false){if(reset)page.value=1;const current=++generation;loading.value=true;error.value=''
 try{const data=await api<{items:Store[];total:number}>(`/stores?${new URLSearchParams({search:search.value.trim(),page:String(page.value),pageSize:'20'})}`);if(current===generation){items.value=data.items;total.value=data.total}}
 catch(e){if(current===generation){items.value=[];error.value=e instanceof Error?e.message:'读取前置仓失败，请重试。'}}finally{if(current===generation)loading.value=false}}
onMounted(()=>load())
</script>
<template><section class="page-content stores-page">
<header class="page-heading"><div><h1>前置仓管理</h1><p>统一维护前置仓资料、服务范围与人员授权。订单及售后在订单模块处理。</p></div><RouterLink v-if="canManage" to="/stores/new" class="primary-button">新增前置仓</RouterLink></header>
<form class="store-search" @submit.prevent="load(true)"><label>前置仓名称<input v-model="search" type="search" maxlength="100" placeholder="搜索前置仓名称"></label><button class="primary-button" :disabled="loading">查询前置仓</button><button type="button" class="secondary-button" :disabled="loading" @click="search='';load(true)">重置</button></form>
<p v-if="loading" role="status">正在读取前置仓…</p><p v-else-if="error" role="alert">{{error}} <button class="text-button" @click="load()">重新加载</button></p>
<p v-else-if="!items.length" class="empty-state">暂无前置仓。创建前置仓后，再分配管理人员与设置可售商品。</p>
<div v-else class="panel table-wrap" tabindex="0" aria-label="前置仓列表，可横向滚动"><table><thead><tr><th>前置仓</th><th>联系人</th><th>营业时间</th><th>服务方式</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="store in items" :key="store.id"><td><strong>{{store.name}}</strong><small>{{store.address}}</small></td><td>{{store.contactName}}<small>{{store.contactPhone}}</small></td><td>{{store.openingHours||'未设置'}}</td><td>{{store.supportedModes.map(modeLabel).join(' / ')}}</td><td>{{!store.enabled?'已停用':store.acceptingOrders?'接单中':'暂停接单'}}</td><td><RouterLink :to="`/stores/${store.id}`" class="text-link">{{canManage?'编辑 / 人员授权':'查看详情'}}</RouterLink></td></tr></tbody></table></div>
<div v-if="!loading&&!error" class="store-pagination"><span>共 {{total}} 家</span><button class="secondary-button" :disabled="page<=1" @click="page--;load()">上一页</button><button class="secondary-button" :disabled="page*20>=total" @click="page++;load()">下一页</button></div>
</section></template>
