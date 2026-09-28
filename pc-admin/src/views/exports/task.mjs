const STATUS_TEXT = Object.freeze({
  PENDING: '等待生成',
  RUNNING: '正在生成',
  READY: '可下载',
  FAILED: '生成失败',
  EXPIRED: '已过期',
})

export const exportStatusText = status => STATUS_TEXT[status] || '状态待核实'

export function canDownloadExport(task, now = Date.now()) {
  const expiry = typeof task.expiresAt === 'string' ? Date.parse(task.expiresAt) : NaN
  return task.status === 'READY' && Number.isFinite(expiry) && now < expiry
}

export const isDefinitiveExportError = status => [400, 401, 403, 404, 409, 422, 429].includes(status)

export function exportFileName(kind, taskId) {
  const prefix = kind === 'AUDIT' ? 'audit' : 'business'
  const safeId = String(taskId).replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 80)
  return `${prefix}-export-${safeId}.csv`
}
