<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api,type Account} from '../../api'
import {amount,type StoreAccount} from './types'
import './stores.css'
defineProps<{account:Account}>()
const page=ref(1),total=ref(0)
const items=ref<StoreAccount[]>([]),loading=ref(false),error=ref('')
async function load(){loading.value=true;error.value='';try{const result=await api<{items:StoreAccount[];total:number}>(`/stores/accounts?page=${page.value}&pageSize=20`);items.value=result.items;total.value=result.total}catch(e){items.value=[];error.value=e instanceof Error?e.message:'读取门店账户失败。'}finally{loading.value=false}}
onMounted(load)
</script>
<template><section class="page-content stores-page"><header class="page-heading"><div><h1>门店账户</h1><p>按门店查看结算余额、收入明细与提现申请，平台审核后登记线下发放。</p></div><button class="secondary-button" :disabled="loading" @click="load">刷新账户</button></header><p v-if="loading" role="status">正在读取账户…</p><p v-else-if="error" role="alert">{{error}} <button class="text-button" @click="load">重试</button></p><p v-else-if="!items.length" class="empty-state">暂无门店账户。请先创建门店。</p><div v-else class="panel table-wrap" tabindex="0" aria-label="门店账户列表，可横向滚动"><table><thead><tr><th>门店</th><th>待结算</th><th>可提现</th><th>冻结中</th><th>已提现</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in items" :key="item.storeId"><td>{{item.storeName}}</td><td>{{amount(item.balance.pendingFen)}}</td><td>{{amount(item.balance.availableFen)}}</td><td>{{amount(item.balance.frozenFen)}}</td><td>{{amount(item.balance.paidFen)}}</td><td>{{item.settlementReady?'已配置':'待配置'}}</td><td><RouterLink class="text-link" :to="`/stores/accounts/${item.storeId}`">账户详情</RouterLink></td></tr></tbody></table></div><div v-if="!loading&&!error" class="store-pagination"><span>共 {{total}} 家</span><button class="secondary-button" :disabled="page<=1" @click="page--;load()">上一页</button><button class="secondary-button" :disabled="page*20>=total" @click="page++;load()">下一页</button></div></section></template>
