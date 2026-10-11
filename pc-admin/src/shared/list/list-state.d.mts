export type StatusTone = 'success' | 'processing' | 'pending' | 'danger' | 'neutral'
export function statusTone(status: unknown): StatusTone
export function pageCount(total: number, size: number): number
export function pageWindow<T>(rows: readonly T[], page: number, size: number): {items: T[]; page: number; pages: number; total: number}
export function selectedOnPage(ids: readonly string[], allowedIds: readonly string[]): string[]
export function toggleSelection(ids: readonly string[], id: string, checked: boolean, allowedIds: readonly string[]): string[]
export function filterLoadedRows<T>(rows: readonly T[], keyword: unknown, values: (row: T) => unknown[]): T[]
