<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
type Result = { code?: string; status: 'PASS' | 'BLOCKED' | 'UNVERIFIED'; detail: string;
  checkedAt: string; missingRequestDomains: string[] }
const props = defineProps<{ appId: string; versionId: string; credentialCheck?: { status: 'PASS' | 'BLOCKED' | 'UNVERIFIED'; detail?: string } }>()
const result = ref<Result | null>(null)
const loading = ref(false)
const error = ref('')
let generation = 0
function reset() { generation++; result.value = null; error.value = ''; loading.value = false }
watch(() => [props.appId, props.versionId], reset)
onUnmounted(() => { generation++ })
async function check() {
  if (loading.value || !props.appId || !props.versionId) return
  const request = ++generation
  loading.value = true; result.value = null; error.value = ''
  try {
    const response = await api<Result>('/code-release/domain-check', {
      method: 'POST', body: JSON.stringify({ versionId: props.versionId }),
    })
    if (request === generation) result.value = response
  } catch {
    if (request === generation) error.value = '暂时无法检查域名，请稍后重试。'
  } finally { if (request === generation) loading.value = false }
}
</script>
<template>
  <section class="code-domain-check" aria-label="微信服务器域名检查">
    <div class="release-inline-action">
      <strong>服务器域名</strong>
      <span v-if="!loading && !error" class="code-release-check-status" :class="`is-${result?.status.toLowerCase() || 'unverified'}`">{{ result ? ({ PASS: '已满足', BLOCKED: '未满足', UNVERIFIED: '无法验证' })[result.status] : '尚未检查' }}</span>
      <button class="secondary-button" type="button" :disabled="loading || !appId || !versionId" @click="check">{{ loading ? '正在检查…' : '检查所选代码包的域名' }}</button>
    </div>
    <p v-if="result && ['OK', 'REQUEST_DOMAIN_MISSING'].includes(result.code || '')">服务端凭据 AppSecret：本次微信查询鉴权已通过。</p>
    <p v-else-if="credentialCheck">服务端凭据 AppSecret：{{ ({ PASS: '已满足', BLOCKED: '未满足', UNVERIFIED: '无法验证' })[credentialCheck.status] }}。{{ credentialCheck.detail }}</p>
    <p>使用已配置的 AppSecret 查询微信域名。代码上传 IP 白名单由微信在实际上传时验证。</p>
    <div aria-live="polite" aria-atomic="true">
      <p v-if="error" role="alert">{{ error }}</p>
      <template v-if="result">
        <p>{{ result.detail }}</p>
        <p v-if="result.missingRequestDomains?.length">请在微信公众平台添加 request 合法域名：{{ result.missingRequestDomains.join('、') }}</p>
        <small v-if="result.checkedAt">检查时间：{{ new Date(result.checkedAt).toLocaleString('zh-CN', { hour12: false }) }}。修改微信配置后请重新检查。</small>
      </template>
    </div>
  </section>
</template>
