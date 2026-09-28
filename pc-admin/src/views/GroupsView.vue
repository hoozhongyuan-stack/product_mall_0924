<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, api, confirmedWrite, type Account, type PermissionGroup } from '../api'

const props = defineProps<{ account: Account }>()
const permissionOptions = [
  ['catalog.read', '查看商品'], ['catalog.write', '编辑商品'],
  ['sku.status.write', '调整 SKU 状态'], ['sku.unit.write', '调整 SKU 单位'], ['sku.price.write', '调整 SKU 价格'],
  ['asset.read', '查看素材'], ['asset.upload', '上传素材'], ['asset.delete', '删除素材'],
  ['page.read', '查看页面'], ['page.edit', '编辑页面'], ['page.publish', '发布页面'],
  ['startup.read', '查看启动素材'], ['startup.edit', '编辑启动素材'], ['startup.publish', '发布启动素材'],
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
const currentPassword = ref('')
const canManage = computed(() => props.account.permissionCodes.includes('permission.manage'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    groups.value = await api<PermissionGroup[]>('/permission-groups')
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
  currentPassword.value = ''
  error.value = ''
}

function openEdit(group: PermissionGroup) {
  editing.value = group
  code.value = group.code
  name.value = group.name
  selected.value = [...group.permissionCodes]
  currentPassword.value = ''
  error.value = ''
}

function togglePermission(value: string) {
  selected.value = selected.value.includes(value)
    ? selected.value.filter((item) => item !== value)
    : [...selected.value, value]
}

function closeEditor() {
  editing.value = null
  currentPassword.value = ''
}

async function save() {
  const target = editing.value
  if (!target || !currentPassword.value || (target === 'new' && (!code.value.trim() || !name.value.trim()))) {
    error.value = '请填写必填项和当前账号密码。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    if (target === 'new') {
      await confirmedWrite<PermissionGroup>('group.create', currentPassword.value,
        '/permission-groups', 'POST', { code: code.value.trim(), name: name.value.trim(), permissionCodes: selected.value })
    } else {
      await confirmedWrite<PermissionGroup>('group.update', currentPassword.value,
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
    currentPassword.value = ''
    saving.value = false
  }
}
</script>

<template>
  <section class="page-content">
    <div class="page-heading"><div><h1>权限组</h1><p>查看或调整账号可执行的操作。</p></div>
      <button v-if="canManage" class="primary-button" type="button" @click="openCreate">创建权限组</button>
    </div>
    <p v-if="error" class="error notice" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-inline" role="status">正在加载权限组…</p>
    <div v-else class="group-grid">
      <article v-for="group in groups" :key="group.groupId" class="panel group-card">
        <div class="panel-heading"><div><h2>{{ group.name }}</h2><p class="code">{{ group.code }}</p></div>
          <button v-if="canManage && group.enabled && !(account.kind === 'STAFF' && account.groupIds.includes(group.groupId))" class="text-button" type="button" @click="openEdit(group)">编辑权限</button>
        </div>
        <span :class="['badge', group.enabled ? 'badge-good' : 'badge-muted']">{{ group.enabled ? '启用' : '停用' }}</span>
        <p>{{ group.permissionCodes.length }} 项权限</p>
        <details><summary>查看操作码</summary><ul><li v-for="item in group.permissionCodes" :key="item">{{ item }}</li></ul></details>
      </article>
      <p v-if="!groups.length" class="panel empty-state">暂无权限组。</p>
    </div>
    <button v-if="error && !loading" class="text-button retry" type="button" @click="load">重新加载</button>
    <form v-if="editing" class="panel action-panel" @submit.prevent="save">
      <div class="panel-heading"><h2>{{ editing === 'new' ? '创建权限组' : `编辑 ${name} 的权限` }}</h2><button class="text-button" type="button" @click="closeEditor">取消</button></div>
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
      <label class="confirm-field">当前账号密码（二次确认）<input v-model="currentPassword" type="password" autocomplete="current-password" required /></label>
      <button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : '确认保存' }}</button>
    </form>
  </section>
</template>
