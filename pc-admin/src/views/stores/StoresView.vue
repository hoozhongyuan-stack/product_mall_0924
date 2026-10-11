<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {ElDialog} from 'element-plus'
import {api,type Account} from '../../api'
import {modeLabel,type Store} from './types'
import StoreDetailView from './StoreDetailView.vue'
import AdminListPager from '../../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../../shared/list/AdminSelectedRowsDialog.vue'
import {useCurrentPageSelection} from '../../shared/list/useCurrentPageSelection'
import './stores.css'
const props=defineProps<{account:Account}>(),canManage=computed(()=>props.account.permissionCodes.includes('stores.manage'))
const loading=ref(false),error=ref(''),search=ref(''),enabled=ref(''),page=ref(1),pageSize=ref(20),total=ref(0),items=ref<Store[]>([])
const selection=useCurrentPageSelection(()=>items.value,r=>r.id),comparison=ref(false),editorId=ref(''),editor=ref<{canClose:()=>Promise<boolean>}|null>(null)
const comparisonRows=computed(()=>selection.selectedRows.value.map(r=>({name:r.name,address:r.address,contact:r.contactName,phone:r.contactPhone,modes:r.supportedModes.map(modeLabel).join(' / '),status:!r.enabled?'已停用':r.acceptingOrders?'接单中':'暂停接单'})))
let generation=0
async function closeEditor(done?:()=>void){if(editor.value&&!await editor.value.canClose())return;editorId.value='';done?.()}
async function load(reset=false){if(reset)page.value=1;const current=++generation;loading.value=true;items.value=[];comparison.value=false;error.value=''
try{const data=await api<{items:Store[];total:number}>(`/stores?${new URLSearchParams({search:search.value.trim(),...(enabled.value?{enabled:enabled.value}:{}),page:String(page.value),pageSize:String(pageSize.value)})}`);if(current===generation){items.value=data.items;total.value=data.total}}catch(e){if(current===generation)error.value=e instanceof Error?e.message:'读取前置仓失败，请重试。'}finally{if(current===generation)loading.value=false}}
watch(()=>JSON.stringify([props.account.accountId,props.account.permissionCodes]),()=>{editorId.value='';search.value='';enabled.value='';load(true)},{immediate:true})
</script>
<template><section class="page-content stores-page admin-list-page">
<header class="page-heading"><div><h1>前置仓管理</h1><p>统一维护前置仓资料、服务范围与人员授权。订单及售后在订单模块处理。</p></div><button v-if="canManage" class="primary-button" @click="editorId='new'">新增前置仓</button></header>
<form class="store-search" @submit.prevent="load(true)"><label>名称 / 联系人 / 电话<input v-model="search" type="search" maxlength="100" placeholder="搜索前置仓名称或联系人"></label><label>状态<select v-model="enabled"><option value="">全部状态</option><option value="true">启用</option><option value="false">停用</option></select></label><button class="primary-button" :disabled="loading">查询前置仓</button><button type="button" class="secondary-button" :disabled="loading" @click="search='';enabled='';load(true)">重置</button></form>
<p v-if="loading" role="status">正在读取前置仓…</p><p v-else-if="error" role="alert">{{error}} <button class="text-button" @click="load()">重新加载</button></p>
<AdminSelectionBar :count="selection.selectedRows.value.length" unit="家" :loading="loading" @clear="selection.clear"><button class="secondary-button" :disabled="!selection.selectedRows.value.length" @click="comparison=true">对比所选前置仓</button></AdminSelectionBar>
<div class="panel table-wrap admin-list-table" tabindex="0" aria-label="前置仓列表，可横向滚动"><table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部前置仓" :checked="selection.allSelected.value" :indeterminate="selection.indeterminate.value" :disabled="loading" @change="selection.togglePage(($event.target as HTMLInputElement).checked)"></th><th>前置仓</th><th>联系人</th><th>营业时间</th><th>服务方式</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="store in items" :key="store.id"><td><input type="checkbox" :aria-label="`选择 ${store.name}`" :checked="selection.selectedIds.value.includes(store.id)" @change="selection.toggle(store.id,($event.target as HTMLInputElement).checked)"></td><td><strong>{{store.name}}</strong><small>{{store.address}}</small></td><td>{{store.contactName}}<small>{{store.contactPhone}}</small></td><td>{{store.openingHours||'未设置'}}</td><td>{{store.supportedModes.map(modeLabel).join(' / ')}}</td><td><AdminStatusBadge :status="!store.enabled?'DISABLED':store.acceptingOrders?'ACTIVE':'PAUSED'" :label="!store.enabled?'已停用':store.acceptingOrders?'接单中':'暂停接单'" :tone="!store.enabled?'neutral':store.acceptingOrders?'success':'pending'"/></td><td><button class="text-button" @click="editorId=store.id">{{canManage?'编辑 / 人员授权':'查看详情'}}</button></td></tr><tr v-if="!loading&&!items.length"><td colspan="7">暂无符合条件的前置仓。</td></tr></tbody></table></div>
<AdminListPager :page="page" :page-size="pageSize" :total="total" :loading="loading" @change="(p,s)=>{page=p;pageSize=s;load()}"/>
<ElDialog :model-value="!!editorId" title="前置仓资料与管理" width="min(1100px,96vw)" :before-close="closeEditor" :close-on-click-modal="false"><StoreDetailView v-if="editorId" ref="editor" :key="editorId" :record-id="editorId" :account="account" embedded @close="editorId=''" @saved="editorId='';load()"/></ElDialog>
<AdminSelectedRowsDialog v-model="comparison" title="前置仓对比" :rows="comparisonRows" :columns="[{key:'name',label:'前置仓'},{key:'address',label:'地址'},{key:'contact',label:'联系人'},{key:'phone',label:'电话'},{key:'modes',label:'服务方式'},{key:'status',label:'状态'}]"/>
</section></template>
