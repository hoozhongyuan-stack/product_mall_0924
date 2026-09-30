<script setup lang="ts">
import {onMounted,ref,watch} from 'vue'
import {api,type Account} from '../api'
import {money,date,type Member,type Page,type Grade} from './customers/types'
import './customers/customers.css'
const props=defineProps<{account:Account}>()
const data=ref<(Page<Member>&{grades:Grade[]})|null>(null),loading=ref(false),error=ref(''),search=ref(''),gradeId=ref(''),enabled=ref(''),page=ref(1)
const failedAvatars=ref<Record<string,boolean>>({})
function avatarFailed(id:string){failedAvatars.value={...failedAvatars.value,[id]:true}}
watch(data,()=>{failedAvatars.value={}})
let generation=0
async function load(reset=false){if(reset)page.value=1;const seq=++generation;loading.value=true;error.value='';try{const query=new URLSearchParams({page:String(page.value),pageSize:'20',search:search.value.trim()});if(gradeId.value)query.set('gradeId',gradeId.value);if(enabled.value)query.set('enabled',enabled.value);const result=await api<Page<Member>&{grades:Grade[]}>(`/members?${query}`);if(seq===generation){data.value=result;failedAvatars.value={}}}catch(e){if(seq===generation){data.value=null;error.value=e instanceof Error?e.message:'会员读取失败。'}}finally{if(seq===generation)loading.value=false}}
watch(()=>`${props.account.accountId}:${props.account.permissionCodes.join(',')}`,()=>{generation++;data.value=null;loading.value=false;if(props.account.permissionCodes.includes('member.read'))void load()});
onMounted(()=>void load())
</script>
<template><section class="page-content customer-page"><header class="page-heading"><div><h1>会员</h1><p>查看当前等级、有效消费与积分权益，追溯每次结算来源。</p></div><RouterLink v-if="account.permissionCodes.includes('member.rules.read') || account.permissionCodes.includes('member.rules.manage')" to="/members/rules" class="secondary-button">等级与积分规则</RouterLink></header>
<form class="customer-filters" @submit.prevent="load(true)"><label>会员编号 / 昵称 / 手机号<input v-model="search" maxlength="64" placeholder="输入会员编号、昵称或手机号"></label><label>当前等级<select v-model="gradeId"><option value="">全部等级</option><option v-for="grade in data?.grades" :key="grade.id" :value="grade.id">{{grade.name}}</option></select></label><label>账号状态<select v-model="enabled"><option value="">全部状态</option><option value="true">正常</option><option value="false">停用</option></select></label><button class="primary-button" :disabled="loading">查询会员</button></form>
<p v-if="loading" role="status">正在读取会员…</p><div v-else-if="error" class="notice error" role="alert">{{error}} <button class="text-button" @click="load()">重试</button></div><template v-else-if="data"><p v-if="!data.items.length" class="panel empty-state">当前条件下暂无会员，请调整条件后查询。</p><div v-else class="panel table-wrap" tabindex="0" aria-label="会员列表，可横向滚动"><table><thead><tr><th>头像 / 昵称</th><th>手机号</th><th>会员编号</th><th>等级／状态</th><th>有效消费</th><th>可用积分</th><th>冻结积分</th><th>积分欠额</th><th>加入时间</th><th>操作</th></tr></thead><tbody><tr v-for="m in data.items" :key="m.id"><td><div class="member-identity"><img v-if="m.avatarUrl && !failedAvatars[m.id]" :src="m.avatarUrl" @error="avatarFailed(m.id)" alt="会员头像" class="member-avatar"><span v-else class="member-avatar placeholder" aria-label="未设置头像">人</span><span>{{m.nickname || '未设置昵称'}}</span></div></td><td>{{m.phone || '未绑定'}}</td><td class="customer-id">{{m.memberNo || m.id}}</td><td>{{m.grade.name}}<small>{{m.enabled?'正常':'停用'}}</small></td><td>{{money(m.effectiveSpendFen)}}</td><td>{{m.points.availablePoints}}</td><td>{{m.points.frozenPoints}}</td><td :class="{'error':m.points.debtPoints>0}">{{m.points.debtPoints}}</td><td>{{date(m.createdAt)}}</td><td><RouterLink :to="`/members/${m.id}`" class="text-link">查看详情</RouterLink></td></tr></tbody></table></div><div class="customer-pagination"><span>共 {{data.pagination.total}} 位 · 第 {{page}} 页</span><div><button class="secondary-button" :disabled="page<=1" @click="page--;load()">上一页</button><button class="secondary-button" :disabled="page*data.pagination.pageSize>=data.pagination.total" @click="page++;load()">下一页</button></div></div></template></section></template>

<style scoped>
.member-identity { display:flex;align-items:center;gap:12px;min-width:160px; }.member-avatar { width:40px;height:40px;border-radius:50%;object-fit:cover;flex:none; }.placeholder { display:grid;place-items:center;background:var(--mall-color-brand-soft);color:var(--mall-color-brand); }
</style>
