<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, type Account, type Confirmation } from '../../../api'
import { confirmAction } from '../../../shared/confirm'
import '../stores.css'
import './procurement.css'
const props=defineProps<{account:Account}>()
const instructions=ref(''), original=ref(''), revision=ref(0), password=ref('')
const loading=ref(false), saving=ref(false), ready=ref(false), error=ref(''), notice=ref('')
const manage=computed(()=>props.account.permissionCodes.includes('stores.procurement.manage'))
const dirty=computed(()=>instructions.value!==original.value)
let epoch=0
async function load(){
 const seq=++epoch;loading.value=true;error.value='';ready.value=false
 if(!props.account.permissionCodes.includes('stores.procurement.read')){loading.value=false;return}
 try {const result=await api<{paymentInstructions:string;revision:number}>('/stores/procurement/settings');if(seq===epoch){instructions.value=result.paymentInstructions;original.value=result.paymentInstructions;revision.value=result.revision;ready.value=true}}
 catch(reason){if(seq===epoch)error.value=reason instanceof Error?reason.message:'收款说明读取失败。'}
 finally{if(seq===epoch)loading.value=false}
}
async function reload(){if(saving.value)return;if(dirty.value&&!await confirmAction('重新读取会放弃未保存修改，继续？'))return;await load()}
async function save(){
 if(!ready.value||saving.value||!manage.value)return
 if(!instructions.value.trim()||instructions.value.length>2000){error.value='请填写采购收款说明，不超过 2000 字。';return}
 if(!password.value){error.value='请填写管理员密码确认。';return}
 const seq=epoch, body={paymentInstructions:instructions.value.trim(),expectedRevision:revision.value}
 saving.value=true;error.value='';notice.value=''
 try{
  const confirmation=await api<Confirmation>('/auth/confirm',{method:'POST',body:JSON.stringify({action:'stores.procurement.configure',password:password.value,objectId:'procurement-settings',revision:body.expectedRevision})})
  if(seq!==epoch)return
  const result=await api<{paymentInstructions:string;revision:number}>('/stores/procurement/settings',{method:'PUT',body:JSON.stringify(body),headers:{'X-Action-Confirmation':confirmation.confirmationToken}})
  if(seq===epoch){instructions.value=result.paymentInstructions;original.value=result.paymentInstructions;revision.value=result.revision;notice.value='采购收款说明已保存，已审单订单仍使用原收款说明。'}
 }catch(reason){if(seq===epoch){error.value=(reason instanceof Error?reason.message:'保存结果未知。')+' 请重新读取后核对，未保存内容仍保留。';ready.value=false}}
 finally{if(seq===epoch){saving.value=false;password.value=''}}
}
const leave=async()=>!saving.value&&(!dirty.value||await confirmAction('有未保存的收款说明，确定离开？'))
onBeforeRouteLeave(leave)
function beforeUnload(e:BeforeUnloadEvent){if(dirty.value||saving.value){e.preventDefault();e.returnValue=''}}
window.addEventListener('beforeunload',beforeUnload)
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes]),()=>{instructions.value='';original.value='';password.value='';saving.value=false;load()},{immediate:true,flush:'sync'})
onUnmounted(()=>{epoch++;password.value='';window.removeEventListener('beforeunload',beforeUnload)})
</script>
<template><section class="stores-page procurement-page panel store-section"><h2>采购收款说明</h2><p>向前置仓展示线下采购付款方式、收款账户和备注要求。审单时冻结说明，后续修改仅影响新审单订单。</p><p v-if="loading" role="status">正在读取…</p><p v-if="error" role="alert" class="error">{{error}}</p><p v-if="notice" role="status">{{notice}}</p><form @submit.prevent="save"><label>收款说明<textarea v-model="instructions" maxlength="2000" rows="9" :disabled="!manage || saving || loading || !ready"></textarea></label><small>{{instructions.length}} / 2000</small><label v-if="manage">管理员密码<input v-model="password" type="password" autocomplete="current-password" :disabled="saving"></label><div class="store-toolbar"><button v-if="manage" :disabled="saving || loading || !ready">{{saving?'正在保存…':'保存收款说明'}}</button><button type="button" :disabled="saving || loading" @click="reload">重新读取</button></div></form></section></template>
<style scoped>label{display:grid;gap:8px;margin:18px 0}textarea,input{padding:12px;border:1px solid #d7dce0;border-radius:6px;max-width:760px;box-sizing:border-box;width:100%}</style>
