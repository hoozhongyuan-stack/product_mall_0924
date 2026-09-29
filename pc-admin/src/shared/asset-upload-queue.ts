import { computed, ref } from 'vue'
import { ApiError } from '../api'
import { validateAssetFile, type AssetKind } from './media-library'
import type { Asset } from './media'

export type UploadStatus = 'WAITING' | 'UPLOADING' | 'SUCCEEDED' | 'INVALID' | 'FAILED' | 'UNKNOWN'
export interface UploadRow {
  id: string
  file: File
  kind: AssetKind
  status: UploadStatus
  message: string
  assetId?: string
}
export const MAX_UPLOAD_FILES = 20

/** A bounded, serial queue. Transport failure is never proof that a write failed. */
export function createAssetUploadQueue(
  upload: (file: File, kind: AssetKind) => Promise<Asset>,
  authorized: () => boolean,
) {
  const rows = ref<UploadRow[]>([])
  const running = ref(false)
  let generation = 0
  let paused = false
  const completed = computed(() => rows.value.filter(row => row.status === 'SUCCEEDED').length)
  const hasPending = computed(() => running.value || rows.value.some(row => row.status === 'WAITING'))
  const hasWaiting = computed(() => rows.value.some(row => row.status === 'WAITING'))
  const totalBytes = computed(() => rows.value.reduce((total, row) => total + row.file.size, 0))

  function update(id: string, patch: Partial<UploadRow>) {
    rows.value = rows.value.map(row => row.id === id ? { ...row, ...patch } : row)
  }
  function add(files: readonly File[], kind: AssetKind) {
    if (!authorized()) return '当前没有上传权限。'
    if (running.value) return '请等待当前上传结束后再添加文件。'
    if (rows.value.length + files.length > MAX_UPLOAD_FILES) return `每批最多 ${MAX_UPLOAD_FILES} 个文件，请分批选择。`
    rows.value = [...rows.value, ...files.map(file => {
      const problem = validateAssetFile(file, kind)
      return { id: crypto.randomUUID(), file, kind, status: problem ? 'INVALID' as const : 'WAITING' as const, message: problem }
    })]
    return ''
  }
  function remove(id: string) {
    rows.value = rows.value.filter(row => row.id !== id || row.status === 'UPLOADING')
  }
  function retry(id: string) {
    if (!authorized() || running.value) return
    const row = rows.value.find(item => item.id === id)
    if (row?.status === 'FAILED') update(id, { status: 'WAITING', message: '' })
  }
  function clearFinished() {
    rows.value = rows.value.filter(row => row.status !== 'SUCCEEDED')
  }
  function pause() { paused = true }
  function invalidate() {
    generation += 1
    paused = true
    rows.value = []
    running.value = false
  }
  async function send(row: UploadRow, current: number) {
    update(row.id, { status: 'UPLOADING', message: '' })
    try {
      const saved = await upload(row.file, row.kind)
      if (current === generation) update(row.id, { status: 'SUCCEEDED', assetId: saved.assetId })
    } catch (reason) {
      if (current !== generation) return
      const rejected = reason instanceof ApiError && reason.status >= 400 && reason.status < 500 && reason.status !== 408
      update(row.id, rejected
        ? { status: 'FAILED', message: reason.message }
        : { status: 'UNKNOWN', message: '上传结果待确认，请先在素材列表核对文件。此次请求不会自动重传。' })
      // Stop after uncertainty, authentication changes, or throttling. Never hammer the service.
      if (!rejected || [401, 403, 429].includes((reason as ApiError).status)) paused = true
    }
  }
  async function start() {
    if (running.value || !authorized() || !hasWaiting.value) return
    const current = generation
    paused = false
    running.value = true
    try {
      let next = rows.value.find(row => row.status === 'WAITING')
      while (next && !paused && authorized() && current === generation) {
        await send(next, current)
        next = rows.value.find(row => row.status === 'WAITING')
      }
    } finally {
      if (current === generation) running.value = false
    }
  }
  return { rows, running, completed, hasPending, hasWaiting, totalBytes, add, remove, retry, clearFinished, pause, invalidate, start }
}
