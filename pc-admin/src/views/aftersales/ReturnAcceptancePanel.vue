<script setup lang="ts">
import { confirmAction } from '../../shared/confirm'

import { computed, ref, onUnmounted } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { api, confirmedWrite, ApiError, type Account } from '../../api'
import { acceptanceError, acceptanceBody, pendingAcceptance, type PendingAcceptance, type AcceptanceForm } from './returns.mjs'
import { money, type AfterSaleCase } from './types'
import {isPointsOrder} from '../../shared/order-settlement.mjs'
const props = defineProps<{ item:AfterSaleCase; account:Account }>()
const emit = defineEmits<{ refresh:[]; busy:[value:boolean] }>()
const form = ref<AcceptanceForm>({ mode:'RECEIVED', receivedQuantity:0, salableQuantity:0, refundQuantity:0, amount:'', reason:'' })
const preview = ref<{ refundAmountFen:number; maxRefundAmountFen:number;refundPoints?:number } | null>(null)
const error = ref(''); const busy = ref(false); const password = ref(''); const dialog = ref(false); const unknown = ref(false)
const frozen = ref<PendingAcceptance | null>(null)
try { if(props.item.returnAcceptance || props.item.status !== 'WAITING_RETURN') pendingAcceptance(sessionStorage,props.account.accountId,props.item.caseId,null); else frozen.value=pendingAcceptance(sessionStorage,props.account.accountId,props.item.caseId) } catch { error.value='无法读取本机待核实记录，请查询处理状态后再操作。' }
if(frozen.value) { const b=frozen.value.body;form.value={mode:String(b.mode),receivedQuantity:Number(b.receivedQuantity),salableQuantity:Number(b.salableQuantity),refundQuantity:Number(b.refundQuantity),amount:b.refundAmountFen == null?'':(Number(b.refundAmountFen)/100).toFixed(2),reason:String(b.reason)} }
const requestKey = ref(frozen.value?.key || ''); const previewBody = ref(''); let active = true
const validation = computed(()=>acceptanceError(form.value,props.item.quantity,props.item.amountFen===0))
const permitted = computed(()=>props.account.permissionCodes.includes('aftersale.return.accept'))
const body = computed(()=>frozen.value?.body || acceptanceBody(form.value,props.item.revision))
const validPreview = computed(()=>Boolean(preview.value && previewBody.value === JSON.stringify(body.value)))
const dirty = computed(()=>Boolean(form.value.reason || form.value.amount || form.value.receivedQuantity || form.value.salableQuantity || form.value.refundQuantity || preview.value || frozen.value || unknown.value))
function setBusy(value:boolean) { busy.value=value; emit('busy',value) }
async function calculate() {
 if(busy.value || validation.value || unknown.value) return
 setBusy(true); error.value=''; preview.value=null
 const submitted=JSON.stringify(body.value)
 try { const result=await api<{refundAmountFen:number;maxRefundAmountFen:number;refundPoints?:number}>(`/aftersales/${props.item.caseId}/return-acceptance/preview`,{method:'POST',body:submitted}); if(active) { preview.value=result; previewBody.value=submitted } }
 catch(reason) { if(active) { error.value=reason instanceof Error?reason.message:'预览失败，请重试。'; if(reason instanceof ApiError && reason.status<500) { pendingAcceptance(sessionStorage,props.account.accountId,props.item.caseId,null); frozen.value=null;requestKey.value='' } } }
 finally { if(active) setBusy(false) }
}
async function confirm() {
 if(busy.value || !validPreview.value || !password.value || unknown.value) return
 setBusy(true); error.value=''; requestKey.value ||= crypto.randomUUID()
try { frozen.value=pendingAcceptance(sessionStorage,props.account.accountId,props.item.caseId,{key:requestKey.value,body:body.value}) } catch {error.value='无法保存本机防重复记录，请检查浏览器存储后重试。';setBusy(false);return}
 try {
  await confirmedWrite('aftersale.return.accept',password.value,`/aftersales/${props.item.caseId}/return-acceptance`,'POST',body.value,props.item.caseId,props.item.revision,{'Idempotency-Key':requestKey.value})
  if(active) { pendingAcceptance(sessionStorage,props.account.accountId,props.item.caseId,null);frozen.value=null;form.value={...form.value,reason:''}; preview.value=null; dialog.value=false; setBusy(false); emit('refresh') }
 } catch(reason) { if(active) { unknown.value=!(reason instanceof ApiError && reason.status<500); error.value=reason instanceof Error?reason.message:'处理结果未知。'; if(unknown.value)dialog.value=false;else { pendingAcceptance(sessionStorage,props.account.accountId,props.item.caseId,null);frozen.value=null;requestKey.value='' } } }
 finally { if(active) { password.value='';setBusy(false) } }
}
async function leave() { return !busy.value && (!dirty.value || await confirmAction('验收尚未提交或结果待核实，确定离开并稍后查询？')) }
onBeforeRouteLeave(leave)
onBeforeRouteUpdate(leave)
function beforeUnload(event:BeforeUnloadEvent) {if(dirty.value || busy.value){event.preventDefault();event.returnValue=''}}
window.addEventListener('beforeunload',beforeUnload)
onUnmounted(()=>{active=false;password.value='';window.removeEventListener('beforeunload',beforeUnload)})
</script>
<template>
<section class="panel order-section">
 <h2>退货物流与验收</h2>
 <p v-if="item.returnShipment">寄回：{{ item.returnShipment.carrierName }} · {{ item.returnShipment.trackingNo }}</p><p v-else>顾客尚未登记退货物流，请核实实际收货情况。</p>
 <template v-if="item.returnAcceptance"><dl class="order-facts"><div><dt>处理方式</dt><dd>{{ item.returnAcceptance.mode === 'WAIVED_RETURN' ? '批准免寄回' : '已收货验收' }}</dd></div><div><dt>收到 / 可售 / 不可售数量</dt><dd>{{ item.returnAcceptance.receivedQuantity }} / {{ item.returnAcceptance.salableQuantity }} / {{ item.returnAcceptance.damagedQuantity }}</dd></div><div><dt>批准退款数量</dt><dd>{{ item.returnAcceptance.refundQuantity }}</dd></div><div><dt>{{isPointsOrder(item)?'批准退回积分':'批准商品退款金额'}}</dt><dd>{{isPointsOrder(item)?`${item.returnAcceptance.refundPoints??'待核查'} 积分`:money(item.returnAcceptance.refundAmountFen)}}</dd></div></dl><p class="order-instructions">{{ item.returnAcceptance.reason }}</p><p class="order-note">可售数量已按验收事实回库，退款确认不会再次回库。</p></template>
 <template v-else-if="item.status === 'WAITING_RETURN' && permitted">
 <p>验收一次形成最终结论。可售数量在保存验收时回库，不可售数量只记录处置；正金额退款随后按原支付渠道处理；零现金售后另行确认数量与权益。</p>
 <form class="order-form" @submit.prevent="calculate"><label>处理方式<select v-model="form.mode" :disabled="busy || unknown || Boolean(frozen)"><option value="RECEIVED">收到退货并验收</option><option value="WAIVED_RETURN">批准免寄回，不回库</option></select></label><label>实际收到数量<input v-model.number="form.receivedQuantity" type="number" min="0" :max="item.quantity" :disabled="busy || unknown || Boolean(frozen)"></label><label>可重新销售数量<input v-model.number="form.salableQuantity" type="number" min="0" :max="form.receivedQuantity" :disabled="busy || unknown || Boolean(frozen)"></label><label>批准退款数量<input v-model.number="form.refundQuantity" type="number" min="0" :max="item.quantity" :disabled="busy || unknown || Boolean(frozen)"></label><label v-if="!isPointsOrder(item)">批准退款金额（元）<input v-model="form.amount" inputmode="decimal" placeholder="留空按成交快照计算" :disabled="busy || unknown || Boolean(frozen)"></label><label class="wide">验收与处置依据<textarea v-model="form.reason" maxlength="500" rows="3" :disabled="busy || unknown || Boolean(frozen)" placeholder="说明不可售处置、部分批准或免寄回理由，顾客可见"></textarea></label><p class="wide order-note">{{ validation }}</p><div class="wide"><button class="secondary-button" :disabled="busy || unknown || Boolean(validation)">{{isPointsOrder(item)?'预览退回积分':'预览批准金额'}}</button></div></form>
 <p v-if="frozen && !unknown" class="order-note">已查询最新状态。上次请求内容已保留，请预览并沿同一请求重试。</p>
 <p v-if="preview && validPreview">{{isPointsOrder(item)?`待确认验收预览：${preview.refundPoints??'待核查'} 积分`:`服务端批准金额：${money(preview.refundAmountFen)}`}}<button class="primary-button" :disabled="busy || unknown" @click="dialog=true">核对验收结论，授权保存</button></p>
 </template><p v-else-if="item.status === 'WAITING_RETURN'">当前账号没有退货验收权限。</p>
 <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="unknown" class="order-warning">提交结果未知，请先刷新处理状态；不要重复登记验收。</p>
 <el-dialog v-model="dialog" title="授权保存退货验收" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @closed="password=''">
 <p>{{ item.orderNo }} · {{ item.title }}</p><p>收到 {{ form.receivedQuantity }} 件，可售 {{ form.salableQuantity }} 件；拟批准退款 {{ form.refundQuantity }} 件，{{isPointsOrder(item)?`${preview?.refundPoints??'待核查'} 积分`:money(preview?.refundAmountFen || 0)}}。</p><p>{{ form.reason }}</p><p>保存后可售数量立即回库，退款或零现金权益处理尚未完成。</p><label>当前账号密码<input v-model="password" type="password" autocomplete="current-password" :disabled="busy"></label><p v-if="error" role="alert">{{ error }}</p><template #footer><button class="secondary-button" :disabled="busy" @click="dialog=false">返回核对</button><button class="primary-button" :disabled="busy || !password || !validPreview" @click="confirm">{{ busy?'保存中…':'授权保存验收结论' }}</button></template>
 </el-dialog>
</section>
</template>
