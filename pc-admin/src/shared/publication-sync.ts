export interface PublicationState {
  publishedRevision: number | null
  publishedVersionId: string | null
  publicationRevision: number
}
export interface PublishIntent {
  readonly base: string
  readonly objectId: string
  readonly key: string
  readonly body: { readonly expectedRevision: number; readonly expectedPublicationRevision: number }
}
export function publicationIntent(base: string, objectId: string, draft: { revision: number; publicationRevision?: number }, key: string): PublishIntent {
  const publicationRevision = draft.publicationRevision ?? 0
  if (!Number.isSafeInteger(draft.revision) || draft.revision < 1 || !Number.isSafeInteger(publicationRevision) || publicationRevision < 0) throw new Error('请重新读取有效的草稿与线上修订信息。')
  return Object.freeze({ base, objectId, key, body: Object.freeze({ expectedRevision: draft.revision, expectedPublicationRevision: publicationRevision }) })
}
export async function currentPublication(read: (path: string) => Promise<unknown>, base: string): Promise<PublicationState> {
  const history = await read(`${base}/versions?page=1&pageSize=1`) as { currentVersionId: string | null; publicationRevision: number }
  if (!Number.isSafeInteger(history.publicationRevision) || history.publicationRevision < 0 || (history.currentVersionId !== null && (typeof history.currentVersionId !== 'string' || !history.currentVersionId))) throw new Error('当前线上状态无效，请重新读取。')
  if (history.currentVersionId === null) return { publishedRevision: null, publishedVersionId: null, publicationRevision: history.publicationRevision }
  const live = await read(`${base}/versions/${encodeURIComponent(history.currentVersionId)}`) as { versionId: string; revision: number }
  if (live.versionId !== history.currentVersionId || !Number.isSafeInteger(live.revision) || live.revision < 1) throw new Error('当前线上版本详情无效，请重新读取。')
  return { publishedRevision: live.revision, publishedVersionId: history.currentVersionId, publicationRevision: history.publicationRevision }
}
export function applyCurrentPublication<T extends object>(draft: T, state: PublicationState): T & PublicationState {
  return { ...draft, ...state }
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function matchesPublishResult(result: unknown, body: PublishIntent['body']): boolean {
  if (!result || typeof result !== 'object') return false
  const value = result as { versionId?: unknown; revision?: unknown; publicationRevision?: unknown; draftRevision?: unknown }
  return typeof value.versionId === 'string' && uuid.test(value.versionId) && value.revision === body.expectedRevision && value.publicationRevision === body.expectedPublicationRevision + 1 && value.draftRevision === body.expectedRevision
}
export function parsePublishIntent(raw: string | null, base: string, objectId: string): PublishIntent | null {
  try {
    const value = JSON.parse(raw || 'null')
    if (!value || Object.keys(value).sort().join(',') !== 'base,body,key,objectId' || value.base !== base || value.objectId !== objectId || !uuid.test(value.key) || Object.keys(value.body).sort().join(',') !== 'expectedPublicationRevision,expectedRevision') return null
    return publicationIntent(base, objectId, { revision: value.body.expectedRevision, publicationRevision: value.body.expectedPublicationRevision }, value.key)
  } catch { return null }
}
export function persistPublishIntent(storage: Pick<Storage, 'setItem' | 'getItem'>, key: string, intent: PublishIntent): boolean {
  try {
    storage.setItem(key, JSON.stringify(intent))
    const saved = parsePublishIntent(storage.getItem(key), intent.base, intent.objectId)
    return !!saved && JSON.stringify(saved) === JSON.stringify(intent)
  } catch { return false }
}
