export interface ReleaseIssue { componentId?: string; path: string; code: string; message: string; severity: 'ERROR' | 'WARNING' }
export interface ReleaseReport {
  pageId: string; revision: number; publicationRevision: number; publishedVersionId: string | null
  schemaVersion: number; runtimeSchemaVersion: number; runtimeSupported: boolean
  diff: { addedComponentIds: string[]; removedComponentIds: string[]; updatedComponentIds: string[]; orderChanged: boolean; themeChanged: boolean; metadataChanged: boolean; nameChanged?: boolean }
  issues: ReleaseIssue[]; canPublish: boolean
}
export function validateReleaseReport(value: unknown): ReleaseReport {
  const report = value as ReleaseReport | null
  const integers = report && [report.revision, report.publicationRevision, report.schemaVersion, report.runtimeSchemaVersion]
  const diff = report?.diff
  const valid = report && typeof report.pageId === 'string' && (report.publishedVersionId === null || typeof report.publishedVersionId === 'string') &&
    integers?.every(item => Number.isSafeInteger(item) && item >= 0) && typeof report.runtimeSupported === 'boolean' && typeof report.canPublish === 'boolean' &&
    diff && [diff.addedComponentIds, diff.removedComponentIds, diff.updatedComponentIds].every(items => Array.isArray(items) && items.length <= 40 && items.every(item => typeof item === 'string')) &&
    [diff.orderChanged, diff.themeChanged, diff.metadataChanged].every(item => typeof item === 'boolean') &&
    (diff.nameChanged === undefined || typeof diff.nameChanged === 'boolean') &&
    Array.isArray(report.issues) && report.issues.every(item => item && (item.componentId === undefined || typeof item.componentId === 'string') && typeof item.path === 'string' && typeof item.code === 'string' && typeof item.message === 'string' && ['ERROR', 'WARNING'].includes(item.severity))
  if (!valid) throw new Error('发布检查报告格式不正确，请重新检查。')
  return report
}
