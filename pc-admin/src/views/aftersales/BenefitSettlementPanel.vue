<script setup lang="ts">
import { confirmAction } from '../../shared/confirm'

import { computed, ref, onUnmounted } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { confirmedWrite, ApiError, type Account } from '../../api'
import { pendingBenefitSettlement, type BenefitPending } from './settlement.mjs'
import { type AfterSaleCase } from './types'
import {isPointsOrder} from '../../shared/order-settlement.mjs'
import {pointRefundState} from './point-refund-state.mjs'
const props=defineProps<{item:AfterSaleCase;account:Account}>()
const emit=defineEmits<{refresh:[];busy:[value:boolean]}>()
const dialog=ref(false),password=ref(''),error=ref(''),busy=ref(false),unknown=ref(false)
const pending=ref<BenefitPending|null>(null)
let active=true
try {
 if(props.item.status==='COMPLETED') pendingBenefitSettlement(sessionStorage,props.account.accountId,props.item.caseId,null)
 else pending.value=pendingBenefitSettlement(sessionStorage,props.account.accountId,props.item.caseId)
} catch {error.value='无法读取本机待核实记录，请查询最新状态并检查浏览器存储。'}
const permitted=computed(()=>['refund.prepare','aftersale.read'].every(code=>props.account.permissionCodes.includes(code)))
const canSettle=computed(()=>permitted.value && props.item.canSettleBenefits===true && props.item.totalRefundAmountFen===0 && !unknown.value)
function setBusy(value:boolean){busy.value=value;emit('busy',value)}
async function confirm(){
 if(busy.value||!canSettle.value||!password.value)return
 setBusy(true);error.value=''
 try{
  const frozen=pending.value || {key:crypto.randomUUID(),body:{expectedRevision:props.item.revision}}
  pendingBenefitSettlement(sessionStorage,props.account.accountId,props.item.caseId,frozen);pending.value=frozen
  await confirmedWrite('refund.benefits.settle',password.value,`/aftersales/${props.item.caseId}/settle-benefits`,'POST',frozen.body,props.item.caseId,props.item.revision,{'Idempotency-Key':frozen.key})
  if(active){pendingBenefitSettlement(sessionStorage,props.account.accountId,props.item.caseId,null);pending.value=null;dialog.value=false;setBusy(false);emit('refresh')}
 }catch(reason){if(active){unknown.value=!(reason instanceof ApiError && [400,403,404,409,422].includes(reason.status));error.value=reason instanceof Error?reason.message:'处理结果未知，请先刷新查询。';if(unknown.value)dialog.value=false;else{pendingBenefitSettlement(sessionStorage,props.account.accountId,props.item.caseId,null);pending.value=null}}}
 finally{if(active){password.value='';setBusy(false)}}
}
const mayLeave=async ()=>!busy.value && (!(unknown.value||pending.value) || await confirmAction('权益处理结果待核实，确定离开并稍后查询？'))
onBeforeRouteLeave(mayLeave);onBeforeRouteUpdate(mayLeave)
function beforeUnload(event:BeforeUnloadEvent){if(busy.value||unknown.value||pending.value){event.preventDefault();event.returnValue=''}}
window.addEventListener('beforeunload',beforeUnload)
onUnmounted(()=>{active=false;password.value='';window.removeEventListener('beforeunload',beforeUnload)})
</script>
<template>
<section class="panel order-section"><h2>{{isPointsOrder(item)?'退积分与数量确认':'零现金售后与权益确认'}}</h2><p>{{isPointsOrder(item)?`${pointRefundState(item).summary} 无现金转出，数量、库存或核销额度按原积分快照处理。`:'本次现金退款合计为零，没有现金转出。确认后按成交快照处理售后数量、库存或核销额度，以及适用的优惠券和积分。'}}</p><p v-if="item.status==='COMPLETED'" class="order-success">{{isPointsOrder(item)?'退积分售后已完成，不产生资金退款凭证。':'零现金售后已完成，不产生资金退款凭证。'}}</p><p v-if="pending && !unknown" class="order-note">已查询最新状态，上次请求内容与标识已保留，可沿同一请求重试。</p><p v-if="unknown" class="order-warning">提交结果未知，请先刷新处理状态；查询完成前不能再次确认。</p><p v-if="error" class="error" role="alert">{{error}}</p><button v-if="canSettle" class="primary-button" :disabled="busy" @click="dialog=true">{{pending?'沿同一请求确认权益处理':'核对数量与权益，授权完成售后'}}</button><p v-else-if="item.status==='WAITING_REFUND' && !permitted" class="order-note">当前账号没有权益结算权限。</p>
<el-dialog v-model="dialog" title="授权确认零现金售后" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @closed="password=''"><p>{{item.orderNo}} · {{item.title}}</p><p>批准处理 {{item.effectiveRefundQuantity ?? item.quantity}} 件。<span v-if="isPointsOrder(item)">{{pointRefundState(item).label}}：{{pointRefundState(item).value}}。</span>没有现金退回，权益和数量以服务端规则结算；已验收库存不会再次回库。</p><label>当前账号密码<input v-model="password" type="password" autocomplete="current-password" :disabled="busy"></label><p v-if="error" role="alert">{{error}}</p><template #footer><button class="secondary-button" :disabled="busy" @click="dialog=false">返回核对</button><button class="primary-button" :disabled="busy || !password || !canSettle" @click="confirm">{{busy?'确认中…':'授权完成数量与权益处理'}}</button></template></el-dialog>
</section>
</template>
