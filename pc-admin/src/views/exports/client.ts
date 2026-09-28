import { ApiError } from '../../api'
import { exportFileName, type ExportKind } from './task.mjs'

interface ErrorEnvelope { error?: { code?: string; message?: string } }
export interface ExportFilters {
  from: string
  to: string
  actorId?: string
  actionCode?: string
  objectType?: string
  objectId?: string
  result?: string
}
export interface ExportTask {
  taskId: string
  kind: ExportKind
  status: string
  createdAt: string
  completedAt: string | null
  expiresAt: string | null
  fileBytes: number | null
  rowCount: number | null
  failureCode: string | null
  requestKey: string
  filters: ExportFilters
}

export async function downloadExportCsv(kind: ExportKind, taskId: string): Promise<void> {
  const response = await fetch(`/api/v1/admin/exports/${encodeURIComponent(taskId)}/download`, {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (response.status === 401) window.dispatchEvent(new Event('admin-session-expired'))
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as ErrorEnvelope
    throw new ApiError(payload.error?.message || '下载失败，请刷新任务状态后重试。', response.status, payload.error?.code || 'DOWNLOAD_FAILED')
  }
  if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('text/csv')) {
    throw new Error('服务返回的不是 CSV 文件，请刷新任务状态后重试。')
  }
  const blob = await response.blob()
  if (!blob.size) throw new Error('导出文件为空，请刷新任务状态后重试。')
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = exportFileName(kind, taskId)
  document.body.append(link)
  try {
    link.click()
  } finally {
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
