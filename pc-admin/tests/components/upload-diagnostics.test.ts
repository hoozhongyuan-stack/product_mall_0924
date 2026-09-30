import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CodeDomainCheck from '../../src/views/CodeDomainCheck.vue'
import UploadTaskResult from '../../src/views/UploadTaskResult.vue'
import { api } from '../../src/api'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.resetAllMocks() })
const task = { taskId: 'task-1', version: '1.2.3', status: 'FAILED', failureCode: 'COMPILE_FAILED',
  failureStage: 'COMPILE', sdkCode: '10001', platformErrorCode: null,
  failureMessage: '代码编译失败。', nextAction: '请修复代码包后重试。', createdAt: '', completedAt: '' }
describe('upload evidence and domain checks', () => {
  it('renders stage, safe reason, next action and diagnostic codes', () => {
    const w = mount(UploadTaskResult, { props: { task } }); wrappers.push(w)
    expect(w.text()).toContain('代码编译')
    expect(w.text()).toContain('代码编译失败。')
    expect(w.text()).toContain('请修复代码包后重试。')
    expect(w.text()).toContain('10001')
  })
  it('keeps legacy unknown outcome explicit instead of implying failure', () => {
    const w = mount(UploadTaskResult, { props: { task: { taskId: 'old', version: '1', status: 'UNKNOWN', failureCode: 'RESULT_UNKNOWN' } } }); wrappers.push(w)
    expect(w.text()).toContain('结果待核查')
    expect(w.text()).toContain('请先在微信公众平台核对')
    expect(w.text()).not.toContain('上传失败')
  })
  it('distinguishes a received development version from publishing', () => {
    const w = mount(UploadTaskResult, { props: { task: { taskId: 'ok', version: '2', status: 'SUCCEEDED' } } }); wrappers.push(w)
    expect(w.text()).toContain('微信已接收开发版本')
    expect(w.text()).not.toContain('发布成功')
  })
  it('labels the authorized upload as a review candidate rather than a development version', () => {
    const w = mount(UploadTaskResult, { props: { task: { taskId: 'ok', version: '2', status: 'SUCCEEDED', channel: 'DIRECT_COMMIT' } } }); wrappers.push(w)
    expect(w.text()).toContain('微信已接收待审核版本')
    expect(w.text()).not.toContain('微信已接收开发版本')
  })
  it('shows signed platform code and completion time without raw error prose', () => {
    const w = mount(UploadTaskResult, { props: { task: { ...task, failureStage: 'UPLOAD',
      failureCode: 'WECHAT_REJECTED', platformErrorCode: -1, completedAt: '2026-09-30T00:00:00Z' } } }); wrappers.push(w)
    expect(w.text()).toContain('微信错误码-1')
    expect(w.text()).toContain('完成时间')
    expect(w.text()).toContain('上传微信')
  })
  it('shows service credential readiness separately from code upload gates', () => {
    const w = mount(CodeDomainCheck, { props: { appId: 'wx1', versionId: 'v1',
      credentialCheck: { status: 'BLOCKED', detail: '请先配置 AppSecret。' } } }); wrappers.push(w)
    expect(w.text()).toContain('AppSecret：未满足')
    expect(w.text()).toContain('请先配置 AppSecret。')
  })
  it('queries only on demand, uses selected package and clears stale result on change', async () => {
    vi.mocked(api).mockResolvedValue({ status: 'BLOCKED', detail: '缺少请求域名。', missingRequestDomains: ['https://api.example.com'], checkedAt: '2026-09-30T00:00:00Z' })
    const w = mount(CodeDomainCheck, { props: { appId: 'wx1', versionId: 'version-1' } }); wrappers.push(w)
    expect(api).not.toHaveBeenCalled()
    await w.get('button').trigger('click'); await flushPromises()
    expect(api).toHaveBeenCalledWith('/code-release/domain-check', { method: 'POST', body: JSON.stringify({ versionId: 'version-1' }) })
    expect(w.text()).toContain('https://api.example.com')
    await w.setProps({ versionId: 'version-2' })
    expect(w.text()).not.toContain('缺少请求域名。')
    expect(w.text()).toContain('尚未检查')
  })
  it('discards an in-flight result when the selected app changes', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(api).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const w = mount(CodeDomainCheck, { props: { appId: 'wx1', versionId: 'v1' } }); wrappers.push(w)
    await w.get('button').trigger('click'); await w.setProps({ appId: 'wx2' })
    finish({ status: 'PASS', detail: '已验证域名' }); await flushPromises()
    expect(w.text()).not.toContain('已验证域名')
  })
  it('clears previous success when a new check fails', async () => {
    vi.mocked(api).mockResolvedValueOnce({ status: 'PASS', detail: '已验证域名', checkedAt: '', missingRequestDomains: [] }).mockRejectedValueOnce(Error('private-token'))
    const w = mount(CodeDomainCheck, { props: { appId: 'wx1', versionId: 'v1' } }); wrappers.push(w)
    await w.get('button').trigger('click'); await flushPromises()
    await w.get('button').trigger('click'); await flushPromises()
    expect(w.text()).not.toContain('已验证域名')
    expect(w.text()).not.toContain('private-token')
    expect(w.text()).toContain('暂时无法检查')
  })
})
