<script setup lang="ts">
import PointsExchangeDetailView from './PointsExchangeDetailView.vue'
import AdminListPager from '../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../shared/list/AdminSelectedRowsDialog.vue'

import {computed,ref,onMounted,onUnmounted,watch} from 'vue'
import {api,type Account} from '../api'
import {type Offer,type Page,offerStatus,specs} from './exchange/types'
import './exchange/exchange.css'
const props=defineProps<{account:Account}>(),data=ref<Page<Offer>|null>(null),loading=ref(false),error=ref(''),q=ref(''),status=ref(''),page=ref(1),pageSize=ref(20)
let generation=0
async function load(reset=false){selectedIds.value = [];if(reset)page.value=1;const seq=++generation;loading.value=true;error.value='';data.value=null;try{const params=new URLSearchParams({q:q.value.trim(),status:status.value,page:String(page.value),pageSize:String(pageSize.value)});const r=await api<Page<Offer>>(`/exchange-offers?${params}`);if(seq===generation)data.value=r}catch(e){if(seq===generation)error.value=e instanceof Error?e.message:'兑换商品读取失败。'}finally{if(seq===generation)loading.value=false}}
onMounted(()=>load());onUnmounted(()=>generation++);watch(()=>`${props.account.accountId}:${props.account.permissionCodes.join(',')}`,()=>{q.value='';status.value='';void load(true)})


const detailTarget = ref('')
const detailView = ref<{mayClose?: () => Promise<boolean>} | null>(null)
function openDetail(id:string) { detailTarget.value=id }
async function closeDetail(done:() => void) { if (detailView.value?.mayClose && !await detailView.value.mayClose()) return; detailTarget.value=''; done(); void load() }
const compareRows = computed(() => currentRows.value.filter(row => selectedIds.value.includes(row.id)).map(row => ({...row, displayStatus:offerStatus(row.status), fulfillment:row.fulfillmentKind==='SHIP'?'快递配送':'到店核销' })))

const selectedIds = ref<string[]>([])
const comparisonOpen = ref(false)
const currentRows = computed(() => data.value?.items || [])
const allSelected = computed(() => currentRows.value.length > 0 && currentRows.value.every(row => selectedIds.value.includes(row.id)))
function toggleRow(id: string) { selectedIds.value = selectedIds.value.includes(id) ? selectedIds.value.filter(value => value !== id) : [...selectedIds.value, id] }
function selectPage() { selectedIds.value = allSelected.value ? [] : currentRows.value.map(row => row.id) }
function pageChanged(value:number,size:number) { page.value=value; pageSize.value=size; void load() }
function resetFilters(){q.value='';status.value='';void load(true)}
watch([q,status],()=>{selectedIds.value = []})
</script>
<template><section class="page-content admin-list-page exchange-page"><header class="page-heading"><div><h1>积分商城商品</h1><p>复用现有 SKU 与库存，设置独立兑换积分；配送包含在积分售价中。</p></div><a v-if="account.permissionCodes.includes('exchange.manage')" href="/exchange-offers/new" @click.prevent="openDetail('new')" class="primary-button">添加兑换商品</a></header><form class="exchange-filters" @submit.prevent="load(true)"><label>商品名称或 SKU 编码<el-input v-model="q" maxlength="100" clearable/></label><label>兑换状态<el-select v-model="status"><el-option value="" label="全部状态"/><el-option value="DRAFT" label="草稿"/><el-option value="ON_SALE" label="兑换开启"/><el-option value="OFF_SALE" label="兑换暂停"/></el-select></label><el-button native-type="submit" type="primary" :loading="loading">查询</el-button><el-button :disabled="loading" @click="resetFilters">重置</el-button></form><p v-if="loading" role="status">正在读取兑换商品…</p><div v-else-if="error" class="notice error" role="alert">{{error}} <el-button @click="load()">重新读取</el-button></div><template v-else-if="data"><AdminSelectionBar :count="selectedIds.length" unit="条" :loading="loading" @clear="selectedIds=[]"><button class="secondary-button" :disabled="!selectedIds.length" @click="comparisonOpen=true">批量查看</button></AdminSelectionBar><div class="panel table-wrap admin-list-table" tabindex="0" aria-label="积分商城商品，可横向滚动"><table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部记录" :checked="allSelected" @change="selectPage" /></th><th>商品 / SKU</th><th>兑换积分</th><th>履约方式</th><th>可兑数量</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="offer in data.items" :key="offer.id"><td><input type="checkbox" :aria-label="`选择记录 ${offer.id}`" :checked="selectedIds.includes(offer.id)" @change="toggleRow(offer.id)" /></td><td><div class="exchange-product"><img v-if="offer.thumbnailUrl" :src="offer.thumbnailUrl" loading="lazy" decoding="async" alt="" @error="($event.target as HTMLImageElement).hidden=true"/><div><strong>{{offer.productName}}</strong><small>{{offer.skuCode}} · {{specs(offer.specs)}}</small></div></div></td><td class="exchange-points">{{offer.pointsPrice}} 积分 / {{offer.saleUnit}}</td><td>{{offer.fulfillmentKind==='SHIP'?'快递 · 配送已含':'到店核销'}}<small v-if="offer.fulfillmentKind==='REDEEM'">有效至 {{offer.redeemValidUntil||'未设置'}}</small></td><td>{{offer.availableQuantity??'待核查'}} {{offer.saleUnit}}</td><td><AdminStatusBadge :status="offer.status" :label="offerStatus(offer.status)" :tone="offer.status==='ON_SALE'?'success':'neutral'" /></td><td><a :href="`/exchange-offers/${offer.id}`" @click.prevent="openDetail(offer.id)" class="text-link">查看配置</a></td></tr><tr v-if="!data.items.length"><td colspan="7" class="empty-state">没有匹配的兑换商品，可调整筛选条件。</td></tr></tbody></table></div><AdminListPager :page="page" :page-size="pageSize" :total="data.pagination.total" :loading="loading" @change="pageChanged" /></template><AdminSelectedRowsDialog v-model="comparisonOpen" title="所选记录对比" :rows="compareRows" :columns="[{key:'productName',label:'商品'},{key:'skuCode',label:'SKU'},{key:'pointsPrice',label:'兑换积分'},{key:'displayStatus',label:'状态'},{key:'fulfillment',label:'履约'}]" />
<el-dialog :model-value="Boolean(detailTarget)" title="兑换商品配置" width="min(1120px, calc(100vw - 32px))" :close-on-click-modal="false" :before-close="closeDetail" destroy-on-close><PointsExchangeDetailView v-if="detailTarget" ref="detailView" :account="account" :offer-id="detailTarget" embedded /></el-dialog>
</section></template>
