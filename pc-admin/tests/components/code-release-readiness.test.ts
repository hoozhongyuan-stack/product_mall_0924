import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CodeVersionsView from '../../src/views/CodeVersionsView.vue'
import { api, type Account } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))

const account: Account = {
  accountId: 'code-owner', loginName: 'owner', displayName: '主账号', kind: 'OWNER',
  enabled: true, revision: 1, groupIds: [], permissionCodes: ['code.version.read', 'code.release.manage'],
}
const readiness = {
  appId: 'wx0123456789abcdef', versionId: '11111111-1111-4111-8111-111111111111',
  uploadKey: { configured: false, revision: 0, appId: null },
  checks: [
    { code: 'APP_ID', status: 'PASS', title: '小程序 AppID', detail: '已配置，与登录凭据一致。' },
    { code: 'SOURCE_PACKAGE', status: 'BLOCKED', title: '代码包', detail: '包内 API 地址仍为本机地址。' },
    { code: 'UPLOAD_KEY', status: 'BLOCKED', title: '代码上传密钥', detail: '尚未上传。' },
    { code: 'APP_SECRET', status: 'UNVERIFIED', title: '审核调用凭据', detail: '已保存，尚未向微信校验。' },
    { code: 'THIRD_PARTY', status: 'UNVERIFIED', title: '第三方平台授权', detail: '当前系统尚未接入授权验证。' },
  ],
}
const wrappers: ReturnType<typeof mount>[] = []

beforeEach(() => {
  vi.mocked(api).mockReset()
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/code-release/readiness') return readiness as never
    if (path === '/code-versions' || path === '/code-sync-jobs') return { items: [], nextCursor: null } as never
    throw new Error(`Unexpected path: ${path}`)
  })
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))

async function setup(value = account) {
  const wrapper = mount(CodeVersionsView, { props: { account: value }, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('code release readiness', () => {
  it('shows passed, blocked, and unverified checks without claiming WeChat approval', async () => {
    const wrapper = await setup()
    expect(wrapper.text()).toContain('已满足')
    expect(wrapper.text()).toContain('未满足')
    expect(wrapper.text()).toContain('无法验证')
    expect(wrapper.text()).toContain('第三方平台授权')
    expect(wrapper.text()).toContain('当前系统尚未接入授权验证')
    expect(wrapper.text()).not.toContain('可以提审')
  })

  it('uploads the selected key only after password confirmation, then clears local input', async () => {
    const wrapper = await setup()
    const file = new File(['-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----'], 'private.key', { type: 'text/plain' })
    Object.defineProperty(wrapper.get('input[type="file"]').element, 'files', { configurable: true, value: [file] })
    await wrapper.get('input[type="file"]').trigger('change')
    await wrapper.get('[data-test="begin-key-upload"]').trigger('click')
    await wrapper.get('[data-test="key-password"]').setValue('synthetic-password')
    vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'synthetic-confirmation' } as never)
      .mockResolvedValueOnce({ configured: true, revision: 1, appId: readiness.appId } as never)
      .mockResolvedValueOnce({ ...readiness, uploadKey: { configured: true, revision: 1, appId: readiness.appId },
        checks: readiness.checks.map(check => check.code === 'UPLOAD_KEY' ? { ...check, status: 'PASS' } : check) } as never)
    await wrapper.get('[data-test="key-upload-form"]').trigger('submit')
    await flushPromises()
    expect(api).toHaveBeenCalledWith('/auth/confirm', expect.objectContaining({ method: 'POST' }))
    const write = vi.mocked(api).mock.calls.find(([path, options]) => path === '/code-release/upload-key' && options?.method === 'PUT')
    expect(write).toBeDefined()
    expect(write?.[1]?.headers).toEqual({ 'X-Action-Confirmation': 'synthetic-confirmation' })
    expect(JSON.parse(String(write?.[1]?.body))).toEqual({ appId: readiness.appId, key: await file.text(), expectedRevision: 0 })
    expect(wrapper.text()).toContain('密钥已保存')
    expect(localStorage.length + sessionStorage.length).toBe(0)
    expect(wrapper.find('[data-test="key-upload-form"]').exists()).toBe(false)
  })

  it('hides key management from a read-only account', async () => {
    const wrapper = await setup({ ...account, permissionCodes: ['code.version.read'] })
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="begin-key-upload"]').exists()).toBe(false)
  })
})
