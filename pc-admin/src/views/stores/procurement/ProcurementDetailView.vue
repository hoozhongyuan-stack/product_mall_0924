<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { ElDialog } from 'element-plus'
import { api, type Account } from '../../../api'
import { confirmAction } from '../../../shared/confirm'
import { amount } from '../types'
import { actionBody, actionLabels, recordLabels, actionPermission, availableActions, proofUrl, statuses } from './domain.mjs'
import { useProcurementOperation } from './operation'
import type { ProcurementOrder } from './types'
import '../stores.css'
import './procurement.css'
const props=defineProps<{account:Account;recordId?:string;embedded?:boolean}>(), route=useRoute()
const id=computed(()=>props.recordId??String(route.params.orderId || ''))
const order=ref<ProcurementOrder|null>(null), loading=ref(false), error=ref(''), action=ref(''), fresh=ref(false)
const initial=()=>({freight:'0.00',carrier:'',tracking:'',reason:'',paymentReference:'',reference:'',note:'',refundConfirmed:false})
const form=reactive(initial())
let epoch=0, session=0
const permitted=(name:string)=>props.account.permissionCodes.includes(name)
const canRead=computed(()=>permitted('stores.procurement.read'))
const dirty=computed(()=>Boolean(action.value) && JSON.stringify(form)!==baseline)
let baseline=JSON.stringify(form)
const op=useProcurementOperation(()=>props.account,()=>id.value,async result=>{
 order.value=result as ProcurementOrder; fresh.value=true; action.value='';session++;Object.assign(form,initial())
},()=>dirty.value)
const locked=computed(()=>op.busy.value || Boolean(op.pending.value))
const actions=computed(()=>order.value ? availableActions(order.value).filter((a:string)=>permitted(actionPermission(a))) : [])
async function load() {
 const seq=++epoch;loading.value=true;error.value='';fresh.value=false
 if(!canRead.value){loading.value=false;order.value=null;return}
 try {const result=await api<ProcurementOrder>(`/stores/procurement/orders/${id.value}`);if(seq===epoch){order.value=result;fresh.value=true}}
 catch(reason){if(seq===epoch)error.value=reason instanceof Error?reason.message:'进货订单读取失败。'}
 finally{if(seq===epoch)loading.value=false}
}
function open(name:string) {
 if(!fresh.value || loading.value || locked.value || !actions.value.includes(name))return
 session++;action.value=name;Object.assign(form,initial(),{freight:((order.value?.freightFen || 0)/100).toFixed(2)});baseline=JSON.stringify(form);error.value=''
}
async function close() {
 if(locked.value)return false
 const seq=session
 if(dirty.value && !await confirmAction('有未保存修改，确定放弃？',{title:'关闭操作',confirmButtonText:'放弃修改',cancelButtonText:'继续编辑'}))return false
 if(seq!==session || locked.value)return false
 action.value='';session++;return true
}
defineExpose({canClose:close})
async function beforeClose(done:()=>void){if(await close())done()}
async function submit() {
 if(!order.value || !fresh.value || loading.value || locked.value)return
 const captured=session, name=action.value, revision=order.value.revision
 if(!actions.value.includes(name))return
 const parsed=actionBody(name,form,revision)
 if(parsed.error){error.value=parsed.error;return}
 if(!await confirmAction(`${actionLabels[name]}：订单 ${order.value.orderNo}，应付 ${amount(order.value.payableFen)}。请核对后继续。`,{title:'确认进货操作',confirmButtonText:'确认操作',cancelButtonText:'返回核对'}))return
 if(captured!==session || !fresh.value || !actions.value.includes(name))return
 await op.write(`/stores/procurement/orders/${id.value}/${name}`,'POST',parsed.body, ['confirm-payment','close-cancel'].includes(name)?`stores.procurement.${name}`:'',actionPermission(name))
 if(op.error.value && !op.pending.value)fresh.value=false
}
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes,id.value]),()=>{
 session++;action.value='';Object.assign(form,initial());order.value=null;load()
},{immediate:true,flush:'sync'})
onUnmounted(()=>{epoch++;session++})
</script>
<template>
 <div class="stores-page procurement-page"><RouterLink v-if="!embedded" to="/stores/procurement">‹ 返回进货订单</RouterLink>
 <p v-if="error" class="error" role="alert">{{error}}</p><p v-if="op.error.value" class="error" role="alert">{{op.error.value}}</p><p v-if="op.storageError.value" class="error" role="alert">{{op.storageError.value}}</p><p v-if="op.notice.value" role="status">{{op.notice.value}}</p>
 <button :disabled="loading || op.busy.value" @click="load">重新读取订单</button><p v-if="loading" role="status">正在读取订单…</p>
 <section v-if="op.pending.value" class="panel store-section"><h3>上次操作结果待确认</h3><p>原请求已保留，请先读取结果。未完成时使用原请求重试。</p><button :disabled="op.busy.value" @click="op.recover">读取操作结果</button><template v-if="op.verified.value"><button :disabled="op.busy.value" @click="op.write(op.pending.value.path,op.pending.value.method,op.pending.value.body,op.pending.value.action,actionPermission(op.pending.value.path.split('/').at(-1)))">使用原请求重试</button></template></section>
 <template v-if="order">
 <section class="panel store-section"><div class="store-toolbar"><h2>{{order.orderNo}}</h2><strong>{{statuses[order.status] || order.status}}</strong></div><p>{{order.storeName}} · {{new Date(order.createdAt).toLocaleString('zh-CN')}}</p><div class="store-toolbar"><span>商品 {{amount(order.goodsAmountFen)}}</span><span>运费 {{amount(order.freightFen)}}</span><strong>应付 {{amount(order.payableFen)}}</strong></div><p v-if="order.unitReviewRequired" class="error">规格单位发生变化，请按订单冻结单位人工复核后再继续。</p><div class="store-toolbar"><button v-for="name in actions" :key="name" :disabled="!fresh || loading || locked" @click="open(name)">{{actionLabels[name]}}</button></div></section>
 <section class="panel store-section"><h3>商品与冻结单位</h3><div class="table-wrap" tabindex="0" aria-label="进货商品列表，可横向滚动"><table><thead><tr><th>商品／规格</th><th>销售单位</th><th>数量</th><th>进货单价</th><th>换算</th></tr></thead><tbody><tr v-for="line in order.lines" :key="line.id"><td>{{line.productName}}<small>{{line.specLabel}}</small></td><td>{{line.saleUnit}}</td><td>{{line.quantity}}</td><td>{{amount(line.purchaseCostFen)}}</td><td>1 {{line.saleUnit}} = {{line.conversionRatio}} {{line.baseUnit}}</td></tr></tbody></table></div></section>
 <section class="panel store-section"><h3>付款与物流</h3><p class="instructions">{{order.paymentInstructions || '审单后提供采购收款说明。'}}</p><a v-if="proofUrl(order.paymentProof)" :href="proofUrl(order.paymentProof)" target="_blank" rel="noopener"><img class="proof" :src="proofUrl(order.paymentProof)" alt="本订单付款凭证"></a><p v-else>暂无付款凭证。</p><p v-if="order.shipment">{{order.shipment.carrier}} · {{order.shipment.tracking}}</p></section>
 <section class="panel store-section"><h3>操作记录</h3><p v-if="!order.records.length">暂无操作记录。</p><article v-for="record in order.records" :key="record.id"><strong>{{recordLabels[record.kind] || '单据处理'}} · {{record.actorName}}</strong><p>{{record.note}} {{record.reference}}</p><small>{{new Date(record.createdAt).toLocaleString('zh-CN')}}</small><a v-if="proofUrl({assetId:record.proofAssetId,previewUrl:record.proofPreviewUrl})" :href="proofUrl({assetId:record.proofAssetId,previewUrl:record.proofPreviewUrl})" target="_blank" rel="noopener">查看历史付款凭证</a></article></section>
 </template>
 <ElDialog class="procurement-dialog" :model-value="Boolean(action)" :title="actionLabels[action] || '进货操作'" width="560px" :before-close="beforeClose" :close-on-click-modal="false" :close-on-press-escape="!locked" :show-close="!locked" destroy-on-close>
 <p v-if="order">{{order.orderNo}} · {{order.storeName}} · 应付 {{amount(order.payableFen)}}</p><p v-if="error || op.error.value" class="error" role="alert">{{error || op.error.value}}</p>
 <section v-if="op.pending.value"><p>原请求已保留，请先核查结果。</p><button :disabled="op.busy.value" @click="op.recover">读取操作结果</button><template v-if="op.verified.value"><button :disabled="op.busy.value" @click="op.write(op.pending.value.path,op.pending.value.method,op.pending.value.body,op.pending.value.action,actionPermission(op.pending.value.path.split('/').at(-1)))">使用原请求重试</button></template></section>
 <fieldset :disabled="locked"><label v-if="action==='review'">运费（元）<input v-model="form.freight" inputmode="decimal"></label><template v-if="action==='ship'"><label>物流公司<input v-model="form.carrier" maxlength="80"></label><label>物流单号<input v-model="form.tracking" maxlength="120"></label></template><label v-if="['reject','reject-proof'].includes(action)">处理原因<textarea v-model="form.reason" maxlength="300"></textarea></label><template v-if="['confirm-payment','close-cancel','resolve-exception','verify-units'].includes(action)"><label>{{action==='confirm-payment'?'到账凭证编号':'处理／退款凭证编号'}}<input v-if="action==='confirm-payment'" v-model="form.paymentReference" maxlength="120"><input v-else v-model="form.reference" maxlength="120"></label><label>处理备注<textarea v-model="form.note" maxlength="300"></textarea></label><label v-if="action==='close-cancel'"><input v-model="form.refundConfirmed" type="checkbox">已完成线下退款，确认关闭订单</label></template></fieldset>
 <template #footer><button :disabled="locked" @click="close()">取消</button><button :disabled="locked || !fresh" @click="submit">{{op.busy.value?'正在处理…':'确认提交'}}</button></template>
 </ElDialog>
 </div>
</template>
<style scoped>label{display:grid;gap:6px;margin:14px 0}input,textarea{padding:10px;border:1px solid #d7dce0;border-radius:6px;width:100%;box-sizing:border-box}input[type=checkbox]{width:auto}fieldset{border:0;padding:0}.proof{max-width:200px;max-height:240px;object-fit:contain}.instructions{white-space:pre-wrap}small{display:block;color:#737f89}article{padding:14px 0;border-bottom:1px solid #eee}:deep(.el-dialog){max-width:calc(100vw - 24px)}</style>
