import type { Asset } from './media'
export type AssetKind = Asset['kind']
export interface LibraryAsset extends Asset { originalName: string; createdAt: string; sha256: string; bindingStatus: 'BOUND' | 'UNBOUND'; availability: 'READY' | 'MISSING' | 'EXPIRED' }
export interface AssetPage { items: LibraryAsset[]; total: number; page: number; pageSize: number }
export interface SelectionRules { kind: AssetKind; square?: boolean; excludedIds?: string[] }
export interface AssetFilters { kind?: AssetKind | ''; q?: string; binding?: string; page: number; square?: boolean }
export function assetQuery(filters: AssetFilters, canRead: boolean): string {
  const query = new URLSearchParams({ page: String(filters.page), pageSize: '20' })
  if (filters.kind) query.set('kind', filters.kind)
  if (filters.q?.trim()) query.set('q', filters.q.trim())
  if (!canRead) query.set('binding', 'UNBOUND')
  else if (filters.binding) query.set('binding', filters.binding)
  if (filters.square) query.set('square', '1')
  return query.toString()
}
export function selectionError(asset: LibraryAsset, rules: SelectionRules): string {
  if (asset.availability !== 'READY') return '素材文件不可用，请重新上传。'
  if (asset.kind !== rules.kind) return '素材类型不匹配。'
  if (rules.square && (!asset.width || asset.width !== asset.height)) return '请选择 1:1 方形图片。'
  if (rules.excludedIds?.includes(asset.assetId)) return '该素材已选择，请选择其他素材。'
  return ''
}
export function validateAssetFile(file: { type: string; size: number }, kind: AssetKind): string {
  const types = { IMAGE: ['image/jpeg', 'image/png'], VIDEO: ['video/mp4'], GIF: ['image/gif'] }
  if (!types[kind].includes(file.type)) return '文件类型不匹配，请按所选类型上传。'
  if (!file.size || file.size > (kind === 'VIDEO' ? 50 : 10) * 1024 * 1024) return `文件须大于 0，且不超过 ${kind === 'VIDEO' ? 50 : 10} MiB。`
  return ''
}
export function selectionStillCurrent(expected: string, current: string): boolean { return expected === current }
