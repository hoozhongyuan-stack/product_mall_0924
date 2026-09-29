<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ApiError, api, confirmedWrite } from '../api'

type Check = { code: string; status: 'PASS' | 'BLOCKED' | 'UNVERIFIED' }
type KeyStatus = { configured: boolean; revision: number; appId: string | null }
type Readiness = { appId: string | null; versionId: string | null; egressIp: string | null; developerAppId: string | null;
  uploadKey: KeyStatus; developerUploadKey: KeyStatus; checks: Check[] }
type Version = { versionId: string; versionLabel: string; storageStatus: string }
type Platform = { componentAppId: string; developerAppId: string; redirectUri: string;
  configured: boolean; ticketReceived: boolean; revision: number }
type Upload = { taskId: string; versionId: string; version: string; channel: string; status: string;
  failureCode: string; resolutionNote: string; reviewAvailable: boolean; createdAt: string }
type Review = { taskId: string; uploadTaskId: string; version: string; auditId: number | null;
  status: string; reason: string; failureCode: string; resolutionNote: string; createdAt: string }
type Category = { first_class: string; second_class: string; first_id: string | number;
  second_id: string | number }

const props = defineProps<{ readiness: Readiness | null; versions: Version[];
  canManage: boolean; canManagePlatform: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const platform = ref<Platform | null>(null)
const platformFields = ref({ componentAppId: '', developerAppId: '', redirectUri: '',
  componentAppSecret: '', messageToken: '', encodingAesKey: '' })
const platformPassword = ref('')
const developerFile = ref<File | null>(null)
const developerPassword = ref('')
const uploads = ref<Upload[]>([])
const reviews = ref<Review[]>([])
const categories = ref<Category[]>([])
const selectedVersionId = ref('')
const directVersion = ref('')
const directDescription = ref('')
const directPassword = ref('')
const directAttempt = ref<{ fingerprint: string; key: string; taskId: string | null } | null>(null)
const uploadVersion = ref('')
const uploadDescription = ref('')
const uploadPassword = ref('')
const selectedUploadId = ref('')
const selectedCategory = ref('')
const reviewDescription = ref('')
const reviewPassword = ref('')
const releasePassword = ref('')
const resolutionTarget = ref('')
const resolutionNote = ref('')
const resolutionPassword = ref('')
const authorizationUrl = ref('')
const busy = ref('')
const notice = ref('')
const issue = ref('')
let timer: ReturnType<typeof setInterval> | undefined

const hasPassed = (code: string) => props.readiness?.checks.some(check =>
  check.code === code && check.status === 'PASS') === true
const selectedVersion = computed(() => props.versions.find(item => item.versionId === selectedVersionId.value))
const directUploadReady = computed(() => Boolean(props.readiness && selectedVersion.value?.storageStatus === 'READY'
  && ['APP_ID', 'UPLOAD_KEY'].every(hasPassed)
  && (selectedVersionId.value !== props.readiness.versionId
    || ['SOURCE_PACKAGE', 'RELEASE_CONFIG'].every(hasPassed))))
const uploadReady = computed(() => ['APP_ID', 'SOURCE_PACKAGE', 'RELEASE_CONFIG',
  'PLATFORM_INTEGRATION', 'DEVELOPER_UPLOAD_KEY', 'THIRD_PARTY_AUTH'].every(code =>
  hasPassed(code)))
const selectedUpload = computed(() => uploads.value.find(item => item.taskId === selectedUploadId.value))
const targetAppId = computed(() => props.readiness?.appId || '')
const directFingerprint = computed(() => JSON.stringify([targetAppId.value, selectedVersionId.value,
  directVersion.value, directDescription.value]))
const directRepeatBlocked = computed(() => {
  const attempt = directAttempt.value
  if (!attempt?.taskId || attempt.fingerprint !== directFingerprint.value) return false
  const task = uploads.value.find(item => item.taskId === attempt.taskId)
  return !task || !['FAILED', 'RESOLVED'].includes(task.status)
})

function uploadStatusLabel(status: string) {
  return ({ PENDING: '等待上传', RUNNING: '正在上传', SUCCEEDED: '微信已接收',
    FAILED: '上传失败', UNKNOWN: '结果待核查', RESOLVED: '已人工关闭' } as Record<string, string>)[status] || '状态待核查'
}

function reviewStatusLabel(status: string) {
  return ({ SUBMITTING: '正在提审', SUBMITTED: '已提交微信审核', REVIEWING: '微信审核中',
    APPROVED: '微信审核通过', REJECTED: '微信审核未通过', FAILED: '提审失败',
    UNKNOWN: '提审结果待核查', RELEASING: '正在发布', RELEASE_UNKNOWN: '发布结果待核查',
    RELEASE_REQUESTED: '微信已接受发布请求', CLOSED_UNVERIFIED: '已人工关闭' } as Record<string, string>)[status] || '状态待核查'
}

watch(() => props.readiness?.versionId, value => {
  if (!selectedVersionId.value) selectedVersionId.value = value || ''
}, { immediate: true })

async function loadPlatform() {
  platform.value = await api<Platform>('/integrations/wechat-open-platform')
  if (platform.value) {
    platformFields.value.componentAppId = platform.value.componentAppId || ''
    platformFields.value.developerAppId = platform.value.developerAppId || ''
    platformFields.value.redirectUri = platform.value.redirectUri || ''
  }
}

async function loadRecords() {
  const [uploadPage, reviewPage] = await Promise.all([
    api<{ items: Upload[] }>('/code-release/uploads'),
    api<{ items: Review[] }>('/code-release/reviews'),
  ])
  uploads.value = uploadPage.items
  reviews.value = reviewPage.items
}

async function loadAll() {
  issue.value = ''
  try { await Promise.all([...(props.canManagePlatform ? [loadPlatform()] : []), loadRecords()]) }
  catch { issue.value = '发布流程状态读取失败，请重新读取。' }
}

async function execute(name: string, operation: () => Promise<void>) {
  if (busy.value) return
  busy.value = name
  issue.value = ''
  notice.value = ''
  try { await operation() }
  catch (reason) {
    issue.value = reason instanceof ApiError && reason.status < 500 ? reason.message :
      '操作结果待核对。请重新读取任务和微信平台状态后再决定下一步。'
  }
  finally { busy.value = '' }
}

async function savePlatform() {
  if (!platform.value || !platformPassword.value) return
  await execute('platform', async () => {
    await confirmedWrite<Platform>('wechat.integration.update', platformPassword.value,
      '/integrations/wechat-open-platform', 'PUT', {
        ...platformFields.value, expectedRevision: platform.value!.revision,
      }, 'wechat-open-platform', platform.value!.revision)
    platformFields.value.componentAppSecret = ''
    platformFields.value.messageToken = ''
    platformFields.value.encodingAesKey = ''
    platformPassword.value = ''
    await loadPlatform()
    emit('refresh')
    notice.value = '第三方平台凭据已保存。收到微信验证票据后再检查授权。'
  })
}

async function beginAuthorization() {
  if (!targetAppId.value) return
  await execute('authorization', async () => {
    const result = await api<{ url: string }>('/integrations/wechat-open-platform/authorize', {
      method: 'POST', body: JSON.stringify({ targetAppId: targetAppId.value }),
    })
    authorizationUrl.value = result.url
    notice.value = '授权链接已生成。请由目标小程序管理员打开并确认授权，然后重新检查条件。'
  })
}

function chooseDeveloperKey(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  developerFile.value = file && file.name.endsWith('.key') && file.size > 0 && file.size <= 16384 ? file : null
  if (!developerFile.value) {
    input.value = ''
    issue.value = '请选择不超过 16 KiB 的 .key 文件。'
  } else issue.value = ''
}

async function saveDeveloperKey() {
  const file = developerFile.value
  const key = props.readiness?.developerUploadKey
  const appId = props.readiness?.developerAppId
  if (!file || !key || !appId || !developerPassword.value) return
  await execute('developer-key', async () => {
    await confirmedWrite<KeyStatus>('code.release.developer_upload_key', developerPassword.value,
      '/code-release/developer-upload-key', 'PUT', {
        appId, key: await file.text(), expectedRevision: key.revision,
      }, appId, key.revision)
    developerFile.value = null
    developerPassword.value = ''
    emit('refresh')
    notice.value = '开发小程序密钥已加密保存；实际可用性将在上传时确认。'
  })
}

async function submitDirectUpload() {
  const versionId = selectedVersionId.value
  const appId = targetAppId.value
  const revision = props.readiness?.uploadKey?.revision
  if (!versionId || !appId || revision === undefined || !directUploadReady.value ||
      !directPassword.value || directRepeatBlocked.value) return
  await execute('direct-upload', async () => {
    const fingerprint = directFingerprint.value
    const previous = directAttempt.value
    const key = previous?.fingerprint === fingerprint && !previous.taskId ? previous.key : crypto.randomUUID()
    directAttempt.value = { fingerprint, key, taskId: null }
    const result = await confirmedWrite<Upload>('code.release.upload', directPassword.value,
      '/code-release/uploads', 'POST', { versionId, version: directVersion.value,
        description: directDescription.value, channel: 'CI_DIRECT' },
      `${appId}:${versionId}`, revision,
      { 'Idempotency-Key': key })
    directAttempt.value = { fingerprint, key, taskId: result.taskId }
    directPassword.value = ''
    notice.value = `开发版本上传任务 ${result.taskId} 已创建，正在等待服务器执行。`
    try { await loadRecords() }
    catch { issue.value = '任务已创建，但记录刷新失败。请用页面上的任务号核查，重新读取记录后再操作。' }
  })
}

async function submitUpload() {
  const versionId = selectedVersionId.value
  const appId = targetAppId.value
  const revision = props.readiness?.developerUploadKey?.revision
  if (!versionId || !appId || revision === undefined || !uploadReady.value || !uploadPassword.value) return
  await execute('upload', async () => {
    const result = await confirmedWrite<Upload>('code.release.upload', uploadPassword.value,
      '/code-release/uploads', 'POST', { versionId, version: uploadVersion.value,
        description: uploadDescription.value, channel: 'DIRECT_COMMIT' },
      `${appId}:${versionId}:DIRECT_COMMIT`, revision,
      { 'Idempotency-Key': crypto.randomUUID() })
    uploadPassword.value = ''
    await loadRecords()
    notice.value = `上传任务 ${result.taskId} 已创建，正在等待执行。`
  })
}

async function loadCategories() {
  if (!selectedUpload.value?.reviewAvailable) return
  await execute('categories', async () => {
    const result = await api<{ items: Category[] }>('/code-release/categories')
    categories.value = result.items
    selectedCategory.value = ''
    notice.value = '微信审核类目已读取，请选择与小程序服务一致的类目。'
  })
}

async function submitReview() {
  const upload = selectedUpload.value
  const category = categories.value[Number(selectedCategory.value)]
  if (!upload?.reviewAvailable || !category || !reviewDescription.value || !reviewPassword.value) return
  await execute('review', async () => {
    const result = await confirmedWrite<Review>('code.release.review', reviewPassword.value,
      '/code-release/reviews', 'POST', { uploadTaskId: upload.taskId,
        itemList: [category], versionDesc: reviewDescription.value }, upload.taskId, 0,
      { 'Idempotency-Key': crypto.randomUUID() })
    reviewPassword.value = ''
    await loadRecords()
    notice.value = result.status === 'SUBMITTED' ? `已提交微信审核，审核单号 ${result.auditId}。` :
      '提审结果未知，请核查微信后台与任务记录。'
  })
}

async function refreshReview(review: Review) {
  await execute('refresh-review', async () => {
    await api<Review>(`/code-release/reviews/${review.taskId}/refresh`, {
      method: 'POST', body: '{}',
    })
    await loadRecords()
    notice.value = '微信审核状态已重新读取。'
  })
}

async function publish(review: Review) {
  const auditId = review.auditId
  if (review.status !== 'APPROVED' || !auditId || !releasePassword.value) return
  await execute('release', async () => {
    const result = await confirmedWrite<Review>('code.release.publish', releasePassword.value,
      `/code-release/reviews/${review.taskId}/release`, 'POST', {},
      review.taskId, auditId)
    releasePassword.value = ''
    await loadRecords()
    notice.value = result.status === 'RELEASE_REQUESTED' ?
      '微信已接受发布请求。请在微信后台和真实设备核验线上版本。' :
      '发布结果未知，请先到微信后台核对，勿重复操作。'
  })
}

async function resolveUnknown(kind: 'upload' | 'review', taskId: string) {
  if (resolutionTarget.value !== `${kind}:${taskId}` || resolutionNote.value.trim().length < 20 || !resolutionPassword.value) return
  await execute('resolve', async () => {
    await confirmedWrite(kind === 'upload' ? 'code.release.resolve_upload' : 'code.release.resolve_review',
      resolutionPassword.value, `/code-release/${kind === 'upload' ? 'uploads' : 'reviews'}/${taskId}/resolve`,
      'POST', { note: resolutionNote.value.trim() }, taskId, 0)
    resolutionTarget.value = ''
    resolutionNote.value = ''
    resolutionPassword.value = ''
    await loadRecords()
    notice.value = '当前任务已按核查说明人工关闭；关闭不表示微信已完成上传、提审或发布。'
  })
}

onMounted(() => {
  void loadAll()
  timer = setInterval(() => {
    if (!busy.value && uploads.value.some(item => ['PENDING', 'RUNNING'].includes(item.status))) {
      void loadRecords().catch(() => { issue.value = '任务轮询失败，请手动重新读取。' })
    }
  }, 5000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <section class="release-workflow" aria-labelledby="release-workflow-title">
    <div class="code-versions-section-heading"><h2 id="release-workflow-title">上传微信开发版本</h2><button class="secondary-button" type="button" :disabled="!!busy" @click="loadAll">重新读取记录</button></div>
    <p class="code-release-explanation">选择已同步到后台的代码版本，由服务器上传至当前小程序。需要目标小程序代码上传密钥及服务器出口 IP 白名单；不需要第三方平台授权。上传成功后，仍需另行提审和发布。</p>
    <p v-if="issue" class="notice" role="alert">{{ issue }}</p>
    <p v-if="notice" class="code-release-key-notice" role="status">{{ notice }}</p>

    <section v-if="canManage" class="panel release-workflow-card" aria-labelledby="direct-upload-title">
      <h3 id="direct-upload-title">从后台上传代码</h3>
      <p v-if="!directUploadReady">请先完成目标小程序 AppID、代码密钥与所选代码版本的准备条件。所选版本的配置会在提交时由服务器再次核对。</p>
      <p>服务器出口 IP：<strong>{{ readiness?.egressIp || '尚未由运维配置' }}</strong>。请在微信公众平台的小程序代码上传设置中将其加入白名单。此地址是部署配置值；密钥和 IP 是否被微信接受，以实际上传结果为准。</p>
      <form data-test="ci-direct-upload-form" class="release-form-grid" @submit.prevent="submitDirectUpload">
        <label>不可变代码版本<select v-model="selectedVersionId" data-test="ci-direct-version-id" required><option value="">请选择</option><option v-for="version in versions" :key="version.versionId" :value="version.versionId">{{ version.versionLabel }}{{ version.storageStatus === 'READY' ? '' : '（文件未就绪）' }}</option></select></label>
        <label>微信版本号<input v-model.trim="directVersion" data-test="ci-direct-version" maxlength="40" pattern="[0-9A-Za-z][0-9A-Za-z._-]*" required></label>
        <label class="wide">上传说明<input v-model.trim="directDescription" data-test="ci-direct-description" maxlength="100" required></label>
        <label>当前管理员密码<input v-model="directPassword" data-test="ci-direct-password" type="password" autocomplete="current-password" required></label>
        <button class="primary-button" type="submit" :disabled="!!busy || !directUploadReady || directRepeatBlocked">上传到微信开发版本</button>
      </form>
      <ul class="release-record-list"><li v-for="item in uploads.filter(item => item.channel === 'CI_DIRECT')" :key="item.taskId"><strong>{{ item.version }} · 开发版本</strong><span>{{ uploadStatusLabel(item.status) }}<template v-if="item.failureCode"> · {{ item.failureCode }}</template></span><small>{{ item.taskId }}</small><p v-if="item.resolutionNote">核查记录：{{ item.resolutionNote }}</p><button v-if="item.status === 'UNKNOWN'" class="secondary-button" type="button" @click="resolutionTarget = `upload:${item.taskId}`">核查后关闭未知上传</button><form v-if="resolutionTarget === `upload:${item.taskId}`" class="release-form-grid" @submit.prevent="resolveUnknown('upload', item.taskId)"><label class="wide">微信后台核查说明（至少 20 字）<input v-model="resolutionNote" minlength="20" maxlength="500" required></label><label>当前管理员密码<input v-model="resolutionPassword" type="password" autocomplete="current-password" required></label><button class="secondary-button" type="submit" :disabled="!!busy">关闭未知任务</button></form></li></ul>
    </section>

    <div class="code-versions-section-heading release-workflow-advanced"><h2>自动提审与发布</h2><span>需要微信第三方平台授权</span></div>
    <p class="code-release-explanation">以下流程使用第三方平台绑定的开发小程序，把代码上传到目标小程序待审核列表；微信审核通过后才能发起发布。</p>

    <section class="panel release-workflow-card" aria-labelledby="platform-title">
      <h3 id="platform-title">1. 第三方平台接入</h3>
      <p v-if="canManagePlatform">组件凭据仅保存在服务器，不在页面回显。当前：{{ platform?.configured ? '凭据已配置' : '凭据未配置' }}；验证票据{{ platform?.ticketReceived ? '已收到' : '未收到' }}。</p>
      <p v-else>第三方平台配置由拥有微信集成管理权限的账号维护；请查看上方条件检测。</p>
      <form v-if="canManagePlatform" class="release-form-grid" @submit.prevent="savePlatform">
        <label>组件 AppID<input v-model.trim="platformFields.componentAppId" autocomplete="off" required></label>
        <label>开发小程序 AppID<input v-model.trim="platformFields.developerAppId" autocomplete="off" required></label>
        <label class="wide">固定授权回调 HTTPS 地址<input v-model.trim="platformFields.redirectUri" type="url" autocomplete="off" required></label>
        <label>组件 AppSecret<input v-model="platformFields.componentAppSecret" type="password" autocomplete="new-password" required></label>
        <label>消息 Token<input v-model="platformFields.messageToken" type="password" autocomplete="new-password" required></label>
        <label>消息 EncodingAESKey<input v-model="platformFields.encodingAesKey" type="password" autocomplete="new-password" required></label>
        <label>当前管理员密码<input v-model="platformPassword" type="password" autocomplete="current-password" required></label>
        <button class="secondary-button" type="submit" :disabled="!!busy || !platform">保存平台配置</button>
      </form>
      <div v-if="canManagePlatform" class="release-inline-action"><button class="secondary-button" type="button" :disabled="!!busy || !platform?.configured || !platform?.ticketReceived || !targetAppId" @click="beginAuthorization">生成目标小程序授权链接</button><a v-if="authorizationUrl" :href="authorizationUrl" target="_blank" rel="noopener noreferrer">打开微信授权页</a></div>
    </section>

    <section v-if="canManage" class="panel release-workflow-card" aria-labelledby="developer-key-title">
      <h3 id="developer-key-title">2. 开发小程序上传密钥</h3>
      <p>开发小程序 {{ readiness?.developerAppId || '尚未配置' }}；密钥{{ readiness?.developerUploadKey?.configured ? '已配置' : '未配置' }}。此密钥与目标小程序直传密钥用途不同。</p>
      <form class="release-form-grid" @submit.prevent="saveDeveloperKey">
        <label class="wide">选择 .key 文件<input type="file" accept=".key,text/plain" :disabled="!!busy" @change="chooseDeveloperKey"></label>
        <label>当前管理员密码<input v-model="developerPassword" type="password" autocomplete="current-password" required></label>
        <button class="secondary-button" type="submit" :disabled="!!busy || !developerFile || !readiness?.developerAppId">加密保存密钥</button>
      </form>
    </section>

    <section v-if="canManage" class="panel release-workflow-card" aria-labelledby="upload-title">
      <h3 id="upload-title">3. 上传代码包</h3>
      <p v-if="!uploadReady">发布条件尚未全部满足，请先处理上方检查项。</p>
      <form data-test="direct-upload-form" class="release-form-grid" @submit.prevent="submitUpload">
        <label>不可变代码版本<select v-model="selectedVersionId" required><option value="">请选择</option><option v-for="version in versions" :key="version.versionId" :value="version.versionId">{{ version.versionLabel }}</option></select></label>
        <label>微信版本号<input v-model.trim="uploadVersion" data-test="upload-version" maxlength="40" pattern="[0-9A-Za-z][0-9A-Za-z._-]*" required></label>
        <label class="wide">上传说明<input v-model.trim="uploadDescription" data-test="upload-description" maxlength="100" required></label>
        <label>当前管理员密码<input v-model="uploadPassword" data-test="upload-password" type="password" autocomplete="current-password" required></label>
        <button class="primary-button" type="submit" :disabled="!!busy || !uploadReady">上传到微信待审核列表</button>
      </form>
      <ul class="release-record-list"><li v-for="item in uploads.filter(item => item.channel === 'DIRECT_COMMIT')" :key="item.taskId"><strong>{{ item.version }} · 待审核链路</strong><span>{{ uploadStatusLabel(item.status) }}<template v-if="item.failureCode"> · {{ item.failureCode }}</template></span><small>{{ item.taskId }}</small><p v-if="item.resolutionNote">核查记录：{{ item.resolutionNote }}</p><button v-if="item.status === 'UNKNOWN'" class="secondary-button" type="button" @click="resolutionTarget = `upload:${item.taskId}`">核查后关闭未知上传</button><form v-if="resolutionTarget === `upload:${item.taskId}`" class="release-form-grid" @submit.prevent="resolveUnknown('upload', item.taskId)"><label class="wide">微信后台核查说明（至少 20 字）<input v-model="resolutionNote" minlength="20" maxlength="500" required></label><label>当前管理员密码<input v-model="resolutionPassword" type="password" autocomplete="current-password" required></label><button class="secondary-button" type="submit" :disabled="!!busy">关闭未知任务</button></form></li></ul>
    </section>

    <section v-if="canManage" class="panel release-workflow-card" aria-labelledby="review-title">
      <h3 id="review-title">4. 提交微信审核</h3>
      <p>仅可选择已成功上传到待审核列表的任务。审核类目从微信实时读取。</p>
      <form data-test="review-form" class="release-form-grid" @submit.prevent="submitReview">
        <label class="wide">已上传任务<select v-model="selectedUploadId" data-test="review-upload" required><option value="">请选择</option><option v-for="item in uploads.filter(item => item.reviewAvailable)" :key="item.taskId" :value="item.taskId">{{ item.version }} · {{ item.taskId }}</option></select></label>
        <button data-test="load-categories" class="secondary-button" type="button" :disabled="!!busy || !selectedUpload?.reviewAvailable" @click="loadCategories">读取微信审核类目</button>
        <label>审核类目<select v-model="selectedCategory" data-test="review-category" required><option value="">请选择</option><option v-for="(item, index) in categories" :key="`${item.first_id}-${item.second_id}`" :value="String(index)">{{ item.first_class }} / {{ item.second_class }}</option></select></label>
        <label class="wide">版本说明<input v-model.trim="reviewDescription" data-test="review-description" maxlength="200" required></label>
        <label>当前管理员密码<input v-model="reviewPassword" data-test="review-password" type="password" autocomplete="current-password" required></label>
        <button class="primary-button" type="submit" :disabled="!!busy || !selectedUpload?.reviewAvailable || selectedCategory === ''">提交微信审核</button>
      </form>
    </section>

    <section class="panel release-workflow-card" aria-labelledby="release-title">
      <h3 id="release-title">5. 审核状态与发布</h3>
      <p>发布前会再次向微信核验该审核单已通过，且仍是最新审核通过版本。</p>
      <ul class="release-record-list"><li v-for="item in reviews" :key="item.taskId"><strong>{{ item.version }} · 审核单 {{ item.auditId || '待核对' }}</strong><span>{{ reviewStatusLabel(item.status) }}<template v-if="item.reason"> · {{ item.reason }}</template></span><small>{{ item.taskId }}</small><p v-if="item.resolutionNote">核查记录：{{ item.resolutionNote }}</p><div v-if="canManage" class="release-inline-action"><button v-if="item.auditId && ['SUBMITTED', 'REVIEWING', 'APPROVED', 'REJECTED'].includes(item.status)" class="secondary-button" type="button" :disabled="!!busy" @click="refreshReview(item)">刷新微信审核状态</button><template v-if="item.status === 'APPROVED'"><label>当前管理员密码<input v-model="releasePassword" data-test="release-password" type="password" autocomplete="current-password"></label><button data-test="publish-reviewed" class="primary-button" type="button" :disabled="!!busy || !releasePassword" @click="publish(item)">发布审核通过版本</button></template><button v-if="['SUBMITTING', 'UNKNOWN', 'RELEASING', 'RELEASE_UNKNOWN', 'RELEASE_REQUESTED', 'APPROVED'].includes(item.status)" class="secondary-button" type="button" @click="resolutionTarget = `review:${item.taskId}`">核查后关闭当前任务</button></div><form v-if="canManage && resolutionTarget === `review:${item.taskId}`" class="release-form-grid" @submit.prevent="resolveUnknown('review', item.taskId)"><label class="wide">微信后台核查说明（至少 20 字）<input v-model="resolutionNote" minlength="20" maxlength="500" required></label><label>当前管理员密码<input v-model="resolutionPassword" type="password" autocomplete="current-password" required></label><button class="secondary-button" type="submit" :disabled="!!busy">关闭当前任务</button></form></li></ul>
      <p v-if="!reviews.length">暂无提审记录。</p>
    </section>
  </section>
</template>
