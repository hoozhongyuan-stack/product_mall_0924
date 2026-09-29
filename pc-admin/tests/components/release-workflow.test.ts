import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ReleaseWorkflowPanel from '../../src/views/ReleaseWorkflowPanel.vue'
import { api, confirmedWrite } from '../../src/api'

vi.mock('../../src/api', () => ({ api: vi.fn(), confirmedWrite: vi.fn() }))

const appId = 'wx0123456789abcdef'
const versionId = '11111111-1111-4111-8111-111111111111'
const category = { first_class: '工具', second_class: '效率', first_id: 1, second_id: 2 }
const readiness = {
  appId, versionId, developerAppId: 'wxabcdef0123456789',
  developerUploadKey: { configured: true, revision: 3, appId: 'wxabcdef0123456789' },
  checks: ['APP_ID', 'SOURCE_PACKAGE', 'RELEASE_CONFIG', 'PLATFORM_INTEGRATION',
    'DEVELOPER_UPLOAD_KEY', 'THIRD_PARTY_AUTH'].map(code => ({ code, status: 'PASS' as const })),
}
const upload = { taskId: '22222222-2222-4222-8222-222222222222', versionId, version: '1.0.0',
  channel: 'DIRECT_COMMIT', status: 'SUCCEEDED', failureCode: '', resolutionNote: '',
  reviewAvailable: true, createdAt: '2026-09-29T00:00:00Z' }
const review = { taskId: '33333333-3333-4333-8333-333333333333', uploadTaskId: upload.taskId,
  version: '1.0.0', auditId: 12345, status: 'APPROVED', reason: '', failureCode: '',
  resolutionNote: '', createdAt: '2026-09-29T00:00:00Z' }

let uploads: typeof upload[]
let reviews: typeof review[]
const wrappers: ReturnType<typeof mount>[] = []

beforeEach(() => {
  uploads = []
  reviews = []
  vi.mocked(api).mockReset()
  vi.mocked(confirmedWrite).mockReset()
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/integrations/wechat-open-platform') return {
      componentAppId: 'wx1111111111111111', developerAppId: readiness.developerAppId,
      redirectUri: 'https://admin.example.com/api/v1/wechat/open-platform/authorization-callback',
      configured: true, ticketReceived: true, revision: 1,
    } as never
    if (path === '/code-release/uploads') return { items: uploads } as never
    if (path === '/code-release/reviews') return { items: reviews } as never
    if (path === '/code-release/categories') return { items: [category] } as never
    throw Error(`Unexpected path ${path}`)
  })
  vi.mocked(confirmedWrite).mockImplementation(async action => {
    if (action === 'code.release.upload') { uploads = [upload]; return upload as never }
    if (action === 'code.release.review') { reviews = [review]; return review as never }
    if (action === 'code.release.publish') return { ...review, status: 'RELEASE_REQUESTED' } as never
    throw Error(`Unexpected action ${action}`)
  })
})

afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))

describe('third-party mini-program release workflow', () => {
  it('binds upload, review and release to the chosen immutable version and audit id', async () => {
    const wrapper = mount(ReleaseWorkflowPanel, {
      props: { readiness, versions: [{ versionId, versionLabel: 'source-1', storageStatus: 'READY' }],
        canManage: true, canManagePlatform: true }, attachTo: document.body,
    })
    wrappers.push(wrapper)
    await flushPromises()
    await wrapper.get('[data-test="upload-version"]').setValue('1.0.0')
    await wrapper.get('[data-test="upload-description"]').setValue('测试版本')
    await wrapper.get('[data-test="upload-password"]').setValue('synthetic-password')
    await wrapper.get('[data-test="direct-upload-form"]').trigger('submit')
    await flushPromises()
    expect(confirmedWrite).toHaveBeenCalledWith('code.release.upload', 'synthetic-password',
      '/code-release/uploads', 'POST', { versionId, version: '1.0.0',
        description: '测试版本', channel: 'DIRECT_COMMIT' }, `${appId}:${versionId}:DIRECT_COMMIT`,
      3, expect.objectContaining({ 'Idempotency-Key': expect.any(String) }))

    await wrapper.get('[data-test="review-upload"]').setValue(upload.taskId)
    await wrapper.get('[data-test="load-categories"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="review-category"]').setValue('0')
    await wrapper.get('[data-test="review-description"]').setValue('功能更新')
    await wrapper.get('[data-test="review-password"]').setValue('review-password')
    await wrapper.get('[data-test="review-form"]').trigger('submit')
    await flushPromises()
    expect(confirmedWrite).toHaveBeenCalledWith('code.release.review', 'review-password',
      '/code-release/reviews', 'POST', { uploadTaskId: upload.taskId,
        itemList: [category], versionDesc: '功能更新' }, upload.taskId, 0,
      expect.objectContaining({ 'Idempotency-Key': expect.any(String) }))

    expect(wrapper.text()).toContain('审核单 12345')
    await wrapper.get('[data-test="release-password"]').setValue('publish-password')
    await wrapper.get('[data-test="publish-reviewed"]').trigger('click')
    await flushPromises()
    expect(confirmedWrite).toHaveBeenCalledWith('code.release.publish', 'publish-password',
      `/code-release/reviews/${review.taskId}/release`, 'POST', {}, review.taskId, 12345)
  })
})
