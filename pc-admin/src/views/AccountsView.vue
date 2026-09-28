<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, api, confirmedWrite, type Account, type PermissionGroup } from '../api'

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
const currentPassword = ref('')
const disableTarget = ref<Account | null>(null)
const canManage = computed(() => props.account.permissionCodes.includes('account.manage'))

async function load() {
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
  if (!loginName.value.trim() || !displayName.value.trim() || !newPassword.value || !groupIds.value.length || !currentPassword.value) {
    error.value = '请填写账号资料、至少选择一个权限组，并输入当前账号密码。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await confirmedWrite<Account>('account.create', currentPassword.value, '/accounts', 'POST', {
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
    currentPassword.value = ''
    saving.value = false
  }
}

async function disableAccount() {
  const target = disableTarget.value
  if (!target || !currentPassword.value) {
    error.value = '请输入当前账号密码完成停用确认。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await confirmedWrite<Account>('account.disable', currentPassword.value,
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
    currentPassword.value = ''
    saving.value = false
  }
}

function openCreate() {
  disableTarget.value = null
  currentPassword.value = ''
  error.value = ''
  formOpen.value = true
}

function openDisable(target: Account) {
  formOpen.value = false
  currentPassword.value = ''
  error.value = ''
  disableTarget.value = target
}

function closeForm() {
  formOpen.value = false
  disableTarget.value = null
  newPassword.value = ''
  currentPassword.value = ''
}
</script>

<template>
  <section class="page-content">
    <div class="page-heading"><div><h1>子账号</h1><p>查看授权范围，创建或停用后台账号。</p></div>
      <button v-if="canManage" class="primary-button" type="button" :disabled="loading || !groups.length" @click="openCreate">创建子账号</button>
    </div>
    <p v-if="canManage && !loading && !groups.length" class="hint">创建账号需要一个可用权限组及权限组读取权限。</p>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-inline" role="status">正在加载账号…</p>
    <div v-else class="panel table-wrap">
      <table><thead><tr><th>登录名</th><th>显示名</th><th>身份</th><th>权限组</th><th>状态</th><th v-if="canManage">操作</th></tr></thead>
        <tbody><tr v-for="item in accounts" :key="item.accountId">
          <td class="strong-cell">{{ item.loginName }}</td><td>{{ item.displayName }}</td>
          <td>{{ item.kind === 'OWNER' ? '主账号' : '子账号' }}</td><td>{{ groupNames(item.groupIds) }}</td>
          <td><span :class="['badge', item.enabled ? 'badge-good' : 'badge-muted']">{{ item.enabled ? '启用' : '停用' }}</span></td>
          <td v-if="canManage"><button v-if="item.kind === 'STAFF' && item.enabled && item.accountId !== account.accountId" class="text-button danger" type="button" @click="openDisable(item)">停用</button><span v-else>—</span></td>
        </tr></tbody></table>
      <p v-if="!accounts.length" class="empty-state">暂无账号。</p>
    </div>
    <button v-if="error && !loading" class="text-button retry" type="button" @click="load">重新加载</button>

    <form v-if="formOpen" class="panel action-panel" @submit.prevent="createAccount">
      <div class="panel-heading"><h2>创建子账号</h2><button class="text-button" type="button" @click="closeForm">取消</button></div>
      <div class="form-grid">
        <label>登录名<input v-model="loginName" autocomplete="off" maxlength="150" required placeholder="例如 operator-01" /></label>
        <label>显示名<input v-model="displayName" maxlength="120" required placeholder="例如 商品运营" /></label>
        <label>新账号密码<input v-model="newPassword" type="password" autocomplete="new-password" required placeholder="请设置强密码" /></label>
      </div>
      <fieldset><legend>权限组（至少选一个）</legend>
        <label v-for="group in groups.filter((item) => item.enabled)" :key="group.groupId" class="check-row">
          <input type="checkbox" :checked="groupIds.includes(group.groupId)" @change="toggleGroup(group.groupId)" />{{ group.name }}
        </label>
      </fieldset>
      <label class="confirm-field">当前账号密码（二次确认）<input v-model="currentPassword" type="password" autocomplete="current-password" required /></label>
      <button class="primary-button" type="submit" :disabled="saving">{{ saving ? '创建中…' : '确认创建' }}</button>
    </form>
    <form v-if="disableTarget" class="panel action-panel" @submit.prevent="disableAccount">
      <div class="panel-heading"><h2>停用 {{ disableTarget.displayName }}</h2><button class="text-button" type="button" @click="closeForm">取消</button></div>
      <p>停用后，该子账号现有会话会失效，无法再次登录。</p>
      <label class="confirm-field">当前账号密码（二次确认）<input v-model="currentPassword" type="password" autocomplete="current-password" required /></label>
      <button class="danger-button" type="submit" :disabled="saving">{{ saving ? '停用中…' : '确认停用' }}</button>
    </form>
  </section>
</template>
