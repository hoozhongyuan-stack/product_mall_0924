<script setup lang="ts">
import { confirmAction } from '../../shared/confirm'

import { computed, ref, onUnmounted } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { api, confirmedWrite, ApiError, type Account } from '../../api'
import { money, type AfterSaleCase } from './types'
import { refundAmounts } from './settlement.mjs'
import { wechatRefundLabel, wechatFailureLabel } from './returns.mjs'
const props=defineProps<{item:AfterSaleCase;account:Account}>()
const emit=defineEmits<{refresh:[];busy:[value:boolean]}>()
const password=ref('');const dialog=ref(false);const busy=ref(false);const error=ref('');const unknown=ref(false);const requestKey=ref('');let active=true
const total=computed(()=>refundAmounts(props.item).total)
const permitted=computed(()=>props.account.permissionCodes.includes('refund.prepare'))
const canDispatch=computed(()=>permitted.value && props.item.status==='WAITING_REFUND' && props.item.wechatRefund?.available && props.item.wechatRefund.canDispatch && !unknown.value)
const canQuery=computed(()=>permitted.value && props.item.wechatRefund?.available && props.item.wechatRefund.canQuery)
function setBusy(value:boolean){busy.value=value;emit('busy',value)}
async function dispatch(){
 if(!canDispatch.value || busy.value || !password.value)return
 setBusy(true);error.value='';requestKey.value ||=crypto.randomUUID()
 try{ await confirmedWrite('refund.wechat.dispatch',password.value,`/aftersales/${props.item.caseId}/wechat-refund`,'POST',{expectedRevision:props.item.revision},props.item.caseId,props.item.revision,{'Idempotency-Key':requestKey.value});if(active){dialog.value=false;setBusy(false);emit('refresh')} }
 catch(reason){if(active){unknown.value=!(reason instanceof ApiError && reason.status<500);error.value=reason instanceof Error?reason.message:'退款结果未知，请查单。';if(unknown.value)dialog.value=false}}
 finally{if(active){password.value='';setBusy(false)}}
}
async function query(){
 if(!canQuery.value || busy.value)return
 setBusy(true);error.value=''
 try{await api(`/aftersales/${props.item.caseId}/wechat-refund/query`,{method:'POST',body:'{}'});if(active){unknown.value=false;setBusy(false);emit('refresh')}}
 catch(reason){if(active)error.value=reason instanceof Error?reason.message:'查单失败，请稍后再查。'}
 finally{if(active)setBusy(false)}
}
const mayLeave=async ()=>!busy.value && (!unknown.value || await confirmAction('退款结果待核实，确定离开并稍后查单？'))
onBeforeRouteLeave(mayLeave)
onBeforeRouteUpdate(mayLeave)
function beforeUnload(event:BeforeUnloadEvent){if(busy.value || unknown.value){event.preventDefault();event.returnValue=''}}
window.addEventListener('beforeunload',beforeUnload)
onUnmounted(()=>{active=false;password.value='';window.removeEventListener('beforeunload',beforeUnload)})
</script>
<template><section class="panel order-section"><h2>微信原路退款</h2><p>{{ wechatRefundLabel(item.refundStatus || item.wechatRefund?.status) }}</p><p v-if="item.wechatRefund?.refundNo">退款业务号：{{ item.wechatRefund.refundNo }}</p><p>现金退款合计：{{ money(total) }}。受理成功仍可能处理中，只有验签通知或主动查单确认成功才完成售后。</p><p v-if="!item.wechatRefund?.available" class="order-warning">微信退款尚未配置或未启用。当前不能发起真实退款，商户账号联调后再开放。</p><p v-if="wechatFailureLabel(item.wechatRefund?.failureCode)" class="order-warning">{{ wechatFailureLabel(item.wechatRefund?.failureCode) }}</p><p v-if="unknown" class="order-warning">上次请求结果未知，请先刷新处理状态并查单；不要重新发起退款。</p><p v-if="error" class="error" role="alert">{{ error }}</p><button v-if="canDispatch" class="primary-button" :disabled="busy" @click="dialog=true">{{ item.wechatRefund?.status==='FAILED' ? '沿原退款单重试' : '核对金额，发起原路退款' }}</button><button v-if="canQuery" class="secondary-button" :disabled="busy" @click="query">{{ busy?'查询中…':'查询微信退款结果' }}</button><p v-if="!permitted">当前账号没有退款操作权限。</p><el-dialog v-model="dialog" title="授权发起微信原路退款" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @closed="password=''"><p>{{ item.orderNo }} · {{ item.title }}</p><p>向原支付渠道申请退款 {{ money(total) }}。结果以微信通知和查询为准。</p><label>当前账号密码<input v-model="password" type="password" autocomplete="current-password" :disabled="busy"></label><p v-if="error" role="alert">{{ error }}</p><template #footer><button class="secondary-button" :disabled="busy" @click="dialog=false">返回核对</button><button class="primary-button" :disabled="busy || !password || !canDispatch" @click="dispatch">{{ busy?'发起中…':'授权发起原路退款' }}</button></template></el-dialog></section></template>
