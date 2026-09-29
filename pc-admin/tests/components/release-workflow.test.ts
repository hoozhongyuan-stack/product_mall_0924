import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ReleaseWorkflowPanel from '../../src/views/ReleaseWorkflowPanel.vue'
import { api, confirmedWrite } from '../../src/api'

vi.mock('../../src/api', () => ({ api: vi.fn(), confirmedWrite: vi.fn() }))

const appId = 'wx0123456789abcdef'
const versionId = '11111111-1111-4111-8111-111111111111'
const category = { first_class: '工具', second_class: '效率', first_id: 1, second_id: 2 }
const readiness = {
  appId, versionId, egressIp: '8.152.204.21', developerAppId: 'wxabcdef0123456789',
  uploadKey: { configured: true, revision: 2, appId },
  developerUploadKey: { configured: true, revision: 3, appId: 'wxabcdef0123456789' },
  checks: ['APP_ID', 'SOURCE_PACKAGE', 'RELEASE_CONFIG', 'UPLOAD_KEY', 'PLATFORM_INTEGRATION',
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
  vi.mocked(confirmedWrite).mockImplementation(async (action, _password, _path, _method, body) => {
    if (action === 'code.release.upload') {
      const result = body.channel === 'CI_DIRECT' ? { ...upload, channel: 'CI_DIRECT', reviewAvailable: false } : upload
      uploads = [result]
      return result as never
    }
    if (action === 'code.release.review') { reviews = [review]; return review as never }
    if (action === 'code.release.publish') return { ...review, status: 'RELEASE_REQUESTED' } as never
    throw Error(`Unexpected action ${action}`)
  })
})

afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))

describe('third-party mini-program release workflow', () => {
  it('allows direct upload with no third-party authorization and keeps review unavailable', async () => {
    const directReadiness = { ...readiness, checks: readiness.checks.map(check =>
      ['PLATFORM_INTEGRATION', 'DEVELOPER_UPLOAD_KEY', 'THIRD_PARTY_AUTH'].includes(check.code)
        ? { ...check, status: 'BLOCKED' as const } : check) }
    const wrapper = mount(ReleaseWorkflowPanel, {
      props: { readiness: directReadiness,
        versions: [{ versionId, versionLabel: 'source-1', storageStatus: 'READY' }],
        canManage: true, canManagePlatform: false }, attachTo: document.body,
    })
    wrappers.push(wrapper)
    await flushPromises()
    expect(wrapper.text()).toContain('8.152.204.21')
    expect(wrapper.get('[data-test="ci-direct-upload-form"] button[type="submit"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-test="ci-direct-version"]').setValue('1.0.1')
    await wrapper.get('[data-test="ci-direct-description"]').setValue('开发版验证')
    await wrapper.get('[data-test="ci-direct-password"]').setValue('synthetic-password')
    await wrapper.get('[data-test="ci-direct-upload-form"]').trigger('submit')
    await flushPromises()
    expect(confirmedWrite).toHaveBeenCalledWith('code.release.upload', 'synthetic-password',
      '/code-release/uploads', 'POST', { versionId, version: '1.0.1',
        description: '开发版验证', channel: 'CI_DIRECT' }, `${appId}:${versionId}`, 2,
      expect.objectContaining({ 'Idempotency-Key': expect.any(String) }))
    expect(wrapper.text()).toContain('开发版本')
    expect(wrapper.get('[data-test="review-upload"]').findAll('option')).toHaveLength(1)
  })

  it('blocks direct upload when the target key is unavailable', async () => {
    const blocked = { ...readiness, checks: readiness.checks.map(check =>
      check.code === 'UPLOAD_KEY' ? { ...check, status: 'BLOCKED' as const } : check) }
    const wrapper = mount(ReleaseWorkflowPanel, {
      props: { readiness: blocked,
        versions: [{ versionId, versionLabel: 'source-1', storageStatus: 'READY' }],
        canManage: true, canManagePlatform: false }, attachTo: document.body,
    })
    wrappers.push(wrapper)
    await flushPromises()
    expect(wrapper.get('[data-test="ci-direct-upload-form"] button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('keeps the created task visible when records fail to refresh and prevents a repeat', async () => {
    const wrapper = mount(ReleaseWorkflowPanel, {
      props: { readiness, versions: [{ versionId, versionLabel: 'source-1', storageStatus: 'READY' }],
        canManage: true, canManagePlatform: false }, attachTo: document.body,
    })
    wrappers.push(wrapper)
    await flushPromises()
    vi.mocked(api).mockImplementation(async path => {
      if (path === '/code-release/uploads') throw new Error('records unavailable')
      if (path === '/code-release/reviews') return { items: [] } as never
      throw new Error(`Unexpected path ${path}`)
    })
    await wrapper.get('[data-test="ci-direct-version"]').setValue('1.0.2')
    await wrapper.get('[data-test="ci-direct-description"]').setValue('后台直传')
    await wrapper.get('[data-test="ci-direct-password"]').setValue('synthetic-password')
    await wrapper.get('[data-test="ci-direct-upload-form"]').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain(upload.taskId)
    expect(wrapper.text()).toContain('记录刷新失败')
    expect(wrapper.get('[data-test="ci-direct-upload-form"] button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(confirmedWrite).toHaveBeenCalledTimes(1)
  })

  it('lets the backend check an older selected version when the latest package is blocked', async () => {
    const olderId = '44444444-4444-4444-8444-444444444444'
    const latestBlocked = { ...readiness, checks: readiness.checks.map(check =>
      check.code === 'RELEASE_CONFIG' ? { ...check, status: 'BLOCKED' as const } : check) }
    const wrapper = mount(ReleaseWorkflowPanel, {
      props: { readiness: latestBlocked, versions: [
        { versionId, versionLabel: 'latest', storageStatus: 'READY' },
        { versionId: olderId, versionLabel: 'older', storageStatus: 'READY' },
      ], canManage: true, canManagePlatform: false }, attachTo: document.body,
    })
    wrappers.push(wrapper)
    await flushPromises()
    expect(wrapper.get('[data-test="ci-direct-upload-form"] button[type="submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="ci-direct-version-id"]').setValue(olderId)
    expect(wrapper.get('[data-test="ci-direct-upload-form"] button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

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
