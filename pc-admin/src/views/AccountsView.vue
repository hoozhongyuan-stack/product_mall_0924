<script setup lang="ts">
import { confirmAction } from '../shared/confirm'
import AdminListPager from '../shared/list/AdminListPager.vue'
import AdminStatusBadge from '../shared/list/AdminStatusBadge.vue'
import AdminSelectionBar from '../shared/list/AdminSelectionBar.vue'
import AdminSelectedRowsDialog from '../shared/list/AdminSelectedRowsDialog.vue'

import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, api, confirmedWrite, type Account, type PermissionGroup } from '../api'
import PasswordDialog from '../components/PasswordDialog.vue'
import { PASSWORD_POLICY_TEXT, passwordIssue } from '../shared/password-policy'

const props = defineProps<{ account: Account }>()
const accounts = ref<Account[]>([])
const groups = ref<PermissionGroup[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const formOpen = ref(false)
const loginName = ref('')
const displayName = ref('')
const newPassword = ref('')
const groupIds = ref<string[]>([])
const disableTarget = ref<Account | null>(null)
const passwordTarget = ref<Account | null>(null)
const canManage = computed(() => props.account.permissionCodes.includes('account.manage'))
const isOwner = computed(() => props.account.kind === 'OWNER')

async function passwordReset() {
  passwordTarget.value = null
  ElMessage.success('子账号密码已重置，其现有会话已失效')
  await load()
}

async function passwordTargetChanged() {
  passwordTarget.value = null
  await load()
  error.value = '账号已被其他人修改。列表已刷新，请核对后重试。'
}

async function load() {
  selectedIds.value = []
  loading.value = true
  error.value = ''
  try {
    const [accountRows, groupRows] = await Promise.all([
      api<Account[]>('/accounts'),
      props.account.permissionCodes.includes('permission.read')
        ? api<PermissionGroup[]>('/permission-groups') : Promise.resolve([]),
    ])
    accounts.value = accountRows
    groups.value = groupRows
    page.value = Math.min(page.value, Math.max(1, Math.ceil(filteredRows.value.length / pageSize.value)))
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '账号加载失败。'
  } finally {
    loading.value = false
  }
}
onMounted(load)

function toggleGroup(id: string) {
  groupIds.value = groupIds.value.includes(id)
    ? groupIds.value.filter((value) => value !== id)
    : [...groupIds.value, id]
}

function groupNames(ids: string[]) {
  const names = ids.map((id) => groups.value.find((group) => group.groupId === id)?.name).filter(Boolean)
  return names.length ? names.join('、') : ids.length ? '权限组不可查看' : '未分配'
}

async function createAccount() {
  if (!loginName.value.trim() || !displayName.value.trim() || !newPassword.value || !groupIds.value.length ) {
    error.value = '请填写账号资料，并至少选择一个权限组。'
    return
  }
  const issue = passwordIssue(newPassword.value)
  if (issue) { error.value = issue; return }
  saving.value = true
  error.value = ''
  try {
    await confirmedWrite<Account>('account.create', '', '/accounts', 'POST', {
      loginName: loginName.value.trim(), displayName: displayName.value.trim(),
      password: newPassword.value, groupIds: groupIds.value,
    })
    formOpen.value = false
    loginName.value = ''
    displayName.value = ''
    groupIds.value = []
    ElMessage.success('子账号已创建')
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败。'
  } finally {
    newPassword.value = ''
    saving.value = false
  }
}

async function disableAccount() {
  const target = disableTarget.value
  if (!target ) {
    error.value = '请选择要停用的账号。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await confirmedWrite<Account>('account.disable', '',
      `/accounts/${target.accountId}`, 'PATCH',
      { enabled: false, expectedRevision: target.revision }, target.accountId, target.revision)
    disableTarget.value = null
    ElMessage.success('子账号已停用，其会话已失效')
    await load()
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 409) {
      closeForm()
      await load()
      error.value = '账号已被其他人修改。列表已刷新，请核对后重试。'
    } else {
      error.value = reason instanceof Error ? reason.message : '停用失败。'
    }
  } finally {
    saving.value = false
  }
}

function openCreate() {
  disableTarget.value = null
  error.value = ''
  formOpen.value = true
}

function openDisable(target: Account) {
  formOpen.value = false
  error.value = ''
  disableTarget.value = target
}

