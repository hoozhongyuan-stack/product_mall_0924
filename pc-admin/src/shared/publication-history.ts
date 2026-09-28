export interface VersionSummary { versionId: string; revision: number; name: string; publishedAt: string; publishedBy: string | { displayName: string }; isCurrent: boolean }
export interface RollbackBody { expectedRevision: number; expectedPublicationRevision: number; versionId: string; reason: string }
export interface RollbackResult { versionId: string; revision: number; publicationRevision: number; draftRevision: number }
export interface RollbackIntent { scope: string; key: string; body: RollbackBody }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function historyScope(actor: string, base: string): string { return JSON.stringify([actor, base]) }
export function rollbackBody(versionId: string, draftRevision: number, publicationRevision: number, reason: string): RollbackBody {
 if (!uuid.test(versionId) || !Number.isSafeInteger(draftRevision) || draftRevision < 1 || !Number.isSafeInteger(publicationRevision) || publicationRevision < 0 || !reason.trim() || reason.trim().length > 200) throw new Error('请填写 1—200 字回退原因，并重新读取有效的版本信息。')
 return { expectedRevision: draftRevision, expectedPublicationRevision: publicationRevision, versionId, reason: reason.trim() }
}
export function parseRollbackIntent(raw: string | null, scope: string): RollbackIntent | null {
 try {
  const value = JSON.parse(raw || 'null')
  if (!value || Object.keys(value).sort().join(',') !== 'body,key,scope' || value.scope !== scope || !uuid.test(value.key) || Object.keys(value.body).sort().join(',') !== 'expectedPublicationRevision,expectedRevision,reason,versionId') return null
  const body = rollbackBody(value.body.versionId, value.body.expectedRevision, value.body.expectedPublicationRevision, value.body.reason)
  return JSON.stringify(body) === JSON.stringify(value.body) ? { scope, key: value.key, body } : null
 } catch { return null }
}
export function matchesRollbackResult(result: RollbackResult, body: RollbackBody): boolean {
 return result.versionId === body.versionId && result.publicationRevision === body.expectedPublicationRevision + 1 && result.draftRevision === body.expectedRevision && Number.isSafeInteger(result.revision) && result.revision > 0
}
export function keepRollbackIntent(status: number | undefined, recovering: boolean): boolean {
 return recovering || status === undefined || status < 400 || status >= 500 || [408, 429].includes(status)
}
export function persistRollbackIntent(storage: Pick<Storage, 'setItem' | 'getItem'>, key: string, intent: RollbackIntent): boolean {
 try {
  storage.setItem(key, JSON.stringify(intent))
  return !!parseRollbackIntent(storage.getItem(key), intent.scope)
 } catch { return false }
}
