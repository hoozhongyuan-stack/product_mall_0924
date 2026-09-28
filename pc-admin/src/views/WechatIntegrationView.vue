<script setup lang="ts">
import 'element-plus/theme-chalk/el-radio.css'
import 'element-plus/theme-chalk/el-radio-group.css'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElButton, ElInput, ElRadio, ElRadioGroup } from 'element-plus'
import { api, type Account, type Confirmation } from '../api'
import { checkMessages, checkTitles, credentialIssue, integrationPath, readIntegration, safeFailure, type Integration } from './integrations/wechat'
import './integrations/wechat.css'

const props = defineProps<{ account: Account }>()
const config = ref<Integration | null>(null)
const appId = ref(''), secret = ref(''), password = ref('')
const secretAction = ref<'KEEP' | 'REPLACE'>('KEEP')
const loading = ref(false), busy = ref(false), needsRead = ref(false)
const error = ref(''), notice = ref(''), confirmation = ref<'save' | 'test' | ''>('')
const canRead = computed(() => props.account.permissionCodes.includes('wechat.integration.read'))
const canManage = computed(() => canRead.value && props.account.permissionCodes.includes('wechat.integration.manage'))
const dirty = computed(() => !!config.value && (appId.value !== config.value.appId || secretAction.value === 'REPLACE' && !!secret.value))
const issue = computed(() => config.value ? credentialIssue(config.value, appId.value, secretAction.value, secret.value) : '')
const saveAllowed = computed(() => canManage.value && config.value?.keyAvailable && !issue.value && !needsRead.value && !busy.value && !loading.value)
const testAllowed = computed(() => canManage.value && config.value?.secretConfigured && !!config.value.appId && !dirty.value && !needsRead.value && !busy.value && !loading.value)
let epoch = 0

function adopt(value: Integration) {
  config.value = value; appId.value = value.appId; secret.value = ''
  secretAction.value = value.secretConfigured ? 'KEEP' : 'REPLACE'
}
async function load() {
  if (!canRead.value || busy.value || loading.value) return
  const current = ++epoch, keepInput = dirty.value
  loading.value = true; error.value = ''; notice.value = ''; confirmation.value = ''; password.value = ''
  try {
    const value = readIntegration(await api<unknown>(integrationPath))
    if (current !== epoch) return
    if (keepInput) { config.value = value; notice.value = '已读取最新配置，输入已保留。请核对后重新确认；读取本身不证明上次密钥替换成功。' }
    else adopt(value)
    needsRead.value = false
  } catch (reason) {
    if (current === epoch) { error.value = safeFailure(reason); needsRead.value = true }
  } finally { if (current === epoch) loading.value = false }
}
function begin(action: 'save' | 'test') {
  if (action === 'save' ? !saveAllowed.value : !testAllowed.value) return
  confirmation.value = action; password.value = ''; error.value = ''; notice.value = ''
}
function cancel() { if (!busy.value) { confirmation.value = ''; password.value = '' } }
function changeSecretAction() { secret.value = ''; cancel() }
async function submit() {
  const action = confirmation.value, previous = config.value
  if (!action || !previous || !password.value || (action === 'save' ? !saveAllowed.value : !testAllowed.value)) return
  const current = ++epoch, requestedAppId = appId.value
  // Sensitive input is kept only in this in-flight request and the current form.
  const body = action === 'test' ? { expectedRevision: previous.revision } : {
    expectedRevision: previous.revision, appId: requestedAppId, secretAction: secretAction.value,
    ...(secretAction.value === 'REPLACE' ? { appSecret: secret.value } : {}),
  }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const confirm = await api<Confirmation>('/auth/confirm', { method: 'POST', body: JSON.stringify({
      action: action === 'save' ? 'wechat.integration.update' : 'wechat.integration.test', password: password.value,
      objectId: 'wechat-mini-program', revision: previous.revision,
    }) })
    if (current !== epoch || !canManage.value) return
    if (typeof confirm.confirmationToken !== 'string' || !confirm.confirmationToken) throw new Error('Invalid confirmation')
    const result = readIntegration(await api<unknown>(integrationPath + (action === 'test' ? '/test' : ''), {
      method: action === 'save' ? 'PUT' : 'POST', headers: { 'X-Action-Confirmation': confirm.confirmationToken }, body: JSON.stringify(body),
    }))
    if (current !== epoch) return
    if (result.revision !== previous.revision + (action === 'save' ? 1 : 0)
      || result.appId !== (action === 'save' ? requestedAppId : previous.appId)
      || action === 'save' && (!result.secretConfigured || result.lastCheck !== null)
      || action === 'test' && result.lastCheck === null) throw new Error('Unexpected configuration result')
    adopt(result); confirmation.value = ''; needsRead.value = action === 'test' && result.lastCheck?.status !== 'SUCCESS'
    notice.value = action === 'save' ? '配置已保存。下一步请校验已保存配置；保存不代表微信接入已验证。' : '已读取本次凭据校验结果，请查看下方状态。'
  } catch (reason) {
    if (current === epoch) { error.value = safeFailure(reason); needsRead.value = true; confirmation.value = '' }
  } finally { if (current === epoch) { busy.value = false; password.value = '' } }
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value || busy.value || needsRead.value) { event.preventDefault(); event.returnValue = '' }
}
onBeforeRouteLeave(() => !busy.value && (!(dirty.value || needsRead.value) || window.confirm('有未保存输入或未核对的操作结果，确定离开？密钥输入不会保留。')))
watch(() => JSON.stringify([props.account.accountId, props.account.permissionCodes]), () => {
  epoch++; config.value = null; appId.value = ''; secret.value = ''; password.value = ''; confirmation.value = ''
  busy.value = false; loading.value = false; needsRead.value = false; error.value = ''; notice.value = ''
  void load()
})
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void load() })
onUnmounted(() => { epoch++; secret.value = ''; password.value = ''; window.removeEventListener('beforeunload', beforeUnload) })
</script>

