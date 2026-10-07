<script setup lang="ts">
import { confirmAction } from '../shared/confirm'

import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import {api,ApiError,type Account,type Confirmation} from '../api'
import {pendingRules,ruleError,rulesBody,type RulesForm,type PendingRules} from './customers/rules.mjs'
import {type Rule} from './customers/types'
import './customers/customers.css'
const props=defineProps<{account:Account}>()
const rule=ref<Rule|null>(null),loading=ref(true),busy=ref(false),error=ref(''),notice=ref(''),password=ref(''),confirmOpen=ref(false),saved=ref(''),pending=ref<PendingRules|null>(null),verified=ref(false),storageError=ref('')
let generation=0
const form=ref<RulesForm>({grades:[],earnYuan:'1',earnPoints:'1',deductPoints:'100',deductYuan:'1',maxPercent:'50',validDays:'365',refundValidDays:'30',reason:''})
const canManage=computed(()=>props.account.permissionCodes.includes('member.rules.manage'))
const dirty=computed(()=>saved.value!==JSON.stringify(form.value)),validation=computed(()=>ruleError(form.value)),locked=computed(()=>busy.value||!!pending.value)
const yuan=(v:number)=>(v/100).toFixed(2)
function assign(r:Rule){form.value={grades:r.grades.map(g=>({id:g.id,amount:yuan(g.minimumSpendFen||0)})),earnYuan:yuan(r.points.earnUnitFen),earnPoints:String(r.points.earnPoints),deductPoints:String(r.points.deductPoints),deductYuan:yuan(r.points.deductFen),maxPercent:String(r.points.maxPercent),validDays:String(r.points.validDays),refundValidDays:String(r.points.refundValidDays),reason:''};saved.value=JSON.stringify(form.value)}
function restore(p:PendingRules){const b=p.body;form.value={grades:b.grades.map(g=>({id:g.id,amount:yuan(g.minimumSpendFen)})),earnYuan:yuan(b.points.earnUnitFen),earnPoints:String(b.points.earnPoints),deductPoints:String(b.points.deductPoints),deductYuan:yuan(b.points.deductFen),maxPercent:String(b.points.maxPercent),validDays:String(b.points.validDays),refundValidDays:String(b.points.refundValidDays),reason:b.reason}}
function clearPending(){pendingRules(sessionStorage,props.account.accountId,null);pending.value=null;verified.value=false}
function match(r:Rule,p:PendingRules){return r.grades.length===p.body.grades.length&&r.grades.every(g=>p.body.grades.some(v=>v.id===g.id&&v.minimumSpendFen===g.minimumSpendFen))&&Object.entries(p.body.points).every(([key,v])=>r.points[key as keyof Rule['points']]===v)}
async function load(ask=true){if(busy.value)return;const seq=++generation;if(ask&&dirty.value&&!pending.value&&!await confirmAction('刷新将放弃未保存的修改，是否继续？'))return;loading.value=true;error.value='';verified.value=false;try{const r=await api<Rule>('/member-rules');if(seq!==generation)return;rule.value=r;if(pending.value){verified.value=true;if(r.revision>pending.value.body.expectedRevision&&match(r,pending.value)){clearPending();assign(r);notice.value='当前服务端配置与上次提交一致，已读取保存结果。'}else{restore(pending.value);notice.value='已读取服务端当前修订。上次结果仍需确认，请使用原请求重试；输入已锁定。'}}else assign(r)}catch(e){if(seq===generation)error.value=e instanceof Error?e.message:'规则读取失败。'}finally{if(seq===generation)loading.value=false}}
function openConfirm(){if(storageError.value||!canManage.value||busy.value||(!pending.value&&validation.value)||(!pending.value&&!dirty.value)||pending.value&&!verified.value)return;password.value='';error.value='';confirmOpen.value=true}
async function save(){
 if(!canManage.value||storageError.value||!password.value||!rule.value||busy.value)return
 const epoch=generation,actor=props.account.accountId
 busy.value=true;error.value='';let writeAttempted=false
 try{
  if(!pending.value){const p={key:crypto.randomUUID(),body:rulesBody(form.value,rule.value.revision)};pendingRules(sessionStorage,actor,p);pending.value=p}
  const p=pending.value!
  const confirmation=await api<Confirmation>('/auth/confirm',{method:'POST',body:JSON.stringify({action:'member.rules.update',password:password.value,objectId:'member-rules',revision:p.body.expectedRevision})})
  if(epoch!==generation||actor!==props.account.accountId||!canManage.value)return
  writeAttempted=true
  const result=await api<Rule>('/member-rules',{method:'PUT',body:JSON.stringify(p.body),headers:{'Idempotency-Key':p.key,'X-Action-Confirmation':confirmation.confirmationToken}})
  if(epoch!==generation||actor!==props.account.accountId)return
  clearPending();rule.value=result;assign(result);confirmOpen.value=false;notice.value='等级与积分规则已保存。历史订单与权益保留原快照。'
 }catch(e){
  if(epoch!==generation||actor!==props.account.accountId)return
  password.value=''
  if(e instanceof ApiError&&e.status>=400&&e.status<500){
   if(writeAttempted)clearPending()
   error.value=e.message+(e.status===409?' 请读取最新修订，核对后重新编辑。':'')
   confirmOpen.value=false
  }else{
   verified.value=false;confirmOpen.value=false
   error.value=`${e instanceof Error?e.message:'保存结果未知。'} 原请求已保留，请先读取服务端状态，再使用原请求重试。`
  }
 }finally{if(epoch===generation){password.value='';busy.value=false}}
}
function beforeUnload(e:BeforeUnloadEvent){if(dirty.value||busy.value||pending.value){e.preventDefault();e.returnValue=''}}
onMounted(()=>{try{pending.value=pendingRules(sessionStorage,props.account.accountId)}catch{storageError.value='浏览器存储不可用，无法安全保留未知请求，请恢复存储后操作。'}void load(false);window.addEventListener('beforeunload',beforeUnload)})
onUnmounted(()=>{password.value='';window.removeEventListener('beforeunload',beforeUnload)})
watch(()=>`${props.account.accountId}:${props.account.permissionCodes.join(',')}`,()=>{generation++;password.value='';confirmOpen.value=false;pending.value=null;verified.value=false;busy.value=false;rule.value=null;loading.value=false;try{pending.value=pendingRules(sessionStorage,props.account.accountId)}catch{storageError.value='浏览器存储不可用，请恢复存储后操作。'}void load(false)})
onBeforeRouteLeave(async ()=>!busy.value&&(!(dirty.value||pending.value)||await confirmAction(pending.value?'上次保存结果待确认，原请求已保留。确定离开？':'规则有未保存修改，确定离开？')))
</script>
<template><section class="page-content customer-page"><header class="page-heading"><div><RouterLink v-if="account.permissionCodes.includes('member.read')" to="/members" class="text-link">返回会员列表</RouterLink><h1>等级与积分规则</h1><p>配置等级门槛、消费奖励与积分抵扣；每次修改均需授权并记录依据。</p></div><button class="secondary-button" :disabled="busy||loading" @click="load()">{{pending?'读取保存结果':'刷新规则'}}</button></header><p v-if="loading" role="status">正在读取规则…</p><p v-if="storageError" class="notice error" role="alert">{{storageError}}</p><p v-if="error" class="notice error" role="alert">{{error}}</p><p v-if="notice" class="notice" role="status">{{notice}}</p><p v-if="!canManage" class="notice">当前账号只有查看权限，修改规则需要等级与积分管理权限。</p><template v-if="rule&&!loading"><p class="customer-note">当前修订 {{rule.revision}} · 修改只作用于后续报价与订单快照；现有会员等级在新规则订单结算后激活，旧单退款沿用已激活规则。历史成交、已发积分与有效期不改写。</p><form @submit.prevent="openConfirm"><fieldset :disabled="locked||!canManage" class="customer-fieldset"><section class="panel customer-rule-section"><h2>会员等级门槛</h2><p>按累计有效消费判定；首级门槛为 0，后续门槛严格递增。</p><div v-for="(g,index) in rule.grades" :key="g.id" class="customer-grade-row"><div><strong>{{g.name}}</strong><small>{{g.code}} · 第 {{index+1}} 级</small></div><label :for="`grade-${g.id}`">最低有效消费（元）<input :id="`grade-${g.id}`" v-model="form.grades[index].amount" inputmode="decimal" maxlength="14" :readonly="index===0"></label></div></section><section class="panel customer-rule-section"><h2>消费奖励与有效期</h2><div class="customer-form-grid"><label>每消费金额（元）<input v-model="form.earnYuan" inputmode="decimal" maxlength="8"></label><label>奖励积分<input v-model="form.earnPoints" inputmode="numeric" maxlength="7"></label><label>奖励积分有效期（天）<input v-model="form.validDays" inputmode="numeric" maxlength="4"></label><label>退款返还已过期抵扣积分有效期（天）<input v-model="form.refundValidDays" inputmode="numeric" maxlength="4"></label></div><p class="customer-note">消费在全部履约完成且无进行中售后后结算。奖励扣回时，已到期扣账部分不重复扣减，尚未到期但已使用的部分可形成欠额。</p></section><section class="panel customer-rule-section"><h2>积分抵扣</h2><div class="customer-form-grid"><label>抵扣积分数量<input v-model="form.deductPoints" inputmode="numeric" maxlength="7"></label><label>可抵扣金额（元）<input v-model="form.deductYuan" inputmode="decimal" maxlength="8"></label><label>商品金额抵扣上限（%）<input v-model="form.maxPercent" inputmode="numeric" maxlength="3"></label></div><p class="customer-note">抵扣不包含运费；最终可用数量和金额由服务端按订单快照计算。</p></section><section class="panel customer-rule-section"><h2>修改依据</h2><label>说明本次调整原因<textarea v-model="form.reason" maxlength="200" rows="3" placeholder="例如调整会员等级门槛与消费积分奖励"></textarea></label></section></fieldset><div v-if="canManage" class="customer-save"><p v-if="!pending&&validation" class="customer-note">{{validation}}</p><button class="primary-button" :disabled="busy||(!pending&&(!dirty||!!validation))||(!!pending&&!verified)">{{pending?'使用原请求重新确认':'核对并保存规则'}}</button></div></form></template><el-dialog v-model="confirmOpen" title="确认修改等级与积分规则" width="480" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @closed="password=''" class="customer-confirm"><p>将修改消费等级与积分参数，请确认门槛、比例和有效期。此次依据：{{pending?.body.reason||form.reason}}</p><label>当前账号密码（二次确认）<el-input v-model="password" type="password" show-password autocomplete="current-password" :disabled="busy" @keyup.enter="save" /></label><template #footer><el-button :disabled="busy" @click="confirmOpen=false">返回核对</el-button><el-button type="primary" :loading="busy" :disabled="!password" @click="save">授权保存</el-button></template></el-dialog></section></template>
