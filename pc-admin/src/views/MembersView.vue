<script setup lang="ts">
import MemberDetailView from './MemberDetailView.vue'
import AdminListPager from '../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../shared/list/AdminSelectedRowsDialog.vue'

import {computed,onMounted,ref,watch} from 'vue'
import {api,type Account} from '../api'
import {money,date,type Member,type Page,type Grade} from './customers/types'
import './customers/customers.css'
const props=defineProps<{account:Account}>()
const data=ref<(Page<Member>&{grades:Grade[]})|null>(null),loading=ref(false),error=ref(''),search=ref(''),gradeId=ref(''),enabled=ref(''),page=ref(1),pageSize=ref(20)
const failedAvatars=ref<Record<string,boolean>>({})
function avatarFailed(id:string){failedAvatars.value={...failedAvatars.value,[id]:true}}
watch(data,()=>{failedAvatars.value={}})
let generation=0
async function load(reset=false){selectedIds.value = [];if(reset)page.value=1;const seq=++generation;loading.value=true;error.value='';try{const query=new URLSearchParams({page:String(page.value),pageSize:String(pageSize.value),search:search.value.trim()});if(gradeId.value)query.set('gradeId',gradeId.value);if(enabled.value)query.set('enabled',enabled.value);const result=await api<Page<Member>&{grades:Grade[]}>(`/members?${query}`);if(seq===generation){data.value=result;failedAvatars.value={}}}catch(e){if(seq===generation){data.value=null;error.value=e instanceof Error?e.message:'会员读取失败。'}}finally{if(seq===generation)loading.value=false}}
watch(()=>`${props.account.accountId}:${props.account.permissionCodes.join(',')}`,()=>{generation++;selectedIds.value = [];detailTarget.value='';data.value=null;loading.value=false;if(props.account.permissionCodes.includes('member.read'))void load()});
onMounted(()=>void load())


const detailTarget = ref('')
const detailView = ref<{mayClose?: () => Promise<boolean>} | null>(null)
function openDetail(id:string) { detailTarget.value=id }
async function closeDetail(done:() => void) { if (detailView.value?.mayClose && !await detailView.value.mayClose()) return; detailTarget.value=''; done(); void load() }
const compareRows = computed(() => currentRows.value.filter(row => selectedIds.value.includes(row.id)).map(row => ({...row, displayStatus:row.enabled ? '正常' : '停用', points:row.points.availablePoints, spend:money(row.effectiveSpendFen) })))

