export type ExportKind = 'AUDIT' | 'BUSINESS'
export type ExportStatus = 'PENDING' | 'RUNNING' | 'READY' | 'FAILED' | 'EXPIRED'
export interface ExportTaskState { status: ExportStatus | string; expiresAt: string | null }
export function exportStatusText(status: string): string
export function canDownloadExport(task: ExportTaskState, now?: number): boolean
export function isDefinitiveExportError(status: number): boolean
export function exportFileName(kind: ExportKind, taskId: string): string
