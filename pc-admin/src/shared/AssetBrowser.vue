<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { api, type Account } from '../api'
import { assetQuery, selectionError, validateAssetFile, type AssetKind, type AssetPage, type LibraryAsset } from './media-library'
import './assets.css'
const props = defineProps<{ canRead: boolean; canUpload: boolean; kind?: AssetKind; square?: boolean; excludedIds?: string[]; selecting?: boolean }>()
const account = inject<Ref<Account | null>>('admin-account')
const canDelete = computed(() => !!account?.value?.permissionCodes.includes('asset.delete'))
const deleting = ref(false)
const deleteAsset = ref<LibraryAsset | null>(null)
const deleteError = ref('')
const emit = defineEmits<{ select: [asset: LibraryAsset] }>()
const kindFilter = ref<AssetKind | ''>(props.kind || '')
const q = ref('')
const binding = ref('')
const page = ref(1)
const result = ref<AssetPage | null>(null)
const loading = ref(false)
const uploading = ref(false)
const error = ref('')
const notice = ref('')
const broken = ref<string[]>([])
const uploadKind = ref<AssetKind>(props.kind || 'IMAGE')
const references = ref<{ domain: string; objectId: string; label: string; role: string; version?: number; state: string }[]>([])
const referenceAsset = ref<LibraryAsset | null>(null)
const referenceLoading = ref(false)
const referenceError = ref('')
const referencePage = ref(1)
const referenceTotal = ref(0)
let generation = 0
let referenceGeneration = 0
let alive = true
const canAccess = computed(() => props.canRead || props.canUpload)
function invalid(asset: LibraryAsset) { return props.selecting ? selectionError(asset, { kind: props.kind || 'IMAGE', square: props.square, excludedIds: props.excludedIds }) : '' }
async function load(reset = false) {
  if (!canAccess.value) return
  if (reset) page.value = 1
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    const data = await api<AssetPage>(`/assets?${assetQuery({ kind: kindFilter.value, q: q.value, binding: binding.value, page: page.value, square: props.square }, props.canRead)}`)
    if (alive && current === generation) { result.value = data; broken.value = [] }
  } catch (reason) { if (alive && current === generation) error.value = reason instanceof Error ? reason.message : '素材读取失败，请重试。' }
  finally { if (alive && current === generation) loading.value = false }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || uploading.value || !props.canUpload) return
  const problem = validateAssetFile(file, uploadKind.value)
  if (problem) { error.value = problem; return }
  uploading.value = true
  error.value = ''
  notice.value = ''
  try {
    const body = new FormData()
    body.append('file', file)
    body.append('kind', uploadKind.value)
    await api('/assets', { method: 'POST', body })
    if (alive) { notice.value = '素材已上传，可从列表选择。'; await load(true) }
  } catch (reason) { if (alive) error.value = reason instanceof Error ? reason.message : '上传失败，请重新选择文件重试。' }
  finally { if (alive) uploading.value = false }
}
async function removeAsset() {
  const asset = deleteAsset.value
  if (!asset || !canDelete.value || deleting.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await api(`/assets/${encodeURIComponent(asset.assetId)}`, { method: 'DELETE' })
    if (alive) { deleteAsset.value = null; notice.value = '素材已删除。'; await load() }
  } catch (reason) { if (alive) deleteError.value = reason instanceof Error ? reason.message : '删除失败，请重试。' }
  finally { if (alive) deleting.value = false }
}
async function loadReferences(asset: LibraryAsset, reset = true) {
  if (!props.canRead) return
  if (reset) { referencePage.value = 1; references.value = [] }
  referenceAsset.value = asset
  referenceLoading.value = true
  referenceError.value = ''
  const current = ++referenceGeneration
  try {
    const data = await api<{ items: typeof references.value; total: number }>(`/assets/${encodeURIComponent(asset.assetId)}/references?page=${referencePage.value}&pageSize=20`)
    if (alive && current === referenceGeneration) { references.value = data.items; referenceTotal.value = data.total }
  } catch (reason) { if (alive && current === referenceGeneration) referenceError.value = reason instanceof Error ? reason.message : '引用读取失败，请重试。' }
  finally { if (alive && current === referenceGeneration) referenceLoading.value = false }
}
function select(asset: LibraryAsset) { if (!loading.value && !uploading.value && !error.value && !invalid(asset) && !broken.value.includes(asset.assetId)) emit('select', asset) }
function turnPage(value: number) { page.value = value; void load() }
onMounted(() => void load())
onBeforeUnmount(() => { alive = false; generation++; referenceGeneration++ })
</script>
<template>
  <div class="asset-browser">
    <p v-if="!canAccess" role="alert">当前账号没有素材读取或上传权限。</p>
    <template v-else>
      <p class="help-text">{{ canRead ? '可浏览素材并查看草稿、线上和历史引用。' : '当前仅可浏览本人上传且尚未绑定的素材。' }} {{ square ? '此素材位要求 1:1 方形图片。' : '' }}</p>
      <form class="asset-toolbar" @submit.prevent="load(true)">
        <label>搜索文件名<el-input v-model="q" maxlength="120" placeholder="输入文件名" clearable /></label>
        <label v-if="!kind">素材类型<el-select v-model="kindFilter" placeholder="全部类型"><el-option label="全部类型" value="" /><el-option label="图片" value="IMAGE" /><el-option label="视频" value="VIDEO" /><el-option label="GIF" value="GIF" /></el-select></label>
        <label v-if="canRead">引用状态<el-select v-model="binding" placeholder="全部状态"><el-option label="全部状态" value="" /><el-option label="已引用" value="BOUND" /><el-option label="未引用" value="UNBOUND" /></el-select></label>
        <el-button native-type="submit" :loading="loading">查询</el-button>
      </form>
      <div v-if="canUpload" class="asset-upload-row">
        <label v-if="!kind">上传类型<el-select v-model="uploadKind" :disabled="uploading"><el-option label="图片" value="IMAGE" /><el-option label="视频" value="VIDEO" /><el-option label="GIF" value="GIF" /></el-select></label>
        <label class="secondary-button asset-upload">{{ uploading ? '上传中…' : '上传素材' }}<input type="file" :accept="uploadKind === 'VIDEO' ? 'video/mp4' : uploadKind === 'GIF' ? 'image/gif' : 'image/jpeg,image/png'" :disabled="uploading" @change="upload" /></label>
        <small>{{ uploadKind === 'VIDEO' ? 'MP4 · 最多 50 MiB' : uploadKind === 'GIF' ? 'GIF · 最多 10 MiB' : 'JPG / PNG · 最多 10 MiB' }}</small>
      </div>
      <p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="error" class="error" role="alert">{{ error }} <el-button text @click="load()">重新读取</el-button></p>
      <p v-if="loading" role="status">正在读取素材…</p>
      <p v-else-if="!error && !result?.items.length" class="asset-empty">暂无符合条件的素材。可调整筛选{{ canUpload ? '或上传新素材' : '' }}。</p>
      <ul v-if="result?.items.length" class="asset-grid" :aria-busy="loading">
        <li v-for="asset in result.items" :key="asset.assetId" class="asset-item">
          <div class="asset-preview"><p v-if="asset.availability !== 'READY' || broken.includes(asset.assetId)">文件不可用</p><video v-else-if="asset.kind === 'VIDEO'" :src="asset.adminUrl" controls preload="metadata" :aria-label="asset.originalName" @error="broken = [...broken, asset.assetId]" /><img v-else :src="asset.adminUrl" :alt="asset.originalName" loading="lazy" @error="broken = [...broken, asset.assetId]" /></div>
          <strong class="asset-name" :title="asset.originalName">{{ asset.originalName }}</strong>
          <small>{{ asset.kind }} · {{ (asset.byteSize / 1024).toFixed(1) }} KiB{{ asset.width ? ` · ${asset.width} × ${asset.height}` : '' }}</small>
          <small>{{ asset.bindingStatus === 'BOUND' ? '已引用' : '未引用' }} · {{ new Date(asset.createdAt).toLocaleString('zh-CN') }}</small>
          <div class="asset-actions"><el-button v-if="selecting" type="primary" :disabled="loading || uploading || !!error || !!invalid(asset) || broken.includes(asset.assetId)" @click="select(asset)">选择素材</el-button><el-button v-if="canRead" text :disabled="loading" @click="loadReferences(asset)">查看引用</el-button><el-button v-if="canDelete && asset.bindingStatus === 'UNBOUND'" text type="danger" :disabled="loading || uploading || deleting" @click="deleteAsset = asset; deleteError = ''">删除</el-button></div>
          <small v-if="selecting && invalid(asset)" class="error">{{ invalid(asset) }}</small>
        </li>
      </ul>
      <el-pagination v-if="result && result.total > 20" class="asset-pagination" layout="prev, pager, next" :total="result.total" :page-size="20" :current-page="page" :disabled="loading" @current-change="turnPage" />
      <p class="help-text">引用素材会保留。未引用素材由服务端按保留期清理；移除页面或商品中的素材不等于删除文件。</p>
    </template>
    <el-dialog class="asset-dialog" :model-value="!!deleteAsset" title="删除未引用素材" width="460px" :close-on-click-modal="false" :close-on-press-escape="!deleting" :show-close="!deleting" @update:model-value="(value: boolean) => { if (!value && !deleting) deleteAsset = null }">
      <p>确认删除“{{ deleteAsset?.originalName }}”？文件删除后不可恢复，服务端会再次核实引用状态。</p><p v-if="deleteError" class="error" role="alert">{{ deleteError }}</p>
      <template #footer><el-button :disabled="deleting" @click="deleteAsset = null">取消</el-button><el-button type="danger" :loading="deleting" @click="removeAsset">确认删除</el-button></template>
    </el-dialog>
    <el-dialog class="asset-dialog" :model-value="!!referenceAsset" title="素材引用" width="640px" @update:model-value="(value: boolean) => { if (!value) { referenceAsset = null; referenceGeneration++ } }">
      <p>{{ referenceAsset?.originalName }}</p>
      <small class="asset-checksum">SHA-256：{{ referenceAsset?.sha256 }}</small>
      <p v-if="referenceLoading" role="status">正在读取引用…</p><p v-else-if="referenceError" class="error" role="alert">{{ referenceError }} <el-button text @click="referenceAsset && loadReferences(referenceAsset, false)">重试</el-button></p><p v-else-if="!references.length">当前未被引用。</p>
      <ul v-else class="asset-reference-list"><li v-for="(item,index) in references" :key="index"><strong>{{ item.label }}</strong><span>{{ item.domain }} · {{ item.role }} · {{ ({ DRAFT: '草稿', CURRENT: '当前线上', HISTORY: '历史版本', BOUND: '已绑定' } as Record<string,string>)[item.state] || item.state }}{{ item.version !== undefined ? ` · 版本 ${item.version}` : '' }}</span><small>{{ item.objectId }}</small></li></ul>
      <el-pagination v-if="referenceTotal > 20" layout="prev, pager, next" :total="referenceTotal" :page-size="20" :current-page="referencePage" :disabled="referenceLoading" @current-change="(value: number) => { referencePage = value; if (referenceAsset) loadReferences(referenceAsset, false) }" />
    </el-dialog>
  </div>
</template>
