<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElDialog, ElInput } from 'element-plus'
import { api, ApiError, type Account } from '../api'
import { PASSWORD_POLICY_TEXT, passwordIssue } from '../shared/password-policy'

const props = defineProps<{ target?: Account }>()
const emit = defineEmits<{ close: []; changed: []; stale: [] }>()
const currentPassword = ref('')
const newPassword = ref('')
const repeatedPassword = ref('')
const saving = ref(false)
const error = ref('')
const title = computed(() => props.target ? `重置 ${props.target.displayName} 的密码` : '修改我的密码')

function clearPasswords() {
  currentPassword.value = ''
  newPassword.value = ''
  repeatedPassword.value = ''
}

function close() {
  if (saving.value) return
  clearPasswords()
  emit('close')
}

async function save() {
  if (saving.value) return
  error.value = ''
  if (!currentPassword.value || !newPassword.value || !repeatedPassword.value) {
    error.value = '请填写当前密码、新密码和确认新密码。'
    return
  }
  if (newPassword.value !== repeatedPassword.value) {
    error.value = '两次输入的新密码不一致，请重新确认。'
    return
  }
  const issue = passwordIssue(newPassword.value)
  if (issue) { error.value = issue; return }
  saving.value = true
  try {
    const target = props.target
    await api(target ? `/accounts/${target.accountId}/password` : '/auth/password', {
      method: 'POST', body: JSON.stringify({ currentPassword: currentPassword.value, newPassword: newPassword.value,
        ...(target ? { expectedRevision: target.revision } : {}) }),
    })
    clearPasswords()
    emit('changed')
  } catch (reason) {
    if (reason instanceof ApiError && reason.code === 'REVISION_CONFLICT') emit('stale')
    else error.value = reason instanceof Error ? reason.message : '修改失败，请重试。'
  } finally {
    clearPasswords()
    saving.value = false
  }
}
</script>

<template>
  <ElDialog :model-value="true" :title="title" width="min(480px, calc(100vw - 32px))" append-to-body
    :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" :before-close="close">
    <form class="password-form" @submit.prevent="save">
      <p class="help-text">{{ target ? '重置后，该子账号需要使用新密码重新登录。' : '修改后，所有设备上的登录都会失效，请使用新密码重新登录。' }}</p>
      <label for="account-current-password">{{ target ? '主账号当前密码' : '当前密码' }}</label>
      <ElInput id="account-current-password" v-model="currentPassword" type="password" autocomplete="current-password" :maxlength="1024" :disabled="saving" required />
      <label for="account-new-password">新密码</label>
      <ElInput id="account-new-password" v-model="newPassword" type="password" autocomplete="new-password" :maxlength="1024" :disabled="saving" required aria-describedby="password-policy" />
      <p id="password-policy" class="help-text">{{ PASSWORD_POLICY_TEXT }}</p>
      <label for="account-repeat-password">确认新密码</label>
      <ElInput id="account-repeat-password" v-model="repeatedPassword" type="password" autocomplete="new-password" :maxlength="1024" :disabled="saving" required />
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="password-actions"><ElButton :disabled="saving" @click="close">取消</ElButton><ElButton type="primary" native-type="submit" :loading="saving" :disabled="saving">{{ target ? '确认重置' : '保存新密码' }}</ElButton></div>
    </form>
  </ElDialog>
</template>

<style scoped>
.password-form { display: grid; gap: 12px; }
.password-form > p { margin: 0 0 4px; }
.password-form > label { font-weight: 600; margin-top: 4px; }
.password-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 12px; }
.password-actions :deep(.el-button + .el-button) { margin-left: 0; }
</style>
