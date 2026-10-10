<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {ElDialog} from 'element-plus'
import {canonicalBatchItems,profitBatchTarget} from './profit-batch'
import {api,confirmedWrite,type Account} from '../../api'
import {amount} from './types'
interface Rule {skuId:string;productName:string;specKey:string;specLabel?:string;skuCode:string;saleUnit:string;purchaseCostFen:number|null;platformShareBps:number|null;revision:number;requiresReconfiguration?:boolean}
const props=defineProps<{account:Account}>()
const items=ref<Rule[]>([]),total=ref(0),page=ref(1),search=ref('')
const loading=ref(false),busy=ref(false),needsRead=ref(false),confirming=ref(false)
const error=ref(''),notice=ref(''),password=ref(''),cost=ref(''),share=ref(''),selected=ref<Rule|null>(null)
const checked=ref<string[]>([]),editing=ref<Rule[]>([]),modalOpen=ref(false)
const allChecked=computed(()=>items.value.length>0&&checked.value.length===items.value.length)
const partiallyChecked=computed(()=>checked.value.length>0&&!allChecked.value)
let epoch=0
const permitted=computed(()=>props.account.permissionCodes.includes('stores.manage'))
function hundredths(value:string,max:number){if(!/^\d+(\.\d{1,2})?$/.test(value))return null;const [whole,decimal='']=value.split('.');const result=Number(whole)*100+Number(decimal.padEnd(2,'0'));return Number.isSafeInteger(result)&&result<=max?result:null}
const costFen=computed(()=>hundredths(cost.value,9900000000)),shareBps=computed(()=>hundredths(share.value,10000))
const allowed=computed(()=>permitted.value&&editing.value.length>0&&costFen.value!==null&&shareBps.value!==null&&!busy.value&&!loading.value&&!needsRead.value)
function togglePage(value:boolean){checked.value=value?items.value.map(row=>row.skuId):[]}
function toggleRow(id:string,value:boolean){checked.value=value?[...checked.value,id]:checked.value.filter(item=>item!==id)}
function closeEditor(){if(busy.value)return;modalOpen.value=false;selected.value=null;editing.value=[];clearConfirmation()}
function editBatch(){const rows=items.value.filter(row=>checked.value.includes(row.skuId));if(!rows.length||busy.value||loading.value||needsRead.value)return;editing.value=rows.map(row=>({...row}));selected.value=null;cost.value='';share.value='';error.value='';notice.value='';clearConfirmation();modalOpen.value=true}
function clearConfirmation(){password.value='';confirming.value=false}
async function load(){if(!permitted.value||busy.value)return;const current=++epoch;loading.value=true;error.value='';clearConfirmation();selected.value=null;editing.value=[];checked.value=[];modalOpen.value=false;try{const result=await api<{items:Rule[];total:number}>(`/stores/profit-rules?search=${encodeURIComponent(search.value.trim())}&page=${page.value}&pageSize=20`);if(current===epoch){if(!Array.isArray(result.items)||!Number.isInteger(result.total))throw new Error("Unexpected result");items.value=result.items;total.value=result.total;needsRead.value=false}}catch{if(current===epoch){items.value=[];error.value='规格分润规则读取失败，请重新读取。'}}finally{if(current===epoch)loading.value=false}}
function edit(row:Rule){if(busy.value||loading.value||needsRead.value)return;selected.value={...row};editing.value=[{...row}];modalOpen.value=true;cost.value=row.requiresReconfiguration||row.purchaseCostFen==null?'':(row.purchaseCostFen/100).toFixed(2);share.value=row.requiresReconfiguration||row.platformShareBps==null?'':String(row.platformShareBps/100);error.value='';notice.value='';clearConfirmation()}
function begin(){if(allowed.value){confirming.value=true;password.value=''}}
async function save(){
 if(!allowed.value||!confirming.value||!password.value)return
 const rows=editing.value.map(row=>({...row})),single=selected.value,body={items:canonicalBatchItems(rows.map(row=>({skuId:row.skuId,expectedRevision:row.revision}))),purchaseCostFen:costFen.value!,platformShareBps:shareBps.value!},current=++epoch
 busy.value=true;error.value=''
 try{
  let saved:Rule[]
  if(single){const result=await confirmedWrite<Rule>('stores.profit.configure',password.value,`/stores/profit-rules/${encodeURIComponent(single.skuId)}`,'PUT',{expectedRevision:single.revision,purchaseCostFen:body.purchaseCostFen,platformShareBps:body.platformShareBps},single.skuId,single.revision);saved=[result]}
  else{const target=await profitBatchTarget(body);if(current!==epoch)return;const result=await confirmedWrite<{items:Rule[]}>('stores.profit.batch.configure',password.value,'/stores/profit-rules','POST',body,target,0);saved=result.items}
  if(current===epoch){
   if(!Array.isArray(saved)||saved.length!==rows.length||new Set(saved.map(row=>row.skuId)).size!==rows.length||saved.some(result=>{const original=rows.find(row=>row.skuId===result.skuId);return !original||result.revision!==original.revision+1||result.purchaseCostFen!==body.purchaseCostFen||result.platformShareBps!==body.platformShareBps}))throw new Error('Unexpected result')
   items.value=items.value.map(item=>saved.find(row=>row.skuId===item.skuId)||item);selected.value=null;editing.value=[];checked.value=[];modalOpen.value=false;notice.value=single?'规格分润规则已保存，仅影响后续订单；已有订单保持原快照。':`已保存 ${saved.length} 个规格，仅影响后续订单；已有订单保持原快照。`
  }
 }catch{if(current===epoch){error.value='保存结果未确认，请关闭弹窗后重新读取规则并核对，再操作。';needsRead.value=true}}
 finally{if(current===epoch){busy.value=false;clearConfirmation()}}
}
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes]),()=>{epoch++;items.value=[];selected.value=null;editing.value=[];checked.value=[];modalOpen.value=false;busy.value=false;loading.value=false;clearConfirmation();void load()})
onMounted(load);onUnmounted(()=>{epoch++;clearConfirmation()})
</script>
<template>
<section v-if="permitted" class="panel store-section" aria-labelledby="store-profit-title">
<h2 id="store-profit-title">商品规格进货价与分润</h2><p>按销售规格统一设置前置仓进货成本和平台分成比例。成本以每个销售单位计，不按库存基础单位计。</p>
<p>平台利润＝（订单实付－进货成本）×平台分成比例；前置仓结算＝进货成本＋前置仓利润－运费。运费由前置仓承担，金额以订单快照为准。</p>
<form class="store-search" aria-label="搜索规格分润规则" @submit.prevent="page=1;load()"><label for="store-rule-search">商品 / 规格 <input id="store-rule-search" v-model="search" type="search" maxlength="100" :disabled="busy||loading" /></label><button class="secondary-button" :disabled="busy||loading">查询</button><button type="button" class="secondary-button" :disabled="busy||loading" @click="load">重新读取规则</button></form>
<p v-if="loading" role="status">正在读取规格分润规则…</p><p v-if="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
<p v-if="!loading&&!error&&!items.length" class="empty-state">暂无符合条件的规格。请调整搜索条件，或先创建商品规格。</p>
<div class="store-actions store-rule-toolbar"><span>已选 {{checked.length}} 个规格（仅当前页）</span><button type="button" class="secondary-button" data-action="batch-rule" :disabled="!checked.length||busy||loading||needsRead||modalOpen" @click="editBatch">批量设置进货价与分润</button></div>
<div v-if="items.length" class="table-wrap" tabindex="0" aria-label="商品规格分润列表，可横向滚动"><table><thead><tr><th><input type="checkbox" data-action="select-page" aria-label="选择当前页全部规格" :checked="allChecked" :indeterminate="partiallyChecked" :disabled="busy||loading||needsRead||modalOpen" @change="togglePage(($event.target as HTMLInputElement).checked)" /></th><th>商品与规格</th><th>前置仓进货价</th><th>平台分成比例</th><th>操作</th></tr></thead><tbody><tr v-for="row in items" :key="row.skuId"><td><input type="checkbox" :aria-label="`选择 ${row.productName} ${row.specLabel||'默认规格'}`" :checked="checked.includes(row.skuId)" :disabled="busy||loading||needsRead||modalOpen" @change="toggleRow(row.skuId,($event.target as HTMLInputElement).checked)" /></td><td>{{row.productName}}<small>{{row.specLabel||'默认规格'}} · {{row.skuCode}}</small><small v-if="row.requiresReconfiguration">销售单位已变化，请重新保存进货价与分成规则。</small></td><td>{{row.requiresReconfiguration?'需重新配置':row.purchaseCostFen==null?'未配置':amount(row.purchaseCostFen)}} / 每{{row.saleUnit}}</td><td>{{row.requiresReconfiguration?'需重新配置':row.platformShareBps==null?'未配置':`${row.platformShareBps/100}%`}}</td><td><button class="text-button" data-action="edit-rule" :disabled="busy||loading||needsRead||confirming" @click="edit(row)">设置分润</button></td></tr></tbody></table></div>
<div class="store-pagination"><span>共 {{total}} 个规格</span><button class="secondary-button" :disabled="page<=1||busy||loading" @click="page--;load()">上一页</button><button class="secondary-button" :disabled="page*20>=total||busy||loading" @click="page++;load()">下一页</button></div>
<ElDialog :model-value="modalOpen" :title="selected?'设置进货价与分润':'批量设置进货价与分润'" width="min(640px, 92vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="closeEditor" @closed="clearConfirmation"><p v-if="error" role="alert">{{error}}</p>
<form class="store-form" aria-label="规格分润设置" @submit.prevent="begin"><h3 v-if="selected" class="wide">{{selected.productName}} · {{selected.specLabel||'默认规格'}}</h3><p v-else class="wide">已选 {{editing.length}} 个规格，将统一设置相同进货价与平台分成比例。进货价按各规格的销售单位计算，保存前请核对。</p><ul v-if="!selected" class="wide profit-selected-list"><li v-for="row in editing" :key="row.skuId">{{row.productName}} · {{row.specLabel||'默认规格'}}（每{{row.saleUnit}}）</li></ul><p v-if="selected?.requiresReconfiguration" class="wide" role="alert">销售单位已变化，原规则不能用于当前规格。请按当前销售单位重新填写进货价与平台分成比例。</p><label for="store-purchase-cost">前置仓进货价（元 / 每{{selected?.saleUnit||'销售单位'}}）<input id="store-purchase-cost" v-model="cost" inputmode="decimal" maxlength="16" :disabled="busy||confirming" /><small>非负金额，最多两位小数。</small></label><label for="store-platform-share">平台分成比例（%）<input id="store-platform-share" v-model="share" inputmode="decimal" maxlength="6" :disabled="busy||confirming" /><small>0–100，最多两位小数。</small></label><p v-if="costFen===null||shareBps===null" class="wide" role="status">请填写有效的进货价和平台分成比例。</p><div class="store-actions wide"><button class="primary-button" data-action="save-rule" :disabled="!allowed||confirming">保存规格分润</button><button type="button" class="secondary-button" :disabled="busy" data-action="close-rule" @click="closeEditor">取消编辑</button></div></form>
<form v-if="confirming" class="store-section" aria-label="确认保存规格分润" @submit.prevent="save"><h3>确认保存规格分润</h3><p>本次设置仅影响后续订单。请输入当前账号密码确认。</p><label for="store-profit-password">当前账号密码 <input id="store-profit-password" v-model="password" type="password" autocomplete="current-password" :disabled="busy" /></label><div class="store-actions"><button class="primary-button" :disabled="busy||!password">{{busy?'正在保存…':selected?'确认保存分润':`确认保存 ${editing.length} 个规格`}}</button><button type="button" class="secondary-button" :disabled="busy" @click="clearConfirmation">取消确认</button></div></form>
</ElDialog>
</section>
</template>

<style scoped>
.store-rule-toolbar{margin:16px 0;justify-content:space-between;flex-wrap:wrap}.profit-selected-list{max-height:160px;overflow:auto;margin:0;padding-left:20px}.profit-selected-list li{margin:4px 0}.store-form{margin-top:16px}.store-form label{min-width:0}
</style>
