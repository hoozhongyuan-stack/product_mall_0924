import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ElButton, ElInput, ElRadio, ElRadioGroup } from 'element-plus'
import WechatIntegrationView from '../../src/views/WechatIntegrationView.vue'
import { api, ApiError, type Account } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const base = '/integrations/wechat-mini-program'
const permissions = ['wechat.integration.read', 'wechat.integration.manage']
const account: Account = { accountId: 'integration-test', loginName: 'operator', displayName: '运营人员', kind: 'STAFF', enabled: true, revision: 1, groupIds: [], permissionCodes: permissions }
const config = { revision: 2, source: 'MANAGED', appId: 'wx0123456789abcdef', secretConfigured: true, keyAvailable: true, identityBinding: { status: 'EMPTY', appId: null }, paymentAppIdStatus: 'NOT_CONFIGURED', notificationsStatus: 'NOT_VERIFIED', lastCheck: null }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.mocked(api).mockReset(); localStorage.clear(); sessionStorage.clear(); vi.stubGlobal('confirm', vi.fn(() => true)) })
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); localStorage.clear(); sessionStorage.clear() })
async function setup(value = config, permissionCodes = permissions) {
  vi.mocked(api).mockResolvedValue(value as never)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }, { path: '/elsewhere', component: { template: '<div />' } }] })
  await router.push('/')
  const wrapper = mount(WechatIntegrationView, { attachTo: document.body, props: { account: { ...account, permissionCodes } }, global: { plugins: [router, ElInput, ElButton, ElRadio, ElRadioGroup] } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
function button(wrapper: ReturnType<typeof mount>, text: string) { return wrapper.findAll('button').find(item => item.text() === text)! }
async function replaceSecret(wrapper: ReturnType<typeof mount>, value = 'synthetic-secret') {
  await wrapper.get('input[value="REPLACE"]').setValue(true)
  await wrapper.get('#wechat-secret').setValue(value)
}
async function confirmAction(wrapper: ReturnType<typeof mount>, action = '保存配置') {
  await button(wrapper, action).trigger('click')
  await wrapper.get('#wechat-confirm-password').setValue('synthetic-password')
  await wrapper.get('[aria-label="二次确认"]').trigger('submit')
  await flushPromises()
}

describe('WeChat integration boundary', () => {
  it('renders read-only configuration without returning a secret, and sends no request without read permission', async () => {
    const { wrapper } = await setup(config, ['wechat.integration.read'])
    expect(wrapper.text()).toContain('后台托管')
    expect(wrapper.text()).toContain('尚未校验')
    expect(wrapper.find('#wechat-secret').exists()).toBe(false)
    expect(button(wrapper, '保存配置')).toBeUndefined()
    const denied = await setup(config, [])
    expect(denied.wrapper.text()).toContain('没有读取权限')
    expect(api).toHaveBeenCalledTimes(1)
  })
  it('sends a confirmed REPLACE once without trimming, storing, or retaining the secret after success', async () => {
    const { wrapper } = await setup()
    await replaceSecret(wrapper, '  opaque-密钥  ')
    vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'synthetic-confirmation' } as never)
      .mockResolvedValueOnce({ ...config, revision: 3 } as never)
    await confirmAction(wrapper)
    expect(api).toHaveBeenNthCalledWith(2, '/auth/confirm', expect.objectContaining({ body: JSON.stringify({ action: 'wechat.integration.update', password: 'synthetic-password', objectId: 'wechat-mini-program', revision: 2 }) }))
    expect(api).toHaveBeenLastCalledWith(base, expect.objectContaining({ method: 'PUT', headers: { 'X-Action-Confirmation': 'synthetic-confirmation' }, body: JSON.stringify({ expectedRevision: 2, appId: config.appId, secretAction: 'REPLACE', appSecret: '  opaque-密钥  ' }) }))
    expect(wrapper.text()).toContain('配置已保存')
    expect(wrapper.find('#wechat-secret').exists()).toBe(false)
    expect(wrapper.find('#wechat-confirm-password').exists()).toBe(false)
    expect(localStorage.length + sessionStorage.length).toBe(0)
  })
  it('tests only the saved revision and distinguishes credential validation from real login', async () => {
    const { wrapper } = await setup({ ...config, source: 'ENV', keyAvailable: false })
    expect(button(wrapper, '保存配置').attributes('disabled')).toBeDefined()
    vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'synthetic-confirmation' } as never)
      .mockResolvedValueOnce({ ...config, source: 'ENV', keyAvailable: false, lastCheck: { revision: 2, status: 'SUCCESS', code: 'OK', checkedAt: '2026-09-28T01:00:00Z' } } as never)
    await confirmAction(wrapper, '校验已保存配置')
    expect(api).toHaveBeenLastCalledWith(base + '/test', expect.objectContaining({ method: 'POST', body: JSON.stringify({ expectedRevision: 2 }) }))
    expect(wrapper.text()).toContain('凭据校验通过')
    expect(wrapper.text()).toContain('真实登录与真机验收仍需单独完成')
  })
  it.each([new ApiError('unsafe diagnostic synthetic-secret', 409, 'REVISION_CONFLICT'), new Error('network synthetic-secret')])('keeps edits and blocks retries after rejection or unknown outcome until a read', async failure => {
    const { wrapper } = await setup()
    await replaceSecret(wrapper)
    vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'synthetic-confirmation' } as never).mockRejectedValueOnce(failure)
    await confirmAction(wrapper)
    expect((wrapper.get('#wechat-secret').element as HTMLInputElement).value).toBe('synthetic-secret')
    expect(button(wrapper, '保存配置').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '校验已保存配置').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('unsafe diagnostic')
    expect(wrapper.text()).not.toContain('network synthetic-secret')
    vi.mocked(api).mockResolvedValueOnce({ ...config, revision: 3 } as never)
    await button(wrapper, '重新读取').trigger('click'); await flushPromises()
    expect((wrapper.get('#wechat-secret').element as HTMLInputElement).value).toBe('synthetic-secret')
    expect(wrapper.text()).toContain('请核对后重新确认')
    expect(button(wrapper, '保存配置').attributes('disabled')).toBeUndefined()
  })
  it('stops before sending a write when permission is revoked during password confirmation', async () => {
    const { wrapper } = await setup()
    await replaceSecret(wrapper)
    let resolve!: (value: unknown) => void
    vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }) as never)
    await button(wrapper, '保存配置').trigger('click')
    await wrapper.get('#wechat-confirm-password').setValue('synthetic-password')
    await wrapper.get('[aria-label="二次确认"]').trigger('submit')
    await wrapper.setProps({ account: { ...account, permissionCodes: ['wechat.integration.read'] } })
    resolve({ confirmationToken: 'stale-confirmation' }); await flushPromises()
    expect(vi.mocked(api).mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(0)
    expect(wrapper.find('#wechat-secret').exists()).toBe(false)
  })
})

