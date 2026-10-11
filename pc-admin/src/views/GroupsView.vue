<script setup lang="ts">
import { confirmAction } from '../shared/confirm'
import AdminListPager from '../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../shared/list/AdminSelectedRowsDialog.vue'

import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, api, confirmedWrite, type Account, type PermissionGroup } from '../api'

const props = defineProps<{ account: Account }>()
const permissionOptions = [
  ['catalog.read', '查看商品'], ['catalog.write', '编辑商品'],
  ['sku.status.write', '调整 SKU 状态'], ['sku.unit.write', '调整 SKU 单位'], ['sku.price.write', '调整 SKU 价格'],
  ['asset.read', '查看素材'], ['asset.upload', '上传素材'], ['asset.delete', '删除素材'],
  ['page.read', '查看页面'], ['page.edit', '编辑页面'], ['page.publish', '发布页面'],
  ['startup.read', '查看启动素材'], ['startup.edit', '编辑启动素材'], ['startup.publish', '发布启动素材'],
  ['wechat.integration.read', '查看小程序接入'], ['wechat.integration.manage', '管理与校验小程序接入（需查看接入）'],
  ['code.version.read', '查看小程序代码版本与发布条件'], ['code.release.manage', '配置小程序代码上传密钥（需查看代码版本）'],
  ['account.read', '查看账号'], ['account.manage', '管理账号'],
  ['account.reset_credentials', '重置账号凭据'], ['account.unlock', '解锁账号'],
  ['permission.read', '查看权限组'], ['permission.manage', '管理权限组'], ['audit.read', '查看操作日志'], ['business.report.read', '查看经营统计'],
  ['member.read', '查看会员与权益'], ['member.rules.read', '查看等级与积分规则'], ['member.rules.manage', '管理等级与积分规则'],
  ['coupon.read', '查看优惠券活动'], ['coupon.manage', '管理优惠券草稿（需查看券）'], ['coupon.publish', '发布与暂停发券（需查看券）'], ['coupon.issue', '向会员发券（需查看券及会员）'], ['coupon.issue.repeat', '重复发券（需发券权限）'],
  ['exchange.read', '查看积分商城商品'], ['exchange.manage', '配置兑换积分（需查看兑换）'], ['exchange.publish', '开启与暂停兑换（需查看兑换）'],
  ['aftersale.read', '查看售后'], ['aftersale.review', '审核售后'],
  ['aftersale.return.accept', '退货验收与免寄回批准'],
  ['refund.prepare', '准备及发起退款'], ['refund.offline.confirm', '独立复核线下退款'],
] as const
const groups = ref<PermissionGroup[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const editing = ref<PermissionGroup | 'new' | null>(null)
const code = ref('')
const name = ref('')
const selected = ref<string[]>([])
const canManage = computed(() => props.account.permissionCodes.includes('permission.manage'))

async function load() {
  selectedIds.value = []
  loading.value = true
  error.value = ''
  try {
    groups.value = await api<PermissionGroup[]>('/permission-groups')
    page.value = Math.min(page.value, Math.max(1, Math.ceil(filteredRows.value.length / pageSize.value)))
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '权限组加载失败。'
  } finally {
    loading.value = false
  }
}
onMounted(load)

function openCreate() {
  editing.value = 'new'
  code.value = ''
  name.value = ''
  selected.value = []
  error.value = ''
}

function openEdit(group: PermissionGroup) {
  editing.value = group
  code.value = group.code
  name.value = group.name
  selected.value = [...group.permissionCodes]
  error.value = ''
}

function togglePermission(value: string) {
  selected.value = selected.value.includes(value)
    ? selected.value.filter((item) => item !== value)
    : [...selected.value, value]
}

function closeEditor() {
  editing.value = null
  }

async function save() {
  const target = editing.value
  if (!target  || (target === 'new' && (!code.value.trim() || !name.value.trim()))) {
    error.value = '请填写必填项。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    if (target === 'new') {
      await confirmedWrite<PermissionGroup>('group.create', '',
        '/permission-groups', 'POST', { code: code.value.trim(), name: name.value.trim(), permissionCodes: selected.value })
    } else {
      await confirmedWrite<PermissionGroup>('group.update', '',
        `/permission-groups/${target.groupId}`, 'PATCH',
        { permissionCodes: selected.value, expectedRevision: target.revision }, target.groupId, target.revision)
    }
    editing.value = null
    ElMessage.success('权限组已保存')
    await load()
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) {
      closeEditor()
      await load()
      error.value = '权限组已被其他人修改。列表已刷新，请核对后重试。'
    } else {
      error.value = reason instanceof Error ? reason.message : '保存失败。'
    }
  } finally {
    saving.value = false
  }
}


