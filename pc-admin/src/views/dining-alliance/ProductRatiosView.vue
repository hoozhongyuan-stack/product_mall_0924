<script setup lang="ts">
import {ElButton,ElDialog,ElSelect,ElOption,ElInput,ElInputNumber,ElTable,ElTableColumn,ElPagination,vLoading} from 'element-plus'
import {computed,ref,watch} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import {confirmAction} from '../../shared/confirm'
import {api,type Account} from '../../api'
import {base,reasonText,type ProductRatio,type PageResult} from './types'
import './dining.css'
const props=defineProps<{account:Account}>()
const canManage=computed(()=>props.account.permissionCodes.includes('dining.manage'))
const items=ref<ProductRatio[]>([]),selected=ref<ProductRatio[]>([]),page=ref(1),total=ref(0),loading=ref(false),busy=ref(false),error=ref(''),search=ref(''),notice=ref('')
const categories=ref<{id:string;name:string;parentId:string|null}[]>([]),categoryId=ref(''),categoryError=ref('')
const configured=ref('')
const batchOpen=ref(false),batchRatio=ref<number|null>(null),batchItems=ref<ProductRatio[]>([])
const dirty=computed(()=>batchOpen.value&&batchRatio.value!==null)
let generation=0,categoryGeneration=0
onBeforeRouteLeave(async()=>!busy.value&&(!dirty.value||await confirmAction('商品佣金比例尚未保存，确定离开吗？')))
async function loadCategories(){const current=++categoryGeneration;categoryError.value='';categories.value=[];try{const result=await api<{items:{id:string;name:string;parentId:string|null}[]}>(`${base}/categories`);if(current===categoryGeneration)categories.value=result.items}catch(e){if(current===categoryGeneration)categoryError.value=reasonText(e)}}
async function reload(nextPage=page.value){if(busy.value)return;if(dirty.value&&!await confirmAction('切换查询将丢弃未保存的商品佣金比例，确定继续吗？'))return;batchOpen.value=false;page.value=nextPage;await load()}
async function load(){const current=++generation;loading.value=true;error.value='';items.value=[];selected.value=[];try{const result=await api<PageResult<ProductRatio>>(`${base}/product-ratios?${new URLSearchParams({page:String(page.value),pageSize:'20',search:search.value.trim(),...(categoryId.value?{categoryId:categoryId.value}:{}),...(configured.value?{configured:configured.value}:{})})}`);if(current===generation){items.value=result.items;total.value=result.total}}catch(e){if(current===generation)error.value=reasonText(e)}finally{if(current===generation)loading.value=false}}
function openBatch(rows=selected.value){if(busy.value||loading.value||!canManage.value||!rows.length)return;batchItems.value=rows.map(r=>({...r}));batchRatio.value=rows.length===1&&rows[0]?.commissionBps!==null?rows[0]!.commissionBps/100:null;batchOpen.value=true;error.value=''}
async function closeBatch(done?:()=>void){if(busy.value)return;if(dirty.value&&!await confirmAction('佣金比例尚未保存，确定关闭吗？'))return;batchOpen.value=false;done?.()}
async function saveBatch(){if(busy.value||!canManage.value||!batchItems.value.length)return;if(batchRatio.value===null||!Number.isFinite(batchRatio.value)||batchRatio.value<0||batchRatio.value>100){error.value='佣金比例必须在0%至100%之间。';return}busy.value=true;error.value='';const current=generation,owner=props.account.accountId;try{await api(`${base}/product-ratios/batch`,{method:'POST',body:JSON.stringify({items:batchItems.value.map(i=>({productId:i.productId,expectedRevision:i.revision})),commissionBps:Math.round(batchRatio.value*100)})});if(current!==generation)return;batchOpen.value=false;await load();notice.value='商品佣金比例已保存，对所有餐饮店统一生效。'}catch(e){if(current===generation)error.value=`${reasonText(e)}；已保留所选商品和比例输入，修订冲突时请记录输入后重新读取。`}finally{if(owner===props.account.accountId)busy.value=false}}
watch(()=>[props.account.accountId,props.account.permissionCodes.join(',')],()=>{busy.value=false;batchOpen.value=false;batchItems.value=[];configured.value='';categoryId.value='';search.value='';page.value=1;loadCategories();load()},{immediate:true})
</script>
<template>
<section class="page-content dining-page">
 <header class="page-heading"><div><h1>商品佣金比例</h1><p>同一商品对所有餐饮店统一生效，历史订单保留成交时的比例。</p></div></header>
 <section class="panel">
  <form class="toolbar" @submit.prevent="reload(1)">
   <el-input v-model="search" aria-label="商品搜索" placeholder="搜索商品名称／编码" style="width:280px" :disabled="busy"/>
   <el-select v-model="categoryId" aria-label="商品分类" placeholder="全部分类" clearable :disabled="busy" style="width:220px"><el-option label="全部分类" value=""/><el-option v-for="category in categories" :key="category.id" :label="category.name" :value="category.id"/></el-select>
   <el-select v-model="configured" aria-label="佣金配置状态" placeholder="全部配置状态" clearable :disabled="busy" style="width:180px"><el-option label="全部配置状态" value=""/><el-option label="已配置" value="true"/><el-option label="未配置" value="false"/></el-select>
   <el-button native-type="submit" :disabled="busy" :loading="loading">查询</el-button>
  </form>
  <p v-if="categoryError" role="alert" class="error">分类读取失败：{{categoryError}} <el-button @click="loadCategories">重试分类</el-button></p>
  <div v-if="canManage" class="toolbar"><span>已选 {{selected.length}} 个商品（仅当前页）</span><el-button type="primary" :disabled="!selected.length||busy||loading" @click="openBatch()">批量设置佣金比例</el-button></div>
  <p v-if="error&&!batchOpen" role="alert" class="error">{{error}} <el-button :disabled="busy" @click="reload()">重新读取</el-button></p><p v-if="notice" role="status">{{notice}}</p>
  <el-table v-loading="loading" :data="items" empty-text="暂无商品" @selection-change="selected=$event">
   <el-table-column v-if="canManage" type="selection" width="50" :selectable="()=>!busy&&!loading"/>
   <el-table-column label="商品" min-width="220"><template #default="{row}">{{row.name}}<p class="muted">{{row.productNo}}</p></template></el-table-column>
   <el-table-column label="分类" min-width="140"><template #default="{row}">{{categories.find(c=>c.id===row.categoryId)?.name||'未分类'}}</template></el-table-column>
   <el-table-column label="佣金比例" min-width="140"><template #default="{row}">{{row.commissionBps===null?'—':row.commissionBps/100+'%'}}</template></el-table-column>
   <el-table-column label="配置状态" min-width="100"><template #default="{row}">{{row.commissionBps===null?'未配置':'已配置'}}</template></el-table-column>
   <el-table-column v-if="canManage" label="操作" width="110"><template #default="{row}"><el-button link :disabled="busy||loading" @click="openBatch([row as ProductRatio])">设置比例</el-button></template></el-table-column>
  </el-table>
  <el-pagination :current-page="page" :total="total" :page-size="20" layout="prev,pager,next,total" @current-change="reload($event)"/>
 </section>
 <p class="muted">佣金＝（商品实付金额－履约前置仓进货单价×数量）×餐饮佣金比例。进货成本由前置仓统一维护。</p>
 <el-dialog v-model="batchOpen" :title="batchItems.length>1?'批量设置佣金比例':'设置佣金比例'" width="min(560px,94vw)" :before-close="closeBatch" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy">
  <p>已选 {{batchItems.length}} 个商品。新比例对所有餐饮店统一生效。</p><ul><li v-for="item in batchItems" :key="item.productId">{{item.name}} · 原比例：{{item.commissionBps===null?'未配置':item.commissionBps/100+'%'}}</li></ul>
  <label>新佣金比例（%）<el-input-number v-model="batchRatio" aria-label="批量佣金比例" :min="0" :max="100" :precision="2" :step="0.01" :disabled="busy"/></label>
  <p class="muted">覆盖所选商品原比例，只影响后续新订单，历史订单快照保持不变。</p><p v-if="error" role="alert" class="error">{{error}}</p>
  <template #footer><el-button :disabled="busy" @click="closeBatch()">取消</el-button><el-button type="primary" :disabled="batchRatio===null||busy" :loading="busy" @click="saveBatch">确认设置</el-button></template>
 </el-dialog>
</section>
</template>
