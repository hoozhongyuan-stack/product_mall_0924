<script setup lang="ts">
import {onUnmounted,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {api,type Account} from '../../api'
import {amount,incomeStatus,withdrawalStatus,type StoreAccount} from './types'
import StoreWithdrawalReview from './StoreWithdrawalReview.vue'
import './stores.css'
const props=defineProps<{account:Account}>()
const route=useRoute(),data=ref<StoreAccount|null>(null),loading=ref(false),error=ref('')
let generation=0
async function load(){const current=++generation;loading.value=true;error.value='';data.value=null;try{const result=await api<StoreAccount>(`/stores/${encodeURIComponent(String(route.params.storeId))}/account`);if(current===generation)data.value=result}catch(e){if(current===generation)error.value=e instanceof Error?e.message:'账户详情读取失败。'}finally{if(current===generation)loading.value=false}}
function date(value:string|null){return value?new Date(value).toLocaleString('zh-CN'):'—'}
watch(()=>JSON.stringify([route.params.storeId,props.account.accountId,props.account.permissionCodes]),load,{immediate:true})
onUnmounted(()=>{generation++;data.value=null})
</script>
<template>
<section class="page-content stores-page"><header class="page-heading"><div><h1>{{data?.storeName||'前置仓'}}账户</h1><p>前置仓结算含进货成本与前置仓利润，扣除前置仓承担的运费。收入与提现以实际结算和线下付款记录为准。</p></div><div class="store-actions"><button class="secondary-button" :disabled="loading" @click="load">重新读取账户</button><RouterLink to="/stores/accounts" class="secondary-button">返回账户列表</RouterLink></div></header>
<p v-if="loading" role="status">正在读取账户详情…</p><p v-else-if="error" role="alert">{{error}} <button class="text-button" @click="load">重试</button></p>
<template v-else-if="data"><div v-if="!data.settlementReady" class="notice" role="status">{{data.reason||'结算条件尚未具备，请查看收入明细。'}}</div><dl class="store-balances"><div><dt>待结算</dt><dd>{{amount(data.balance.pendingFen)}}</dd></div><div><dt>可提现</dt><dd>{{amount(data.balance.availableFen)}}</dd></div><div><dt>冻结中</dt><dd>{{amount(data.balance.frozenFen)}}</dd></div><div><dt>已提现</dt><dd>{{amount(data.balance.paidFen)}}</dd></div></dl>
<section class="panel store-section"><h2>收入明细</h2><p>最近 100 条记录</p><p>订单完成、超过订单售后期且无处理中售后，才可结算。待人工核对的记录暂不计入可提现余额。</p><p v-if="!data.income.length">{{data.settlementReady?'暂无结算收入明细。前置仓订单完成后将在此展示。':'结算口径待配置，尚未生成收入明细。'}}</p><div v-else class="table-wrap" tabindex="0" aria-label="前置仓收入明细，可横向滚动"><table><thead><tr><th>订单</th><th>订单实付</th><th>进货成本</th><th>平台利润</th><th>前置仓承担运费</th><th>前置仓结算</th><th>结算状态</th><th>可结算时间</th></tr></thead><tbody><tr v-for="item in data.income" :key="item.id"><td><RouterLink class="text-link" :to="`/orders/${item.orderId}`">{{item.orderNo}}</RouterLink></td><td>{{amount(item.paidFen)}}</td><td>{{item.costFen==null?'待人工核对':amount(item.costFen)}}</td><td>{{item.platformFen==null?'待人工核对':amount(item.platformFen)}}</td><td>{{amount(item.freightFen)}}</td><td>{{item.storeFen==null?'待人工核对':amount(item.storeFen)}}</td><td>{{incomeStatus(item.status)}}<small v-if="item.reason">{{item.reason}}</small></td><td>{{date(item.availableAt)}}<small v-if="item.settledAt">结算于 {{date(item.settledAt)}}</small></td></tr></tbody></table></div></section>
<section class="panel store-section"><h2>提现记录</h2><p>最近 100 条记录</p><p>前置仓提交申请后冻结对应余额；平台审核通过后线下转账，再登记付款流水。</p><p v-if="!data.withdrawals.length">{{data.withdrawalReady?'暂无提现申请。前置仓可在小程序前置仓中心发起提现。':'提现渠道待配置，暂未开放提现。'}}</p><button v-if="!data.withdrawalReady" class="secondary-button" disabled>提现待配置</button><div v-for="item in data.withdrawals" :key="item.id" class="store-withdrawal-row"><header><strong>{{amount(item.amountFen)}} · {{withdrawalStatus(item.status)}}</strong><small>申请于 {{date(item.createdAt)}} · {{item.id}}</small></header><p>{{item.payeeName}} · {{item.bankName}} · {{item.bankAccount}}</p><p v-if="item.reason">说明：{{item.reason}}</p><p v-if="item.paidAt">线下发放于 {{date(item.paidAt)}} · 付款凭证 {{item.paymentReference}}</p><StoreWithdrawalReview :account="account" :store-id="data.storeId" :item="item" @refresh="load" /></div></section>
</template></section>
</template>