const selectedIds = ref<string[]>([])
const comparisonOpen = ref(false)
const currentRows = computed(() => data.value?.items || [])
const allSelected = computed(() => currentRows.value.length > 0 && currentRows.value.every(row => selectedIds.value.includes(row.id)))
function toggleRow(id: string) { selectedIds.value = selectedIds.value.includes(id) ? selectedIds.value.filter(value => value !== id) : [...selectedIds.value, id] }
function selectPage() { selectedIds.value = allSelected.value ? [] : currentRows.value.map(row => row.id) }
function pageChanged(value:number,size:number) { page.value=value; pageSize.value=size; void load() }
function resetFilters(){ search.value=''; gradeId.value=''; enabled.value=''; void load(true) }
watch([search,gradeId,enabled],()=>{selectedIds.value = []})
</script>
<template><section class="page-content admin-list-page customer-page"><header class="page-heading"><div><h1>会员</h1><p>查看当前等级、有效消费与积分权益，追溯每次结算来源。</p></div><RouterLink v-if="account.permissionCodes.includes('member.rules.read') || account.permissionCodes.includes('member.rules.manage')" to="/members/rules" class="secondary-button">等级与积分规则</RouterLink></header>
<form class="customer-filters" @submit.prevent="load(true)"><label>会员编号 / 昵称 / 手机号<input v-model="search" maxlength="64" placeholder="输入会员编号、昵称或手机号"></label><label>当前等级<select v-model="gradeId"><option value="">全部等级</option><option v-for="grade in data?.grades" :key="grade.id" :value="grade.id">{{grade.name}}</option></select></label><label>账号状态<select v-model="enabled"><option value="">全部状态</option><option value="true">正常</option><option value="false">停用</option></select></label><button class="primary-button" :disabled="loading">查询会员</button><button type="button" class="secondary-button" :disabled="loading" @click="resetFilters">重置</button></form>
<p v-if="loading" role="status">正在读取会员…</p><div v-else-if="error" class="notice error" role="alert">{{error}} <button class="text-button" @click="load()">重试</button></div><template v-else-if="data"><AdminSelectionBar :count="selectedIds.length" unit="条" :loading="loading" @clear="selectedIds=[]"><button class="secondary-button" :disabled="!selectedIds.length" @click="comparisonOpen=true">批量查看</button></AdminSelectionBar><p v-if="!data.items.length" class="panel empty-state">当前条件下暂无会员，请调整条件后查询。</p><div v-else class="panel table-wrap admin-list-table" tabindex="0" aria-label="会员列表，可横向滚动"><table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部记录" :checked="allSelected" @change="selectPage" /></th><th>头像 / 昵称</th><th>手机号</th><th>会员编号</th><th>等级／状态</th><th>有效消费</th><th>可用积分</th><th>冻结积分</th><th>积分欠额</th><th>加入时间</th><th>操作</th></tr></thead><tbody><tr v-for="m in data.items" :key="m.id"><td><input type="checkbox" :aria-label="`选择记录 ${m.id}`" :checked="selectedIds.includes(m.id)" @change="toggleRow(m.id)" /></td><td><div class="member-identity"><img v-if="m.avatarUrl && !failedAvatars[m.id]" :src="m.avatarUrl" @error="avatarFailed(m.id)" alt="会员头像" class="member-avatar"><span v-else class="member-avatar placeholder" aria-label="未设置头像">人</span><span>{{m.nickname || '未设置昵称'}}</span></div></td><td>{{m.phone || '未绑定'}}</td><td class="customer-id">{{m.memberNo || m.id}}</td><td>{{m.grade.name}}<small><AdminStatusBadge :status="m.enabled?'ENABLED':'DISABLED'" :label="m.enabled?'正常':'停用'" :tone="m.enabled?'success':'neutral'" /></small></td><td>{{money(m.effectiveSpendFen)}}</td><td>{{m.points.availablePoints}}</td><td>{{m.points.frozenPoints}}</td><td :class="{'error':m.points.debtPoints>0}">{{m.points.debtPoints}}</td><td>{{date(m.createdAt)}}</td><td><a :href="`/members/${m.id}`" @click.prevent="openDetail(m.id)" class="text-link">查看详情</a></td></tr></tbody></table></div><AdminListPager :page="page" :page-size="pageSize" :total="data.pagination.total" :loading="loading" @change="pageChanged" /></template><AdminSelectedRowsDialog v-model="comparisonOpen" title="所选记录对比" :rows="compareRows" :columns="[{key:'memberNo',label:'会员编号'},{key:'nickname',label:'昵称'},{key:'displayStatus',label:'状态'},{key:'points',label:'可用积分'},{key:'spend',label:'有效消费'}]" />
<el-dialog :model-value="Boolean(detailTarget)" title="会员详情" width="min(1120px, calc(100vw - 32px))" :close-on-click-modal="false" :before-close="closeDetail" destroy-on-close><MemberDetailView v-if="detailTarget" ref="detailView" :account="account" :member-id="detailTarget" embedded /></el-dialog>
</section></template>

<style scoped>
.member-identity { display:flex;align-items:center;gap:12px;min-width:160px; }.member-avatar { width:40px;height:40px;border-radius:50%;object-fit:cover;flex:none; }.placeholder { display:grid;place-items:center;background:var(--mall-color-brand-soft);color:var(--mall-color-brand); }
</style>
