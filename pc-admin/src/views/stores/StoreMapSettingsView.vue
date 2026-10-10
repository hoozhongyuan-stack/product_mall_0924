<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, confirmedWrite, type Account } from '../../api'
import './stores.css'
import StoreSettlementSettingsPanel from './StoreSettlementSettingsPanel.vue'
import StoreProfitRulesPanel from './StoreProfitRulesPanel.vue'
interface Credential { configured: boolean; tail: string }
interface MapSettings {
  revision: number; managed: boolean; source: 'ENV' | 'MANAGED'; encryptionReady: boolean; available: boolean
  webServiceKey: Credential; jsApiKey: Credential; jsSecurityCode: Credential
}
const props = defineProps<{ account: Account }>()
const fields = [
  { key: 'webServiceKey', label: '高德地图（Web 服务）', help: '用于后台地址搜索与坐标解析。' },
  { key: 'jsApiKey', label: '高德地图（Web 端 JS API）', help: '保存供后续 Web 地图集成使用；需与 Web 端安全密钥一起配置。' },
  { key: 'jsSecurityCode', label: '高德地图（Web 端安全密钥）', help: '与 JS API Key 配套使用。' },
] as const
const config = ref<MapSettings | null>(null)
const secrets = ref({ webServiceKey: '', jsApiKey: '', jsSecurityCode: '' })
const password = ref(''), error = ref(''), notice = ref('')
const loading = ref(false), busy = ref(false), confirming = ref(false), needsRead = ref(false)
let epoch = 0
const canManage = computed(() => props.account.permissionCodes.includes('stores.manage'))
const dirty = computed(() => Object.values(secrets.value).some(value => !!value.trim()))
const issue = computed(() => {
  if (!config.value?.encryptionReady) return '后台加密存储尚未就绪，请联系系统管理员配置后再保存。'
  if (!secrets.value.webServiceKey.trim() && !(config.value.available && config.value.webServiceKey.configured)) return '请填写 Web 服务 Key。'
  const js = !!secrets.value.jsApiKey.trim() || (config.value.available && config.value.jsApiKey.configured)
  const security = !!secrets.value.jsSecurityCode.trim() || (config.value.available && config.value.jsSecurityCode.configured)
  return js !== security ? 'JS API Key 与 Web 端安全密钥需要一起配置。' : ''
})
const saveAllowed = computed(() => canManage.value && config.value && dirty.value && !issue.value && !needsRead.value && !loading.value && !busy.value)
function clearInputs() { secrets.value = { webServiceKey: '', jsApiKey: '', jsSecurityCode: '' }; password.value = ''; confirming.value = false }
async function load() {
  if (!canManage.value || busy.value) return
  const current = ++epoch
  clearInputs(); loading.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api<MapSettings>('/stores/map-settings')
    if (current === epoch) { config.value = result; needsRead.value = false }
  } catch { if (current === epoch) { config.value = null; error.value = '读取地图配置失败，请重新读取后重试。' } }
  finally { if (current === epoch) loading.value = false }
}
function begin() { if (saveAllowed.value) { confirming.value = true; password.value = ''; error.value = ''; notice.value = '' } }
async function save() {
  if (!saveAllowed.value || !confirming.value || !password.value || !config.value) return
  const current = ++epoch, revision = config.value.revision
  const body = { expectedRevision: revision, ...Object.fromEntries(Object.entries(secrets.value).map(([key, value]) => [key, value.trim()])) }
  busy.value = true; error.value = ''
  try {
    const result = await confirmedWrite<MapSettings>('stores.map.configure', password.value, '/stores/map-settings', 'PUT', body, 'amap', revision)
    if (current === epoch) {
      if (result.revision !== revision + 1) throw new Error('Unexpected result')
      config.value = result; notice.value = '配置已保存。请到前置仓详情验证地址搜索；保存不代表高德服务已验证。'
    }
  } catch { if (current === epoch) { error.value = '保存未确认，请重新读取配置，核对当前状态后再操作。'; needsRead.value = true } }
  finally { if (current === epoch) { busy.value = false; clearInputs() } }
}
watch(() => JSON.stringify([props.account.accountId, props.account.permissionCodes]), () => {
  epoch++; clearInputs(); config.value = null; busy.value = false; loading.value = false; error.value = ''; notice.value = ''; void load()
})
onMounted(load)
onUnmounted(() => { epoch++; clearInputs() })
</script>
<template>
  <section class="page-content stores-page">
    <header class="page-heading"><div><h1>前置仓设置</h1><p>配置地址搜索、高德 Web 地图集成参数与新订单售后期。</p></div><button class="secondary-button" :disabled="loading || busy" @click="load">重新读取</button></header>
    <p v-if="!canManage" role="alert">当前账号没有地图配置权限。</p>
    <p v-if="loading" role="status">正在读取地图配置…</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <form v-if="config && canManage" class="panel store-section" aria-label="高德参数配置" @submit.prevent="begin">
      <h2>定位 API 参数</h2><p v-if="!config.available" role="alert">当前地图凭据不可用，请重新填写 Web 服务 Key 后保存恢复。Web 地图凭据可成对选填。</p><p>留空保留当前参数。已保存的密钥只显示配置状态和末四位，不会回显完整内容。</p>
      <p>配置来源：{{ config.source === 'MANAGED' ? '后台托管' : '环境配置' }} · 修订 {{ config.revision }}</p>
      <div class="store-form">
        <label v-for="field in fields" :key="field.key" class="wide" :for="`amap-${field.key}`">{{ field.label }}
          <input :id="`amap-${field.key}`" v-model="secrets[field.key]" type="password" autocomplete="new-password" maxlength="128" :disabled="busy || loading || confirming" placeholder="填写新参数，留空保留当前参数" />
          <small>{{ field.help }} {{ config[field.key].configured ? `已配置 · 末四位 ${config[field.key].tail}` : '未配置' }}</small>
        </label>
      </div>
      <p v-if="issue" role="status">{{ issue }}</p>
      <div class="store-actions"><button class="primary-button" type="submit" :disabled="!saveAllowed || confirming">保存配置</button></div>
    </form>
    <form v-if="confirming" class="panel store-section" aria-label="确认保存地图配置" @submit.prevent="save">
      <h2>确认保存地图配置</h2><p>修改后将用于前置仓定位。请输入当前账号密码确认本次操作。</p>
      <label for="amap-password">当前账号密码 <input id="amap-password" v-model="password" type="password" autocomplete="current-password" :disabled="busy" /></label>
      <div class="store-actions"><button class="primary-button" type="submit" :disabled="busy || !password">{{ busy ? '正在保存…' : '确认保存' }}</button><button class="secondary-button" type="button" :disabled="busy" @click="clearInputs">取消</button></div>
    </form>
    <StoreSettlementSettingsPanel :account="account" />
    <StoreProfitRulesPanel :account="account" />
  </section>
</template>