<template>
  <section class="page-content wechat-integration-page">
    <header class="page-heading"><div><h1>小程序接入</h1><p>配置商城使用的微信小程序凭据，并校验当前已保存的配置。</p></div><ElButton v-if="canRead" :disabled="loading || busy" @click="load">重新读取</ElButton></header>
    <p v-if="!canRead" class="notice error" role="alert">当前账号没有读取权限。</p>
    <template v-else>
      <p v-if="loading" class="loading-inline" role="status">正在读取接入配置…</p>
      <p v-if="error" class="notice error" role="alert">{{ error }}<span v-if="needsRead"> 保存与校验已暂停，请先重新读取。</span></p>
      <p v-if="notice" class="wechat-notice" role="status">{{ notice }}<span v-if="needsRead"> 请先重新读取配置，再次确认后才能重试。</span></p>
      <template v-if="config">
        <section class="panel wechat-config" aria-labelledby="wechat-config-title">
          <div class="panel-heading"><div><h2 id="wechat-config-title">应用凭据</h2><p>AppSecret 仅用于写入，已保存的密钥不会回显。</p></div><span class="badge badge-muted">{{ config.source === 'ENV' ? '环境配置' : '后台托管' }} · 修订 {{ config.revision }}</span></div>
          <dl class="wechat-metadata"><div><dt>当前 AppID</dt><dd>{{ config.appId || '尚未配置' }}</dd></div><div><dt>密钥状态</dt><dd>{{ config.secretConfigured ? '已配置' : '尚未配置' }}</dd></div></dl>
          <p v-if="!config.keyAvailable" class="wechat-warning">托管加密密钥不可用，暂不能保存。请联系部署管理员；现有环境配置仍可进行凭据校验。</p>
          <p v-if="config.identityBinding.status === 'BOUND'" class="wechat-note">会员身份已绑定 AppID：<strong>{{ config.identityBinding.appId }}</strong>。只允许使用此 AppID。<span v-if="config.identityBinding.appId !== config.appId">当前配置与会员身份不一致，请修复为已绑定的 AppID。</span></p>
          <p v-if="config.identityBinding.status === 'MULTIPLE'" class="wechat-warning">历史会员身份涉及多个 AppID。这里只允许保留当前 AppID 并轮换密钥，不会自动迁移会员身份。</p>
          <form class="wechat-form" @submit.prevent="begin('save')">
            <label for="wechat-app-id">小程序 AppID<ElInput id="wechat-app-id" v-model="appId" maxlength="64" autocomplete="off" :disabled="!canManage || busy || loading || !!confirmation" placeholder="wx 开头的小程序 AppID" /></label>
            <template v-if="canManage">
              <fieldset :disabled="busy || loading || !!confirmation"><legend>密钥操作</legend><ElRadioGroup v-model="secretAction" aria-label="密钥操作" :disabled="busy || loading || !!confirmation" @change="changeSecretAction"><ElRadio value="KEEP" :disabled="!config.secretConfigured">保留已配置密钥</ElRadio><ElRadio value="REPLACE">替换密钥</ElRadio></ElRadioGroup></fieldset>
              <label v-if="secretAction === 'REPLACE'" for="wechat-secret">新的 AppSecret<ElInput id="wechat-secret" v-model="secret" type="password" autocomplete="new-password" :disabled="busy || loading || !!confirmation" placeholder="输入新密钥，保存成功后清空" /></label>
              <p v-if="dirty && issue" class="error" role="status">{{ issue }}</p>
              <div class="wechat-actions"><ElButton type="primary" native-type="submit" :disabled="!saveAllowed || !!confirmation">保存配置</ElButton><span v-if="dirty" class="wechat-note">有未保存修改</span></div>
            </template>
            <p v-else class="wechat-note">当前账号仅可查看，保存和校验需要接入管理权限。</p>
          </form>
        </section>
        <form v-if="confirmation" class="panel wechat-confirm" aria-label="二次确认" @submit.prevent="submit">
          <h2>{{ confirmation === 'save' ? '确认保存应用凭据' : '确认校验已保存配置' }}</h2>
          <p>{{ confirmation === 'save' ? '本次修改影响后续微信身份登录。请核对 AppID 与密钥操作。' : '只校验已保存配置，不使用尚未保存的输入，也不会执行真实用户登录。' }}</p>
          <label for="wechat-confirm-password">当前账号密码<ElInput id="wechat-confirm-password" v-model="password" type="password" autocomplete="current-password" :disabled="busy" /></label>
          <div class="wechat-actions"><ElButton type="primary" native-type="submit" :loading="busy" :disabled="busy || !password">{{ confirmation === 'save' ? '确认保存' : '确认校验' }}</ElButton><ElButton :disabled="busy" @click="cancel">取消</ElButton></div>
        </form>
        <section class="panel wechat-check" aria-labelledby="wechat-check-title">
          <div class="panel-heading"><div><h2 id="wechat-check-title">凭据校验</h2><p>校验仅证明应用凭据可调用微信平台，不代表登录、支付或消息已验收。</p></div><ElButton v-if="canManage" :disabled="!testAllowed || !!confirmation" @click="begin('test')">校验已保存配置</ElButton></div>
          <div v-if="config.lastCheck" class="wechat-check-result" :class="config.lastCheck.status === 'SUCCESS' ? 'is-success' : 'is-warning'" role="status"><strong>{{ checkTitles[config.lastCheck.status] }}</strong><p>{{ checkMessages[config.lastCheck.code] }}</p><span>配置修订 {{ config.lastCheck.revision }} · {{ new Date(config.lastCheck.checkedAt).toLocaleString('zh-CN', { hour12: false }) }}</span></div>
          <p v-else class="wechat-note">尚未校验。保存配置后，请发起凭据校验。</p>
          <p v-if="dirty" class="wechat-note">请先保存修改，再校验已保存配置。</p>
          <dl class="wechat-metadata"><div><dt>支付 AppID 对照</dt><dd>{{ { NOT_CONFIGURED: '部署配置未填写', MATCHED: '与部署配置一致', MISMATCHED: '与部署配置不一致' }[config.paymentAppIdStatus] }}<small>仅对照 AppID，不代表支付验证。</small></dd></div><div><dt>订阅消息</dt><dd>尚未核验<small>凭据保存或校验不会开放消息发送。</small></dd></div></dl>
        </section>
        <section class="wechat-help" aria-labelledby="wechat-help-title"><h2 id="wechat-help-title">接入步骤</h2><ol><li>从微信公众平台取得当前小程序的 AppID 与 AppSecret。</li><li>保存配置后校验凭据；如提示 IP 未获允许，请检查服务器 IP 白名单。</li><li>配置小程序合法请求域名，再在真实小程序中验证登录。</li><li>真实登录与真机验收仍需单独完成；支付、订阅消息和代码发布按各自流程验证。</li></ol></section>
      </template>
    </template>
  </section>
</template>
