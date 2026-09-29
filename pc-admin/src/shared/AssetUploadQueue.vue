<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { ElButton, ElOption, ElSelect, ElUpload, type UploadFile } from 'element-plus'
import 'element-plus/theme-chalk/el-upload.css'
import { api, type Account } from '../api'
import type { Asset } from './media'
import type { AssetKind } from './media-library'
import { createAssetUploadQueue, MAX_UPLOAD_FILES, type UploadStatus } from './asset-upload-queue'

const props = defineProps<{ canUpload: boolean; kind?: AssetKind }>()
const emit = defineEmits<{ refresh: []; pending: [value: boolean] }>()
const account = inject<Ref<Account | null>>('admin-account')
const uploadKind = ref<AssetKind>(props.kind || 'IMAGE')
const error = ref('')
const stopping = ref(false)
let epoch = 0
let alive = true
const queue = createAssetUploadQueue(async (file, kind) => {
  const body = new FormData()
  body.append('file', file)
  body.append('kind', kind)
  return api<Asset>('/assets', { method: 'POST', body })
}, () => props.canUpload)
const { rows, running, completed, hasPending, hasWaiting, totalBytes } = queue
const accept = computed(() => uploadKind.value === 'VIDEO' ? 'video/mp4' : uploadKind.value === 'GIF' ? 'image/gif' : 'image/jpeg,image/png')
const statuses: Record<UploadStatus, string> = { WAITING: '等待上传', UPLOADING: '上传中', SUCCEEDED: '上传成功', INVALID: '校验未通过', FAILED: '上传失败', UNKNOWN: '结果待确认' }
watch(hasPending, value => emit('pending', value), { immediate: true })
watch(() => [account?.value?.accountId, props.canUpload, props.kind], () => {
  epoch += 1
  queue.invalidate()
  error.value = ''
  uploadKind.value = props.kind || 'IMAGE'
})
function selectFile(file: UploadFile) {
  if (file.raw) error.value = queue.add([file.raw], uploadKind.value)
}
async function start() {
  const current = epoch
  stopping.value = false
  await queue.start()
  if (alive && current === epoch) emit('refresh')
}
function retry(id: string) { queue.retry(id); void start() }
function pause() { stopping.value = true; queue.pause() }
function beforeUnload(event: BeforeUnloadEvent) {
  if (!hasPending.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => {
  alive = false
  epoch += 1
  queue.invalidate()
  emit('pending', false)
  window.removeEventListener('beforeunload', beforeUnload)
})
</script>

<template>
  <section class="asset-upload-panel" aria-label="批量上传素材">
    <div class="asset-upload-heading">
      <div><h2>上传素材</h2><p>每批最多 {{ MAX_UPLOAD_FILES }} 个文件，逐个上传并保留成功结果。</p></div>
      <label v-if="!kind">文件类型<ElSelect v-model="uploadKind" :disabled="running" aria-label="上传文件类型"><ElOption label="图片" value="IMAGE" /><ElOption label="视频" value="VIDEO" /><ElOption label="GIF" value="GIF" /></ElSelect></label>
    </div>
    <ElUpload drag multiple :auto-upload="false" :show-file-list="false" :file-list="[]" :limit="MAX_UPLOAD_FILES"
      :accept="accept" :disabled="running || !canUpload" :on-change="selectFile"
      :on-exceed="() => error = `每批最多 ${MAX_UPLOAD_FILES} 个文件，请分批选择。`">
      <strong>选择文件，或拖拽到这里</strong>
      <p>{{ uploadKind === 'VIDEO' ? 'MP4，每个不超过 50 MiB' : uploadKind === 'GIF' ? 'GIF，每个不超过 10 MiB' : 'JPG / PNG，每个不超过 10 MiB' }}</p>
    </ElUpload>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="rows.length">
      <div class="asset-queue-summary"><span role="status">成功 {{ completed }} / {{ rows.length }} 个 · 共 {{ (totalBytes / 1024 / 1024).toFixed(2) }} MiB</span><ElButton v-if="completed" text :disabled="running" @click="queue.clearFinished">清除成功记录</ElButton></div>
      <ul class="asset-queue-list" aria-label="上传队列">
        <li v-for="row in rows" :key="row.id" :data-state="row.status">
          <div class="asset-queue-file"><strong :title="row.file.name">{{ row.file.name }}</strong><small>{{ (row.file.size / 1024).toFixed(1) }} KiB</small><p v-if="row.message" class="asset-queue-message">{{ row.message }}</p></div>
          <span class="asset-queue-status">{{ statuses[row.status] }}</span>
          <div class="asset-queue-actions">
            <ElButton v-if="row.status === 'FAILED'" text :disabled="running || !canUpload" :aria-label="`重试 ${row.file.name}`" @click="retry(row.id)">重试</ElButton>
            <ElButton v-if="row.status === 'UNKNOWN'" text :disabled="running" @click="emit('refresh')">核对素材列表</ElButton>
            <ElButton v-if="row.status !== 'UPLOADING' && row.status !== 'SUCCEEDED'" text :aria-label="`移出队列 ${row.file.name}`" @click="queue.remove(row.id)">移出队列</ElButton>
          </div>
        </li>
      </ul>
      <div class="asset-queue-footer"><p>{{ stopping ? '当前请求结束后停止，成功文件会保留。' : '结果待确认的文件不会自动重传，请先在下方素材列表核对。' }}</p><ElButton v-if="running" :disabled="stopping" @click="pause">停止后续上传</ElButton><ElButton v-else type="primary" :disabled="!hasWaiting || !canUpload" @click="start">{{ completed ? '继续上传' : '开始上传' }}</ElButton></div>
    </template>
  </section>
</template>

<style scoped>
.asset-upload-panel{margin:20px 0 24px;padding-top:20px;border-top:1px solid var(--mall-color-border)}
.asset-upload-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:12px}
.asset-upload-heading h2{font-size:16px;margin:0 0 6px}.asset-upload-heading p,.asset-upload-panel :deep(.el-upload-dragger p){font-size:13px;color:var(--mall-color-muted);margin:0;line-height:1.6}
.asset-upload-heading label{display:flex;align-items:center;gap:10px;white-space:nowrap;font-size:13px}.asset-upload-heading :deep(.el-select){width:110px}
.asset-upload-panel :deep(.el-upload){width:100%}.asset-upload-panel :deep(.el-upload-dragger){padding:20px;border-radius:8px;background:var(--mall-color-surface)}.asset-upload-panel :deep(.el-upload-dragger strong){display:block;font-size:14px;margin-bottom:5px}
.asset-queue-summary{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:12px;font-size:13px;font-variant-numeric:tabular-nums}
.asset-queue-list{list-style:none;padding:0;margin:8px 0}.asset-queue-list li{display:grid;grid-template-columns:minmax(0,1fr) 90px auto;gap:16px;align-items:center;padding:12px 0;border-bottom:1px solid var(--mall-color-border)}
.asset-queue-file{min-width:0}.asset-queue-file strong{display:block;overflow-wrap:anywhere;font-size:13px}.asset-queue-file small{color:var(--mall-color-muted)}.asset-queue-message{font-size:13px;line-height:1.5;margin:5px 0 0;color:var(--mall-color-muted)}
.asset-queue-status{font-size:13px;white-space:nowrap}.asset-queue-list li[data-state="SUCCEEDED"] .asset-queue-status{color:var(--mall-color-success)}.asset-queue-list li[data-state="FAILED"] .asset-queue-status,.asset-queue-list li[data-state="INVALID"] .asset-queue-status{color:var(--mall-color-danger)}
.asset-queue-actions{display:flex;flex-wrap:wrap;justify-content:flex-end}.asset-queue-actions :deep(.el-button+.el-button){margin-left:0}.asset-queue-footer{display:flex;justify-content:space-between;align-items:center;gap:16px}.asset-queue-footer p{font-size:12px;color:var(--mall-color-muted);line-height:1.6;max-width:65ch}
@media(max-width:600px){.asset-upload-heading{align-items:flex-start;flex-direction:column}.asset-queue-list li{grid-template-columns:minmax(0,1fr) auto;gap:8px}.asset-queue-actions{grid-column:1/-1;justify-content:flex-start}.asset-queue-footer{align-items:stretch;flex-direction:column;gap:4px}}
</style>
