import { computed, ref, type Ref } from 'vue'
import { api } from '../../api'
import { validateReleaseReport, type ReleaseReport } from './release-report'

type DraftRevision = { pageId: string; revision: number; publicationRevision?: number }
/** Reports are read-only conclusions about one saved draft and publication pointer. */
export function useReleaseReport(
  draft: Ref<DraftRevision | null>, dirty: Ref<boolean>, busy: Ref<string>,
  base: () => string, save: () => Promise<boolean>,
) {
  const report = ref<ReleaseReport | null>(null)
  const reportLoading = ref(false)
  const reportError = ref('')
  const reportStale = computed(() => !!report.value && (dirty.value || !draft.value ||
    report.value.pageId !== draft.value.pageId || report.value.revision !== draft.value.revision ||
    report.value.publicationRevision !== (draft.value.publicationRevision ?? 0)))
  async function refreshReport() {
    if (!draft.value || busy.value) return
    if (dirty.value && !(await save())) return
    if (!draft.value || dirty.value || busy.value) return
    const target = { ...draft.value, publicationRevision: draft.value.publicationRevision ?? 0 }
    const targetBase = base()
    const matches = () => !dirty.value && base() === targetBase && draft.value?.pageId === target.pageId &&
      draft.value.revision === target.revision && (draft.value.publicationRevision ?? 0) === target.publicationRevision
    busy.value = 'release-report'; reportLoading.value = true; reportError.value = ''; report.value = null
    try {
      const result = await api<ReleaseReport>(`${targetBase}/release-report`, {
        method: 'POST', body: JSON.stringify({ expectedRevision: target.revision, expectedPublicationRevision: target.publicationRevision }),
      })
      if (!matches()) return
      if (!result || result.pageId !== target.pageId || result.revision !== target.revision ||
        result.publicationRevision !== target.publicationRevision) throw new Error('报告修订不匹配，请重新检查。')
      report.value = validateReleaseReport(result)
    } catch (reason) {
      if (matches()) reportError.value = reason instanceof Error ? reason.message : '发布检查失败，请重试。'
    } finally { reportLoading.value = false; if (busy.value === 'release-report') busy.value = '' }
  }
  return { report, reportLoading, reportError, reportStale, refreshReport }
}