const search = ref(''), enabledFilter = ref(''), page = ref(1), pageSize = ref(20)
const appliedSearch = ref(''), appliedEnabled = ref('')
const filteredRows = computed(() => groups.value.filter(row => `${row.name} ${row.code}`.toLowerCase().includes(appliedSearch.value.toLowerCase()) && (!appliedEnabled.value || String(row.enabled) === appliedEnabled.value)))
const visibleRows = computed(() => filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
function applyFilters() { appliedSearch.value = search.value.trim(); appliedEnabled.value = enabledFilter.value; page.value=1; selectedIds.value = [] }
function resetFilters() { search.value=''; enabledFilter.value=''; applyFilters() }
function pageChanged(value:number,size:number) { page.value=value; pageSize.value=size; selectedIds.value = [] }
const compareRows = computed(() => currentRows.value.filter(row => selectedIds.value.includes(row.groupId)).map(row => ({...row, status:row.enabled ? '启用' : '停用', permissions:row.permissionCodes.join('、') })))
const detailTarget = ref<PermissionGroup | null>(null)

const selectedIds = ref<string[]>([])
const comparisonOpen = ref(false)
const currentRows = computed(() => visibleRows.value)
const allSelected = computed(() => currentRows.value.length > 0 && currentRows.value.every(row => selectedIds.value.includes(row.groupId)))
function toggleRow(id: string) { selectedIds.value = selectedIds.value.includes(id) ? selectedIds.value.filter(value => value !== id) : [...selectedIds.value, id] }
function selectPage() { selectedIds.value = allSelected.value ? [] : currentRows.value.map(row => row.groupId) }
watch([search,enabledFilter],()=>{selectedIds.value = []})
async function requestCloseEditor() {
  if (saving.value) return false
  const original=JSON.stringify([editing.value,code.value,name.value,selected.value])
  const dirty=editing.value==='new' ? !!(code.value || name.value || selected.value.length) : !!editing.value && JSON.stringify(editing.value.permissionCodes)!==JSON.stringify(selected.value)
  if (dirty && !await confirmAction('有未保存权限修改，确定关闭？')) return false
  if (saving.value || original!==JSON.stringify([editing.value,code.value,name.value,selected.value])) return false
  closeEditor()
  return true
}
async function closeGroupDialog(done:() => void) { if(await requestCloseEditor()) done() }
</script>

<template>
  <section class="page-content admin-list-page">
    <div class="page-heading"><div><h1>权限组</h1><p>查看或调整账号可执行的操作。</p></div>
      <button v-if="canManage" class="primary-button" type="button" @click="openCreate">创建权限组</button>
    </div>
    <form class="admin-list-filters" @submit.prevent="applyFilters"><label>名称 / 编码<input v-model="search" maxlength="150" placeholder="查询权限组" /></label><label>状态<select v-model="enabledFilter"><option value="">全部状态</option><option value="true">启用</option><option value="false">停用</option></select></label><button class="primary-button" :disabled="loading">查询</button><button class="secondary-button" type="button" :disabled="loading" @click="resetFilters">重置</button></form>
<AdminSelectionBar :count="selectedIds.length" unit="条" :loading="loading" @clear="selectedIds=[]"><button class="secondary-button" :disabled="!selectedIds.length" @click="comparisonOpen=true">批量查看</button></AdminSelectionBar>
<p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-inline" role="status">正在加载权限组…</p>
    <div v-else class="panel table-wrap admin-list-table"><table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部记录" :checked="allSelected" @change="selectPage" /></th><th>名称 / 编码</th><th>权限数</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="group in visibleRows" :key="group.groupId"><td><input type="checkbox" :aria-label="`选择权限组 ${group.name}`" :checked="selectedIds.includes(group.groupId)" @change="toggleRow(group.groupId)" /></td><td><strong>{{group.name}}</strong><small>{{group.code}}</small></td><td>{{group.permissionCodes.length}} 项</td><td><AdminStatusBadge :status="group.enabled?'ENABLED':'DISABLED'" :label="group.enabled?'启用':'停用'" :tone="group.enabled?'success':'neutral'" /></td><td><button class="text-button" @click="detailTarget=group">查看权限</button><button v-if="canManage && group.enabled && !(account.kind === 'STAFF' && account.groupIds.includes(group.groupId))" class="text-button" @click="openEdit(group)">编辑权限</button></td></tr><tr v-if="!visibleRows.length"><td colspan="5" class="empty-state">当前条件下暂无权限组。</td></tr></tbody></table></div>
    <AdminListPager :page="page" :page-size="pageSize" :total="filteredRows.length" :loading="loading" @change="pageChanged" />
<AdminSelectedRowsDialog v-model="comparisonOpen" title="所选权限组" :rows="compareRows" :columns="[{key:'name',label:'名称'},{key:'code',label:'编码'},{key:'permissions',label:'权限码'},{key:'status',label:'状态'}]" />
<el-dialog :model-value="Boolean(detailTarget)" title="权限组详情" @update:model-value="detailTarget=null"><template v-if="detailTarget"><h2>{{detailTarget.name}}</h2><p>{{detailTarget.code}} · 修订 {{detailTarget.revision}}</p><ul><li v-for="code in detailTarget.permissionCodes" :key="code">{{code}}</li></ul></template></el-dialog>
<button v-if="error && !loading" class="text-button retry" type="button" @click="load">重新加载</button>
    <el-dialog :model-value="Boolean(editing)" :title="editing==='new'?'创建权限组':'编辑权限'" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" :before-close="closeGroupDialog"><form v-if="editing" class="action-panel" @submit.prevent="save"><p v-if="error" class="error notice" role="alert">{{error}}</p>
      <div class="panel-heading"><h2>{{ editing === 'new' ? '创建权限组' : `编辑 ${name} 的权限` }}</h2><button class="text-button" type="button" :disabled="saving" @click="requestCloseEditor">取消</button></div>
      <div v-if="editing === 'new'" class="form-grid">
        <label>编码<input v-model="code" maxlength="60" required placeholder="例如 support" /></label>
        <label>名称<input v-model="name" maxlength="100" required placeholder="例如 客服" /></label>
      </div>
      <fieldset><legend>授权操作</legend><div class="permission-grid">
        <label v-for="[value, label] in permissionOptions" :key="value" class="check-row">
          <input type="checkbox" :checked="selected.includes(value)" @change="togglePermission(value)" />
          <span>{{ label }}<small>{{ value }}</small></span>
        </label>
      </div></fieldset>

      <button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : '确认保存' }}</button>
    </form></el-dialog>
  </section>
</template>
