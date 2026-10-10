<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {useRoute,useRouter,onBeforeRouteLeave} from 'vue-router'
import {api,type Account} from '../../api'
import {blankStore,modes,type Store} from './types'
import {confirmAction} from '../../shared/confirm'
import './stores.css'
const props=defineProps<{account:Account}>(),route=useRoute(),router=useRouter()
const isNew=computed(()=>route.params.storeId==='new'),canManage=computed(()=>props.account.permissionCodes.includes('stores.manage'))
const form=ref<Store>(blankStore()),loaded=ref(''),loading=ref(false),busy=ref(false),error=ref(''),notice=ref('')
const mapQuery=ref(''),mapBusy=ref(false),mapError=ref(''),mapItems=ref<{name:string;address:string;latitude:number;longitude:number}[]>([])
type StaffMember={id:string;memberNo:string;name:string}
const staff=ref<{id:string;memberId:string;memberNo?:string;name:string;permissions:string[];enabled:boolean;revision:number}[]>([]),staffError=ref(''),staffBusy=ref(false)
const memberQuery=ref(''),memberItems=ref<StaffMember[]>([]),selectedMember=ref<StaffMember|null>(null),memberBusy=ref(false),memberError=ref('')
let memberGeneration=0
watch(memberQuery,()=>{memberGeneration++;selectedMember.value=null;memberItems.value=[];memberError.value=''}, {flush:'sync'})
const staffPermissions=ref<string[]>(['products','orders']),staffEnabled=ref(true)
const dirty=computed(()=>loaded.value!==''&&loaded.value!==JSON.stringify(form.value))
let generation=0,staffReadGeneration=0,staffOperation=0
async function load(){const current=++generation;staffOperation++;staffReadGeneration++;staffBusy.value=false;staffError.value='';memberBusy.value=false;staffPermissions.value=['products','orders'];staffEnabled.value=true;memberQuery.value='';selectedMember.value=null;memberGeneration++;memberItems.value=[];memberError.value='';error.value='';notice.value='';loading.value=true;staff.value=[];loaded.value='';try{const result=isNew.value?blankStore():await api<Store>(`/stores/${encodeURIComponent(String(route.params.storeId))}`);if(current!==generation)return;form.value={...result,supportedModes:[...result.supportedModes]};loaded.value=JSON.stringify(result);if(!isNew.value&&canManage.value)await loadStaff()}catch(e){if(current===generation)error.value=e instanceof Error?e.message:'前置仓读取失败。'}finally{if(current===generation)loading.value=false}}
async function save(){if(busy.value||!canManage.value)return;busy.value=true;error.value='';notice.value='';try{const {id,revision,warehouseId,...fields}=form.value;const result=await api<Store>(isNew.value?'/stores':`/stores/${id}`,{method:isNew.value?'POST':'PATCH',body:JSON.stringify(isNew.value?fields:{...fields,revision})});form.value={...result,supportedModes:[...result.supportedModes]};loaded.value=JSON.stringify(result);notice.value='前置仓资料已保存。';if(isNew.value)await router.replace(`/stores/${result.id}`)}catch(e){error.value=e instanceof Error?e.message:'保存失败，请重试。'}finally{busy.value=false}}
async function searchMap(){if(!mapQuery.value.trim()||mapBusy.value)return;mapBusy.value=true;mapError.value='';mapItems.value=[];try{const data=await api<{items:typeof mapItems.value}>(`/stores/map-search?${new URLSearchParams({q:mapQuery.value.trim(),city:form.value.city})}`);mapItems.value=data.items;if(!data.items.length)mapError.value='未找到位置，请换一个关键词。'}catch(e){mapError.value=e instanceof Error?e.message:'地图查询失败，可重试或手工填写准确坐标。'}finally{mapBusy.value=false}}
function selectMap(item:typeof mapItems.value[number]){form.value={...form.value,address:item.address,latitude:item.latitude,longitude:item.longitude};mapItems.value=[]}
function isCurrentStore(current:number,storeId:string){return current===generation&&form.value.id===storeId&&String(route.params.storeId)===storeId}
async function loadStaff(){
 const current=generation,storeId=String(route.params.storeId),read=++staffReadGeneration
 staffError.value=''
 try{const result=await api<{items:typeof staff.value}>(`/stores/${storeId}/staff`)
 if(isCurrentStore(current,storeId)&&read===staffReadGeneration)staff.value=result.items
 }catch(e){if(isCurrentStore(current,storeId)&&read===staffReadGeneration)staffError.value=e instanceof Error?e.message:'人员授权读取失败。'}
}
async function searchMembers(){
 if(memberBusy.value||memberQuery.value.trim().length<2)return
 const current=++memberGeneration,currentPage=generation,query=memberQuery.value.trim(),storeId=form.value.id
 memberBusy.value=true;memberError.value='';memberItems.value=[];selectedMember.value=null
 try{const result=await api<{members:StaffMember[]}>(`/stores/${storeId}/staff?${new URLSearchParams({memberSearch:query})}`)
 if(current!==memberGeneration||form.value.id!==storeId)return
 memberItems.value=result.members
 if(!result.members.length)memberError.value='未找到已启用的会员，请核对会员编号或昵称。'
 }catch(e){if(current===memberGeneration)memberError.value=e instanceof Error?e.message:'会员查询失败，请重试。'}finally{if(currentPage===generation)memberBusy.value=false}
}
function selectMember(item:StaffMember){selectedMember.value={...item};memberItems.value=[];memberError.value=''}
async function assignStaff(){
 if(staffBusy.value||!selectedMember.value)return
 const current=generation,storeId=form.value.id,operation=++staffOperation
 staffBusy.value=true;staffError.value=''
 try{await api(`/stores/${storeId}/staff`,{method:'POST',body:JSON.stringify({memberId:selectedMember.value.id,permissions:[...staffPermissions.value],enabled:staffEnabled.value})})
 if(!isCurrentStore(current,storeId)||operation!==staffOperation)return
 memberQuery.value='';selectedMember.value=null;await loadStaff()
 if(isCurrentStore(current,storeId)&&operation===staffOperation)notice.value='前置仓人员授权已保存。'
 }catch(e){if(isCurrentStore(current,storeId)&&operation===staffOperation)staffError.value=e instanceof Error?e.message:'人员授权保存失败。'}finally{if(isCurrentStore(current,storeId)&&operation===staffOperation)staffBusy.value=false}
}
function editStaff(item:typeof staff.value[number]){memberQuery.value=item.memberNo||item.memberId;selectedMember.value={id:item.memberId,memberNo:item.memberNo||item.memberId,name:item.name};staffPermissions.value=[...item.permissions];staffEnabled.value=item.enabled}
onBeforeRouteLeave(async()=>!dirty.value||await confirmAction('尚有未保存的前置仓资料，确定离开吗？',{title:'前置仓资料未保存'}))
watch(()=>route.params.storeId,load,{immediate:true})
</script>
<template><section class="page-content stores-page store-editor"><header class="page-heading"><div><h1>{{isNew?'新增前置仓':form.name||'前置仓详情'}}</h1><p>前置仓商品使用平台价格与统一装修；暂停接单后，已有订单仍可履约。</p></div><RouterLink class="secondary-button" to="/stores">返回前置仓列表</RouterLink></header><p v-if="loading" role="status">正在读取前置仓…</p><p v-if="error" class="error" role="alert">{{error}} <button type="button" class="text-button" :disabled="busy" @click="load">重新读取</button></p><p v-if="notice" role="status">{{notice}}</p><template v-if="!loading&&(loaded||isNew)"><form class="panel store-section" @submit.prevent="save"><fieldset :disabled="!canManage||busy" class="store-form"><legend>前置仓资料</legend><label>前置仓名称<input v-model="form.name" required maxlength="100"></label><label>营业时间<input v-model="form.openingHours" maxlength="100" placeholder="例如 09:00–21:00"></label><label>联系人<input v-model="form.contactName" required maxlength="100"></label><label>联系电话<input v-model="form.contactPhone" required type="tel" maxlength="30"></label><label>城市<input v-model="form.city" maxlength="100"></label><label>详细地址<input v-model="form.address" required maxlength="500"></label><div class="wide"><label>高德地图定位<input v-model="mapQuery" maxlength="100" placeholder="输入前置仓名称或地址"></label><button class="secondary-button" type="button" :disabled="mapBusy||!mapQuery.trim()" @click="searchMap">{{mapBusy?'正在查询…':'搜索高德位置'}}</button><p class="help-text">地图服务需要后台配置高德 Web 服务密钥；也可填写准确经纬度。</p><p v-if="mapError" role="alert">{{mapError}}</p><ul class="store-map-results"><li v-for="(item,index) in mapItems" :key="index"><button type="button" class="text-button" @click="selectMap(item)">{{item.name}} · {{item.address}}</button></li></ul></div><label>纬度<input v-model.number="form.latitude" required type="number" step="any" min="-90" max="90"></label><label>经度<input v-model.number="form.longitude" required type="number" step="any" min="-180" max="180"></label><fieldset class="wide"><legend>交付方式</legend><label v-for="mode in modes" :key="mode.value" class="check-row"><input v-model="form.supportedModes" type="checkbox" :value="mode.value">{{mode.label}}</label></fieldset><label v-if="form.supportedModes.includes('DELIVERY')">配送半径（米）<input v-model.number="form.deliveryRadiusMeters" type="number" min="1" step="1" required></label><label v-if="form.supportedModes.includes('DELIVERY')">配送费（分）<input v-model.number="form.deliveryFeeFen" type="number" min="0" step="1" required></label><div class="wide staff-permissions"><label class="check-row"><input v-model="form.enabled" type="checkbox">启用前置仓</label><label class="check-row"><input v-model="form.acceptingOrders" type="checkbox">允许新订单</label></div></fieldset><div v-if="canManage" class="store-actions"><button class="primary-button" :disabled="busy||!form.supportedModes.length">{{busy?'保存中…':'保存前置仓'}}</button><span v-if="dirty">有未保存的修改</span></div></form><section v-if="!isNew&&canManage" class="panel store-section"><h2>前置仓人员授权</h2><p>按会员编号或昵称查找已注册会员，选择后设置权限；可更新已有人员权限或停用授权。</p><p v-if="staffError" role="alert">{{staffError}} <button type="button" class="text-button" @click="loadStaff">重新读取</button></p><div class="table-wrap"><table><thead><tr><th>人员</th><th>权限</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in staff" :key="item.id"><td>{{item.name||item.memberId}}<small v-if="item.memberNo" class="member-number">会员编号：{{item.memberNo}}</small></td><td>{{item.permissions.map(p=>({products:'商品与库存',orders:'订单与售后',accounts:'账户'}[p]||p)).join(' / ')}}</td><td>{{item.enabled?'已授权':'已停用'}}</td><td><button type="button" class="text-button" @click="editStaff(item)">编辑授权</button></td></tr></tbody></table><p v-if="!staff.length">尚未授权管理人员。</p></div><form @submit.prevent="assignStaff"><label>查找会员<input v-model="memberQuery" maxlength="100" placeholder="输入会员编号或昵称" :disabled="staffBusy" @keydown.enter.prevent="searchMembers"></label><button type="button" class="secondary-button" :disabled="staffBusy||memberBusy||memberQuery.trim().length<2" @click="searchMembers">{{memberBusy?'正在查找…':'查找会员'}}</button><p v-if="memberError" role="alert">{{memberError}}</p><ul v-if="memberItems.length" class="member-results"><li v-for="item in memberItems" :key="item.id"><span>{{item.name}} · {{item.memberNo}}</span><button type="button" class="text-button" :disabled="staffBusy" @click="selectMember(item)">选择 {{item.name}}</button></li></ul><p v-if="selectedMember" class="member-selection" role="status">已选择：{{selectedMember.name}} · 会员编号 {{selectedMember.memberNo}}</p><p v-else class="help-text">请先查找并选择会员，再保存人员授权。</p><div class="staff-permissions"><label v-for="item in [{value:'products',name:'商品与库存'},{value:'orders',name:'订单与售后'},{value:'accounts',name:'账户'}]" :key="item.value" class="check-row"><input v-model="staffPermissions" type="checkbox" :value="item.value" :disabled="staffBusy">{{item.name}}</label><label class="check-row"><input v-model="staffEnabled" type="checkbox" :disabled="staffBusy">启用授权</label></div><button class="primary-button" :disabled="staffBusy||!selectedMember">{{staffBusy?'保存中…':'保存人员授权'}}</button></form></section></template></section></template>

<style scoped>
.member-number{display:block;color:var(--mall-color-muted);margin-top:4px}.member-results{list-style:none;padding:0;max-height:240px;overflow:auto}.member-results li{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:12px;border-bottom:1px solid var(--mall-color-border)}.member-selection{padding:12px;background:var(--mall-color-brand-soft);border-radius:8px;overflow-wrap:anywhere}
</style>