function closeForm() {
  formOpen.value = false
  disableTarget.value = null
  newPassword.value = ''
  }


const search = ref(''), enabledFilter = ref(''), page = ref(1), pageSize = ref(20)
const appliedSearch = ref(''), appliedEnabled = ref('')
const filteredRows = computed(() => accounts.value.filter(row => `${row.loginName} ${row.displayName}`.toLowerCase().includes(appliedSearch.value.toLowerCase()) && (!appliedEnabled.value || String(row.enabled) === appliedEnabled.value)))
const visibleRows = computed(() => filteredRows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
function applyFilters() { appliedSearch.value = search.value.trim(); appliedEnabled.value = enabledFilter.value; page.value=1; selectedIds.value = [] }
function resetFilters() { search.value=''; enabledFilter.value=''; applyFilters() }
function pageChanged(value:number,size:number) { page.value=value; pageSize.value=size; selectedIds.value = [] }
const compareRows = computed(() => currentRows.value.filter(row => selectedIds.value.includes(row.accountId)).map(row => ({...row, status:row.enabled ? '启用' : '停用', groups:groupNames(row.groupIds) })))
const detailTarget = ref<Account | null>(null)

const selectedIds = ref<string[]>([])
const comparisonOpen = ref(false)
const currentRows = computed(() => visibleRows.value)
const allSelected = computed(() => currentRows.value.length > 0 && currentRows.value.every(row => selectedIds.value.includes(row.accountId)))
function toggleRow(id: string) { selectedIds.value = selectedIds.value.includes(id) ? selectedIds.value.filter(value => value !== id) : [...selectedIds.value, id] }
function selectPage() { selectedIds.value = allSelected.value ? [] : currentRows.value.map(row => row.accountId) }
watch([search,enabledFilter],()=>{selectedIds.value = []})
async function requestCloseForm() {
  if (saving.value) return false
  const original=JSON.stringify([loginName.value,displayName.value,newPassword.value,groupIds.value])
  if (formOpen.value && (loginName.value || displayName.value || newPassword.value || groupIds.value.length) && !await confirmAction('有未保存账号资料，确定关闭？')) return false
  if (saving.value || original!==JSON.stringify([loginName.value,displayName.value,newPassword.value,groupIds.value])) return false
  closeForm(); loginName.value=''; displayName.value=''; groupIds.value=[]
  return true
}
async function closeAccountDialog(done:() => void) { if(await requestCloseForm()) done() }
</script>

<template>
  <section class="page-content admin-list-page">
    <div class="page-heading"><div><h1>子账号</h1><p>查看授权范围，创建或停用后台账号。主账号可以重置子账号密码。</p></div>
      <button v-if="canManage" class="primary-button" type="button" :disabled="loading || !groups.length" @click="openCreate">创建子账号</button>
    </div>
    <p v-if="canManage && !loading && !groups.length" class="hint">创建账号需要一个可用权限组及权限组读取权限。</p>
    <form class="admin-list-filters" @submit.prevent="applyFilters"><label>登录名 / 显示名<input v-model="search" maxlength="150" placeholder="查询账号" /></label><label>状态<select v-model="enabledFilter"><option value="">全部状态</option><option value="true">启用</option><option value="false">停用</option></select></label><button class="primary-button" :disabled="loading">查询</button><button class="secondary-button" type="button" :disabled="loading" @click="resetFilters">重置</button></form>
<AdminSelectionBar :count="selectedIds.length" unit="条" :loading="loading" @clear="selectedIds=[]"><button class="secondary-button" :disabled="!selectedIds.length" @click="comparisonOpen=true">批量查看</button></AdminSelectionBar>
<p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-inline" role="status">正在加载账号…</p>
    <div v-else class="panel table-wrap admin-list-table">
      <table><thead><tr><th><input type="checkbox" aria-label="选择当前页全部记录" :checked="allSelected" @change="selectPage" /></th><th>登录名</th><th>显示名</th><th>身份</th><th>权限组</th><th>状态</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in visibleRows" :key="item.accountId">
          <td><input type="checkbox" :aria-label="`选择账号 ${item.loginName}`" :checked="selectedIds.includes(item.accountId)" @change="toggleRow(item.accountId)" /></td><td class="strong-cell">{{ item.loginName }}</td><td>{{ item.displayName }}</td>
          <td>{{ item.kind === 'OWNER' ? '主账号' : '子账号' }}</td><td>{{ groupNames(item.groupIds) }}</td>
          <td><AdminStatusBadge :status="item.enabled ? 'ENABLED' : 'DISABLED'" :label="item.enabled ? '启用' : '停用'" :tone="item.enabled ? 'success' : 'neutral'" /></td>
          <td><button class="text-button" @click="detailTarget=item">查看</button><template v-if="item.kind === 'STAFF' && item.accountId !== account.accountId"><button v-if="isOwner" class="text-button" type="button" @click="passwordTarget = item">重置密码</button><button v-if="canManage && item.enabled" class="text-button danger" type="button" @click="openDisable(item)">停用</button></template><span v-else>—</span></td>
        </tr></tbody></table>
      <p v-if="!visibleRows.length" class="empty-state">当前条件下暂无账号。</p>
    </div>
    <AdminListPager :page="page" :page-size="pageSize" :total="filteredRows.length" :loading="loading" @change="pageChanged" />
<AdminSelectedRowsDialog v-model="comparisonOpen" title="所选账号" :rows="compareRows" :columns="[{key:'loginName',label:'登录名'},{key:'displayName',label:'显示名'},{key:'groups',label:'权限组'},{key:'status',label:'状态'}]" />
<el-dialog :model-value="Boolean(detailTarget)" title="账号详情" @update:model-value="detailTarget=null"><template v-if="detailTarget"><dl><dt>登录名</dt><dd>{{detailTarget.loginName}}</dd><dt>显示名</dt><dd>{{detailTarget.displayName}}</dd><dt>身份</dt><dd>{{detailTarget.kind==='OWNER'?'主账号':'子账号'}}</dd><dt>权限组</dt><dd>{{groupNames(detailTarget.groupIds)}}</dd><dt>修订</dt><dd>{{detailTarget.revision}}</dd></dl></template></el-dialog>
<button v-if="error && !loading" class="text-button retry" type="button" @click="load">重新加载</button>
    <PasswordDialog v-if="passwordTarget" :target="passwordTarget" @close="passwordTarget = null" @changed="passwordReset" @stale="passwordTargetChanged" />

    <el-dialog :model-value="formOpen" title="创建子账号" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" :before-close="closeAccountDialog"><form class="action-panel" @submit.prevent="createAccount"><p v-if="error" class="error notice" role="alert">{{error}}</p>
      <div class="panel-heading"><h2>创建子账号</h2><button class="text-button" type="button"  :disabled="saving" @click="requestCloseForm">取消</button></div>
      <div class="form-grid">
        <label>登录名<input v-model="loginName" autocomplete="off" maxlength="150" required placeholder="例如 operator-01" /></label>
        <label>显示名<input v-model="displayName" maxlength="120" required placeholder="例如 商品运营" /></label>
        <label>新账号密码<input v-model="newPassword" type="password" autocomplete="new-password" required :placeholder="PASSWORD_POLICY_TEXT" minlength="6" maxlength="1024" /></label>
      </div>
      <fieldset><legend>权限组（至少选一个）</legend>
        <label v-for="group in groups.filter((item) => item.enabled)" :key="group.groupId" class="check-row">
          <input type="checkbox" :checked="groupIds.includes(group.groupId)" @change="toggleGroup(group.groupId)" />{{ group.name }}
        </label>
      </fieldset>

      <button class="primary-button" type="submit" :disabled="saving">{{ saving ? '创建中…' : '确认创建' }}</button>
    </form></el-dialog>
    <el-dialog :model-value="Boolean(disableTarget)" title="停用账号" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" :before-close="closeAccountDialog"><form v-if="disableTarget" class="action-panel" @submit.prevent="disableAccount"><p v-if="error" class="error notice" role="alert">{{error}}</p>
      <div class="panel-heading"><h2>停用 {{ disableTarget.displayName }}</h2><button class="text-button" type="button"  :disabled="saving" @click="requestCloseForm">取消</button></div>
      <p>停用后，该子账号现有会话会失效，无法再次登录。</p>

      <button class="danger-button" type="submit" :disabled="saving">{{ saving ? '停用中…' : '确认停用' }}</button>
    </form></el-dialog>
  </section>
</template>
