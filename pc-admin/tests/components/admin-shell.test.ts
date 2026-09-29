import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ElButton, ElInput, ElDrawer } from 'element-plus'
import App from '../../src/App.vue'
import { router as applicationRouter } from '../../src/router'
import { api, type Account } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const account: Account = { accountId: 'pilot', loginName: 'operator', displayName: '运营人员', kind: 'OWNER', enabled: true, revision: 1, groupIds: [], permissionCodes: [] }
const wrappers: ReturnType<typeof mount>[] = []
const key = 'mall.admin.remembered-login'
beforeEach(() => { vi.mocked(api).mockReset(); localStorage.clear() })
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); localStorage.clear() })
async function setup(permissions: string[] | null, path = '/') {
  vi.mocked(api).mockImplementation(async endpoint => {
    if (endpoint === '/me' && permissions === null) { window.dispatchEvent(new Event('admin-session-expired')); throw new Error('未登录') }
    return { ...account, permissionCodes: permissions || [] } as never
  })
  const router = createRouter({ history: createMemoryHistory(), routes: applicationRouter.options.routes.map(route =>
    'redirect' in route ? route : { ...route, component: { template: '<div data-page>业务内容</div>' } }) })
  await router.push(path)
  const wrapper = mount(App, { global: { plugins: [router, ElButton, ElInput, ElDrawer] } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

describe('pilot shell and login', () => {
  it('clears the password immediately after login and logout without remembering the account', async () => {
    const { wrapper } = await setup(null)
    await wrapper.get('#login-name').setValue('synthetic-operator')
    await wrapper.get('#login-password').setValue('synthetic-password')
    expect((wrapper.get('input[type=checkbox]').element as HTMLInputElement).checked).toBe(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const logout = wrapper.findAll('button').find(button => button.text() === '退出')!
    await logout.trigger('click')
    await flushPromises()
    expect((wrapper.get('#login-password').element as HTMLInputElement).value).toBe('')
    expect(localStorage.getItem(key)).toBeNull()
    expect(wrapper.text()).not.toContain('synthetic-password')
  })
  it('treats an initial anonymous session as normal, but reports expiry after authentication', async () => {
    const anonymous = await setup(null)
    expect(anonymous.wrapper.text()).not.toContain('会话已失效')
    anonymous.wrapper.unmount()
    const authenticated = await setup([])
    window.dispatchEvent(new Event('admin-session-expired'))
    await flushPromises()
    expect(authenticated.wrapper.text()).toContain('会话已失效，请重新登录。')
    expect(authenticated.wrapper.find('[data-page]').exists()).toBe(false)
  })
  it('remembers only the opted-in account name after successful login', async () => {
    const { wrapper } = await setup(null)
    await wrapper.get('#login-name').setValue('shop-operator')
    await wrapper.get('#login-password').setValue('synthetic-password')
    await wrapper.get('input[type=checkbox]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(localStorage.getItem(key)).toBe('shop-operator')
    expect(Object.values(localStorage)).not.toContain('synthetic-password')
    expect(api).toHaveBeenCalledWith('/auth/login', expect.objectContaining({ body: JSON.stringify({ loginName: 'shop-operator', password: 'synthetic-password' }) }))
  })
  it('restores the remembered account and removes it immediately when unchecked', async () => {
    localStorage.setItem(key, 'saved-operator')
    const { wrapper } = await setup(null)
    expect((wrapper.get('#login-name').element as HTMLInputElement).value).toBe('saved-operator')
    expect((wrapper.get('#login-password').element as HTMLInputElement).value).toBe('')
    await wrapper.get('input[type=checkbox]').setValue(false)
    expect(localStorage.getItem(key)).toBeNull()
  })
  it('still logs in when browser preference storage is unavailable', async () => {
    const { wrapper } = await setup(null)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    await wrapper.get('#login-name').setValue('operator')
    await wrapper.get('#login-password').setValue('synthetic-password')
    await wrapper.get('input[type=checkbox]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-page]').exists()).toBe(true)
  })
  it('groups all authorized entries into nine sections and selects the detail parent', async () => {
    const permissions = applicationRouter.options.routes.flatMap(route => [String(route.meta?.permission || ''), ...(route.meta?.permissionsAny as string[] || [])])
    const { wrapper } = await setup(permissions, '/orders/one')
    expect(wrapper.get('nav[aria-label="后台主导航"]').findAll('a').map(a => a.text())).toEqual(['工作台', '商品', '库存', '订单', '会员', '营销', '店铺', '小程序', '系统管理'])
    expect(wrapper.get('nav[aria-label="订单功能"]').text()).toContain('付款配置')
    expect(wrapper.get('nav[aria-label="页面位置"]').text()).toContain('订单详情')
    expect(wrapper.get('nav[aria-label="后台主导航"] a[aria-current="true"]').text()).toBe('订单')
  })
  it('uses the existing route permission metadata for navigation and direct access', async () => {
    const { wrapper, router } = await setup(['asset.upload'])
    expect(wrapper.get('nav[aria-label="后台主导航"]').findAll('a').map(a => a.text())).toEqual(['工作台', '店铺'])
    await router.push('/assets')
    await flushPromises()
    expect(wrapper.get('nav[aria-label="店铺功能"]').findAll('a').map(a => a.text())).toEqual(['素材中心'])
    await router.push('/orders/one')
    await flushPromises()
    expect(wrapper.text()).toContain('无权访问订单详情')
    expect(wrapper.find('[data-page]').exists()).toBe(false)
    expect(wrapper.get('nav[aria-label="后台主导航"]').text()).not.toContain('订单')
  })
  it('provides a keyboard reachable collapsed navigation control', async () => {
    const { wrapper } = await setup(['catalog.read'])
    const menu = wrapper.get('button[aria-label="打开导航菜单"]')
    expect(menu.attributes('aria-expanded')).toBe('false')
    await menu.trigger('click')
    expect(menu.attributes('aria-expanded')).toBe('true')
  })
})
