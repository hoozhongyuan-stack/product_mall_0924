import { afterEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { useReleaseReport } from '../../src/views/pages/use-release-report'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
import { api } from '../../src/api'
const result = (revision = 1, publicationRevision = 0) => ({ pageId: 'page-a', revision, publicationRevision, publishedVersionId: null, schemaVersion: 1, runtimeSchemaVersion: 1, runtimeSupported: true, diff: { addedComponentIds: [], removedComponentIds: [], updatedComponentIds: [], orderChanged: false, themeChanged: false, metadataChanged: false }, issues: [], canPublish: true })
afterEach(() => vi.resetAllMocks())
describe('release report revision isolation', () => {
  function setup() {
    const draft = ref<{ pageId: string; revision: number; publicationRevision: number } | null>({ pageId: 'page-a', revision: 1, publicationRevision: 0 })
    const dirty = ref(false), busy = ref('')
    const save = vi.fn(async () => { dirty.value = false; return true })
    const runtime = useReleaseReport(draft, dirty, busy, () => '/pages/home', save)
    return { ...runtime, draft, dirty, busy, save }
  }
  it('checks saved dual revisions and marks an edited report stale', async () => {
    vi.mocked(api).mockResolvedValue(result())
    const s = setup(); await s.refreshReport()
    expect(api).toHaveBeenCalledWith('/pages/home/release-report', { method: 'POST', body: JSON.stringify({ expectedRevision: 1, expectedPublicationRevision: 0 }) })
    expect(s.report.value).toEqual(result()); expect(s.reportStale.value).toBe(false)
    s.dirty.value = true; expect(s.reportStale.value).toBe(true)
    s.dirty.value = false; s.draft.value = { ...s.draft.value!, publicationRevision: 2 }; expect(s.reportStale.value).toBe(true)
  })
  it('saves before checking and does not request when save fails or other work is busy', async () => {
    const s = setup(); s.dirty.value = true; s.save.mockResolvedValue(false)
    await s.refreshReport(); expect(api).not.toHaveBeenCalled()
    s.busy.value = 'save'; await s.refreshReport(); expect(s.save).toHaveBeenCalledTimes(1)
    s.busy.value = ''; s.save.mockImplementation(async () => { s.dirty.value = false; s.draft.value = { ...s.draft.value!, revision: 2 }; return true })
    vi.mocked(api).mockResolvedValue(result(2)); await s.refreshReport(); expect(s.report.value?.revision).toBe(2)
  })
  it('discards late responses after page switch, revisions or edits', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(api).mockImplementation(() => new Promise(done => { resolve = done }) as never)
    const s = setup(); const pending = s.refreshReport(); s.draft.value = { pageId: 'page-b', revision: 1, publicationRevision: 0 }; resolve(result()); await pending
    expect(s.report.value).toBeNull(); expect(s.reportError.value).toBe('')
    const pending2 = s.refreshReport(); s.dirty.value = true; resolve({ ...result(), pageId: 'page-b' }); await pending2
    expect(s.report.value).toBeNull(); expect(s.busy.value).toBe('')
  })
  it('clears previous conclusions on errors and malformed revision results', async () => {
    const s = setup(); await s.refreshReport(); expect(s.reportError.value).toBeTruthy()
    vi.mocked(api).mockRejectedValue(new Error('检查失败')); await s.refreshReport(); expect(s.reportError.value).toBe('检查失败'); expect(s.report.value).toBeNull()
    vi.mocked(api).mockResolvedValue(result(99)); await s.refreshReport(); expect(s.reportError.value).toContain('修订')
    s.draft.value = null; await s.refreshReport(); expect(s.report.value).toBeNull()
  })
})