it('can explicitly save KEEP without sending any secret property', async () => {
  const { wrapper } = await setup()
  vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'synthetic-confirmation' } as never).mockResolvedValueOnce({ ...config, revision: 3 } as never)
  await confirmAction(wrapper)
  const request = vi.mocked(api).mock.calls.find(([, options]) => options?.method === 'PUT')!
  expect(JSON.parse(String(request[1]?.body))).toEqual({ expectedRevision: 2, appId: config.appId, secretAction: 'KEEP' })
})

it('locks a failed platform check until configuration is read again', async () => {
  const { wrapper } = await setup()
  const failed = { ...config, lastCheck: { revision: 2, status: 'FAILED', code: 'ADMIN_CONFIRMATION_REQUIRED', checkedAt: '2026-09-28T01:00:00Z' } }
  vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'synthetic-confirmation' } as never).mockResolvedValueOnce(failed as never)
  await confirmAction(wrapper, '校验已保存配置')
  expect(wrapper.text()).toContain('管理员须在微信平台确认')
  expect(button(wrapper, '校验已保存配置').attributes('disabled')).toBeDefined()
  vi.mocked(api).mockResolvedValueOnce(failed as never)
  await button(wrapper, '重新读取').trigger('click'); await flushPromises()
  expect(button(wrapper, '校验已保存配置').attributes('disabled')).toBeUndefined()
})
it('retains identity restrictions, reports invalid new credentials, and clears a replacement when KEEP is selected', async () => {
  const { wrapper } = await setup({ ...config, identityBinding: { status: 'BOUND', appId: config.appId } })
  await wrapper.get('#wechat-app-id').setValue('wx1111111111111111')
  expect(wrapper.text()).toContain('只能使用已绑定的 AppID')
  expect(button(wrapper, '保存配置').attributes('disabled')).toBeDefined()
  await wrapper.get('#wechat-app-id').setValue(config.appId)
  await replaceSecret(wrapper, '   ')
  expect(wrapper.text()).toContain('不能全为空白')
  await wrapper.get('input[value="KEEP"]').setValue(true)
  expect(wrapper.find('#wechat-secret').exists()).toBe(false)
  await wrapper.get('input[value="REPLACE"]').setValue(true)
  expect((wrapper.get('#wechat-secret').element as HTMLInputElement).value).toBe('')
})
it('handles missing configuration and read failures without exposing unsafe response messages', async () => {
  const { wrapper } = await setup({ ...config, revision: 0, source: 'ENV', appId: '', secretConfigured: false })
  expect(wrapper.text()).toContain('尚未配置')
  expect(wrapper.get('input[value="KEEP"]').attributes('disabled')).toBeDefined()
  expect(button(wrapper, '校验已保存配置').attributes('disabled')).toBeDefined()
  vi.mocked(api).mockRejectedValueOnce(new ApiError('secret diagnostic', 503, 'CREDENTIALS_UNAVAILABLE'))
  await button(wrapper, '重新读取').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('凭据暂不可用')
  expect(wrapper.text()).not.toContain('secret diagnostic')
  expect(button(wrapper, '保存配置').attributes('disabled')).toBeDefined()
})
it('cancels confirmation without losing edits and protects tab closing', async () => {
  const { wrapper } = await setup()
  await replaceSecret(wrapper)
  await button(wrapper, '保存配置').trigger('click')
  await wrapper.get('#wechat-confirm-password').setValue('synthetic-password')
  await button(wrapper, '取消').trigger('click')
  expect(wrapper.find('#wechat-confirm-password').exists()).toBe(false)
  expect((wrapper.get('#wechat-secret').element as HTMLInputElement).value).toBe('synthetic-secret')
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(true)
})
it('ignores a stale read after switching accounts and rejects inconsistent write receipts', async () => {
  const { wrapper } = await setup()
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }) as never)
  await button(wrapper, '重新读取').trigger('click')
  vi.mocked(api).mockResolvedValueOnce({ ...config, appId: 'wx1111111111111111' } as never)
  await wrapper.setProps({ account: { ...account, accountId: 'next-account' } }); await flushPromises()
  resolve(config); await flushPromises()
  expect((wrapper.get('#wechat-app-id').element as HTMLInputElement).value).toBe('wx1111111111111111')
  await replaceSecret(wrapper)
  vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'synthetic-confirmation' } as never).mockResolvedValueOnce({ ...config, revision: 7 } as never)
  await confirmAction(wrapper)
  expect(wrapper.text()).toContain('无法确认服务端结果')
  expect(button(wrapper, '保存配置').attributes('disabled')).toBeDefined()
})
