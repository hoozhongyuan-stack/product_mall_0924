<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {confirmedWrite,type Account} from '../../api'
import {amount,type StoreWithdrawal} from './types'
const props=defineProps<{account:Account;storeId:string;item:StoreWithdrawal}>()
const emit=defineEmits<{refresh:[]}>()
type Action='APPROVE'|'REJECT'|'PAY'|'PAYEE'
const action=ref<Action|null>(null),password=ref(''),reason=ref(''),reference=ref(''),busy=ref(false),needsRead=ref(false),error=ref('')
const payee=ref<{payeeName:string;bankName:string;bankAccount:string}|null>(null)
let epoch=0
const permitted=computed(()=>props.account.permissionCodes.includes('stores.accounts.manage'))
const valid=computed(()=>permitted.value&&!!password.value&&!!action.value&&!busy.value&&!needsRead.value&&(action.value!=='REJECT'||!!reason.value.trim())&&(action.value!=='PAY'||!!reference.value.trim()))
const labels:Record<Action,string>={APPROVE:'审核通过',REJECT:'驳回申请',PAY:'登记线下发放',PAYEE:'查看完整收款信息'}
function clear(){password.value='';reason.value='';reference.value='';action.value=null;payee.value=null}
function begin(value:Action){if(!permitted.value||busy.value||needsRead.value)return;clear();action.value=value;error.value=''}
async function submit(){if(!valid.value||!action.value)return;const current=++epoch,id=props.item.id,revision=props.item.revision,chosen=action.value;busy.value=true;error.value='';const path=`/stores/${encodeURIComponent(props.storeId)}/withdrawals/${encodeURIComponent(id)}`
try{if(chosen==='PAYEE'){const result=await confirmedWrite<{payeeName:string;bankName:string;bankAccount:string}>('stores.withdrawal.payee',password.value,`${path}/payee`,'POST',{expectedRevision:revision},id,revision);if(current===epoch){clear();payee.value=result}}
else{const result=await confirmedWrite<StoreWithdrawal>(chosen==='PAY'?'stores.withdrawal.pay':'stores.withdrawal.review',password.value,`${path}/${chosen==='PAY'?'pay':'review'}`,'POST',chosen==='PAY'?{expectedRevision:revision,paymentReference:reference.value.trim(),reason:reason.value.trim()}:{expectedRevision:revision,decision:chosen,reason:reason.value.trim()},id,revision);if(current===epoch){const status=chosen==='PAY'?'PAID':chosen==='APPROVE'?'APPROVED_PENDING_PAYMENT':'REJECTED';if(result.id!==id||result.revision!==revision+1||result.status!==status)throw new Error('Unexpected result');clear();needsRead.value=true;emit('refresh')}}}
catch{if(current===epoch){clear();needsRead.value=true;error.value='操作结果未确认，请重新读取账户，核对记录后再操作。'}}finally{if(current===epoch){busy.value=false;password.value=''}}}
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes,props.storeId,props.item.id,props.item.revision]),()=>{epoch++;clear();busy.value=false;needsRead.value=false;error.value=''})
onUnmounted(()=>{epoch++;clear()})
</script>
<template>
<div v-if="permitted" class="store-withdrawal-review">
<div class="store-actions"><template v-if="item.status==='PENDING_REVIEW'"><button class="text-button" data-action="approve" :disabled="busy||needsRead" @click="begin('APPROVE')">审核通过</button><button class="text-button" data-action="reject" :disabled="busy||needsRead" @click="begin('REJECT')">驳回申请</button></template><template v-if="item.status==='APPROVED_PENDING_PAYMENT'"><button class="text-button" data-action="pay" :disabled="busy||needsRead" @click="begin('PAY')">登记线下发放</button><button class="text-button" data-action="payee" :disabled="busy||needsRead" @click="begin('PAYEE')">查看完整收款信息</button></template></div>
<p v-if="error" role="alert">{{error}}</p>
<div v-if="payee" role="status"><p>收款人：{{payee.payeeName}} · {{payee.bankName}}</p><p>收款账号：{{payee.bankAccount}}</p><button class="secondary-button" @click="clear">隐藏收款信息</button></div>
<form v-if="action" class="store-form store-withdrawal-form" :aria-label="labels[action]" @submit.prevent="submit"><h3 class="wide">{{labels[action]}} · {{amount(item.amountFen)}}</h3><p class="wide">{{action==='PAY'?'仅在已完成线下转账后登记。此操作不会发起银行付款。':action==='APPROVE'?'审核通过后仍需线下发放并登记付款流水。':action==='REJECT'?'驳回后，冻结金额将退回前置仓可提现余额。':'完整收款信息只在当前页面临时显示，关闭后清除。'}}</p><label v-if="action==='PAY'" class="wide" :for="`withdrawal-reference-${item.id}`">线下付款流水 / 凭证编号<input :id="`withdrawal-reference-${item.id}`" v-model="reference" maxlength="120" :disabled="busy" /></label><label v-if="action!=='PAYEE'" class="wide" :for="`withdrawal-reason-${item.id}`">{{action==='REJECT'?'驳回原因（必填）':'操作说明（选填）'}}<textarea :id="`withdrawal-reason-${item.id}`" v-model="reason" maxlength="300" :disabled="busy" /></label><label class="wide" :for="`withdrawal-password-${item.id}`">当前账号密码<input :id="`withdrawal-password-${item.id}`" v-model="password" type="password" autocomplete="current-password" :disabled="busy" /></label><div class="store-actions wide"><button class="primary-button" type="submit" :disabled="!valid">{{busy?'正在确认…':`确认${labels[action]}`}}</button><button class="secondary-button" type="button" :disabled="busy" @click="clear">取消</button></div></form>
</div>
</template>
