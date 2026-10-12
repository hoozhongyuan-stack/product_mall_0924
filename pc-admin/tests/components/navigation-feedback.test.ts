import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, inject, type Ref } from 'vue'
import { createMemoryHistory, createRouter, onBeforeRouteLeave } from 'vue-router'
import { ElButton, ElInput, ElDrawer } from 'element-plus'
import App from '../../src/App.vue'
import { router as applicationRouter } from '../../src/router'
import { api, type Account } from '../../src/api'
import { useNavigationFeedback } from '../../src/shared/navigation/useNavigationFeedback'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
const targetPage = { template: '<section data-target>目标页面</section>' }
const initialPage = { template: '<section data-original><input aria-label="保留草稿" value="尚未保存" /></section>' }
const account = { accountId: 'actor', displayName: '运营人员', kind: 'OWNER', permissionCodes: ['catalog.read', 'order.read', 'inventory.read'] } as Account
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  vi.mocked(api).mockResolvedValue(account)
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.restoreAllMocks()
  vi.resetAllMocks()
  vi.useRealTimers()
})
async function setup(catalog: () => Promise<any>, orders = () => Promise.resolve(targetPage), initial: any = initialPage, start = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes: applicationRouter.options.routes.map(record =>
    'redirect' in record ? record : { ...record, component: record.path === '/catalog' ? catalog : record.path === '/orders' ? orders : record.path === start ? initial : targetPage }) })
  await router.push(start)
  const wrapper = mount(App, { global: { plugins: [router, ElButton, ElInput, ElDrawer] } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
const feedback = (wrapper: ReturnType<typeof mount>) => wrapper.find('[data-navigation-feedback]')
const menu = (wrapper: ReturnType<typeof mount>, path: string) => wrapper.get(`nav[aria-label="后台主导航"] a[href="${path}"]`)

it.each(['before', 'during'] as const)('rechecks target permissions %s the safe-refresh leave decision', async when => {
  const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
  let allowed = true
  const decision = deferred<boolean>()
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: targetPage },
    { path: '/inventory', component: initialPage },
    { path: '/catalog', component: () => Promise.reject(new Error('cached chunk failure')), meta: { title: '商品管理', permission: 'catalog.read' } },
  ] })
  await router.push('/inventory')
  const navigation = useNavigationFeedback(router, () => true, () => allowed)
  try {
    await navigation.navigate('/catalog')
    router.beforeEach(to => to.path === '/' ? decision.promise : undefined)
    if (when === 'before') allowed = false
    const refreshing = navigation.safeRefresh()
    await flushPromises()
    allowed = false
    decision.resolve(true)
    await refreshing
    expect(assign).not.toHaveBeenCalled()
    expect(navigation.state.value?.phase).toBe('blocked')
    expect(router.currentRoute.value.path).toBe(when === 'before' ? '/inventory' : '/')
  } finally { navigation.dispose() }
})

describe('navigation feedback keeps the active page safe', () => {
  it('names the clicked target immediately, explains a slow chunk, and clears after success', async () => {
    const chunk = deferred<any>()
    const { wrapper, router } = await setup(() => chunk.promise)
    await menu(wrapper, '/catalog').trigger('click')
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('商品管理')
    expect(wrapper.get('[aria-label="保留草稿"]').element).toHaveProperty('value', '尚未保存')
    expect(router.currentRoute.value.path).toBe('/')
    await vi.advanceTimersByTimeAsync(3100)
    expect(feedback(wrapper).text()).toContain('较长')
    chunk.resolve(targetPage)
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/catalog')
    expect(feedback(wrapper).exists()).toBe(false)
  })
  it('shows intent while waiting for the original leave guard, and clears a refusal without importing', async () => {
    const decision = deferred<boolean>()
    const loader = vi.fn().mockResolvedValue(targetPage)
    const dirtyPage = defineComponent({ setup() { onBeforeRouteLeave(() => decision.promise) }, template: initialPage.template })
    const { wrapper, router } = await setup(loader, undefined, dirtyPage)
    await menu(wrapper, '/catalog').trigger('click')
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('商品管理')
    expect(loader).not.toHaveBeenCalled()
    decision.resolve(false)
    await flushPromises()
    expect(feedback(wrapper).exists()).toBe(false)
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.get('[aria-label="保留草稿"]').element).toHaveProperty('value', '尚未保存')
  })
  it('keeps the draft on chunk failure and retries by running navigation again', async () => {
    const loader = vi.fn().mockRejectedValueOnce(new Error('Failed to fetch dynamically imported module')).mockResolvedValueOnce(targetPage)
    const { wrapper, router } = await setup(loader)
    await menu(wrapper, '/catalog').trigger('click')
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('无法打开')
    expect(wrapper.get('[aria-label="保留草稿"]').element).toHaveProperty('value', '尚未保存')
    await feedback(wrapper).findAll('button').find(button => button.text() === '重新尝试')!.trigger('click')
    await flushPromises()
    expect(loader).toHaveBeenCalledTimes(2)
    expect(router.currentRoute.value.path).toBe('/catalog')
    expect(feedback(wrapper).exists()).toBe(false)
  })
  it('dismisses a load failure hint without leaving the active page or losing its draft', async () => {
    const loader = vi.fn().mockRejectedValue(new Error('Failed to fetch dynamically imported module'))
    const { wrapper, router } = await setup(loader)
    await menu(wrapper, '/catalog').trigger('click')
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('无法打开商品管理')
    await feedback(wrapper).findAll('button').find(button => button.text() === '关闭提示')!.trigger('click')
    await flushPromises()
    expect(feedback(wrapper).exists()).toBe(false)
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.get('[aria-label="保留草稿"]').element).toHaveProperty('value', '尚未保存')
    await menu(wrapper, '/catalog').trigger('click')
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('无法打开商品管理')
    expect(loader).toHaveBeenCalledTimes(2)
  })
  it('explicitly refreshes to the failed target after guarded navigation to home succeeds', async () => {
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    const { wrapper, router } = await setup(() => Promise.reject(new Error('cached chunk failure')), undefined, initialPage, '/inventory')
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    await feedback(wrapper).findAll('button').find(button => button.text() === '安全刷新并重试')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(assign).toHaveBeenCalledOnce()
    expect(assign).toHaveBeenCalledWith(router.resolve('/catalog').href)
  })
  it('allows an explicit safe refresh from an already-current home route', async () => {
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    const { wrapper, router } = await setup(() => Promise.reject(new Error('cached chunk failure')))
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await feedback(wrapper).findAll('button').find(button => button.text() === '安全刷新并重试')!.trigger('click'); await flushPromises()
    expect(assign).toHaveBeenCalledWith(router.resolve('/catalog').href)
  })
  it('never refreshes when the original unsaved-content decision is cancelled', async () => {
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    const decision = deferred<boolean>()
    const guard = vi.fn(to => to.path === '/' ? decision.promise : true)
    const guarded = defineComponent({ setup() { onBeforeRouteLeave(guard) }, template: initialPage.template })
    const { wrapper, router } = await setup(() => Promise.reject(new Error('cached chunk failure')), undefined, guarded, '/inventory')
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await feedback(wrapper).findAll('button').find(button => button.text() === '安全刷新并重试')!.trigger('click'); await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    decision.resolve(false); await flushPromises()
    expect(guard).toHaveBeenCalledWith(expect.objectContaining({ path: '/' }), expect.anything(), expect.anything())
    expect(assign).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/inventory')
    expect(wrapper.get('[aria-label="保留草稿"]').element).toHaveProperty('value', '尚未保存')
  })
  it('never refreshes while an existing unknown-write guard refuses to leave', async () => {
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    let unknownWrite = false
    const guarded = defineComponent({ setup() { onBeforeRouteLeave(() => !unknownWrite) }, template: initialPage.template })
    const { wrapper, router } = await setup(() => Promise.reject(new Error('cached chunk failure')), undefined, guarded, '/inventory')
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    unknownWrite = true
    await feedback(wrapper).findAll('button').find(button => button.text() === '安全刷新并重试')!.trigger('click'); await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/inventory')
    expect(wrapper.get('[aria-label="保留草稿"]').element).toHaveProperty('value', '尚未保存')
  })
  it('never refreshes if the session expires while the original leave decision is pending', async () => {
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    const decision = deferred<boolean>()
    const guarded = defineComponent({ setup() { onBeforeRouteLeave(to => to.path === '/' ? decision.promise : true) }, template: initialPage.template })
    const { wrapper } = await setup(() => Promise.reject(new Error('cached chunk failure')), undefined, guarded, '/inventory')
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await feedback(wrapper).findAll('button').find(button => button.text() === '安全刷新并重试')!.trigger('click'); await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    window.dispatchEvent(new Event('admin-session-expired')); await flushPromises()
    decision.resolve(true); await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('会话已失效')
  })
  it('does not let a stale safe refresh replace a newer menu choice', async () => {
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    const decision = deferred<boolean>()
    const guarded = defineComponent({ setup() { onBeforeRouteLeave(to => to.path === '/' ? decision.promise : true) }, template: initialPage.template })
    const { wrapper, router } = await setup(() => Promise.reject(new Error('cached chunk failure')), undefined, guarded, '/inventory')
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await feedback(wrapper).findAll('button').find(button => button.text() === '安全刷新并重试')!.trigger('click'); await flushPromises()
    await menu(wrapper, '/orders').trigger('click'); await flushPromises()
    decision.resolve(true); await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/orders')
  })
  it.each(['identity', 'permission'] as const)('invalidates a pending safe refresh after %s changes', async change => {
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    const decision = deferred<boolean>()
    let currentAccount!: Ref<Account>
    const guarded = defineComponent({ setup() {
      currentAccount = inject<Ref<Account>>('admin-account')!
      onBeforeRouteLeave(to => to.path === '/' ? decision.promise : true)
    }, template: initialPage.template })
    const { wrapper } = await setup(() => Promise.reject(new Error('cached chunk failure')), undefined, guarded, '/inventory')
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await feedback(wrapper).findAll('button').find(button => button.text() === '安全刷新并重试')!.trigger('click'); await flushPromises()
    currentAccount.value = { ...account, accountId: change === 'identity' ? 'another-actor' : account.accountId, permissionCodes: change === 'permission' ? ['inventory.read'] : account.permissionCodes }
    await flushPromises()
    decision.resolve(true); await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    expect(feedback(wrapper).exists()).toBe(false)
  })
  it('only lets the newest of rapid menu requests control the feedback', async () => {
    const older = deferred<any>(), latest = deferred<any>()
    const { wrapper, router } = await setup(() => older.promise, () => latest.promise)
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await menu(wrapper, '/orders').trigger('click'); await flushPromises()
    older.reject(new Error('older chunk failed'))
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('订单管理')
    expect(feedback(wrapper).text()).not.toContain('无法打开')
    latest.resolve(targetPage)
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/orders')
    expect(feedback(wrapper).exists()).toBe(false)
  })
  it('does not let an older click to the same target clear the newer attempt', async () => {
    const older = deferred<any>(), latest = deferred<any>()
    const loader = vi.fn().mockImplementationOnce(() => older.promise).mockImplementationOnce(() => latest.promise)
    const { wrapper } = await setup(loader)
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    older.reject(new Error('older failure'))
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('商品管理')
    expect(feedback(wrapper).text()).not.toContain('无法打开')
    latest.resolve(targetPage)
    await flushPromises()
    expect(feedback(wrapper).exists()).toBe(false)
  })
  it('clears feedback on session expiry and never displays private content after the old chunk resolves', async () => {
    const chunk = deferred<any>()
    const { wrapper } = await setup(() => chunk.promise)
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    window.dispatchEvent(new Event('admin-session-expired'))
    await flushPromises()
    expect(feedback(wrapper).exists()).toBe(false)
    chunk.resolve(targetPage)
    await flushPromises()
    expect(wrapper.find('[data-target]').exists()).toBe(false)
    expect(wrapper.text()).toContain('会话已失效')
  })
  it('preserves modified-click native links and clears duplicated-current-route navigation', async () => {
    const loader = vi.fn().mockResolvedValue(targetPage)
    const { wrapper } = await setup(loader)
    await menu(wrapper, '/catalog').trigger('click', { ctrlKey: true })
    await flushPromises()
    expect(loader).not.toHaveBeenCalled()
    expect(feedback(wrapper).exists()).toBe(false)
    await menu(wrapper, '/').trigger('click'); await flushPromises()
    expect(feedback(wrapper).exists()).toBe(false)
  })
  it('also tracks non-menu router transitions and clears guard redirects', async () => {
    const chunk = deferred<any>()
    const { wrapper, router } = await setup(() => chunk.promise)
    const changing = router.push('/catalog')
    await flushPromises()
    expect(feedback(wrapper).text()).toContain('商品管理')
    chunk.resolve(targetPage)
    await changing; await flushPromises()
    expect(feedback(wrapper).exists()).toBe(false)
    router.beforeEach(to => to.path === '/orders' ? '/' : undefined)
    await menu(wrapper, '/orders').trigger('click'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(feedback(wrapper).exists()).toBe(false)
  })
  it('keeps the current page when a newer click returns to the current route', async () => {
    const chunk = deferred<any>()
    const { wrapper, router } = await setup(() => chunk.promise)
    await menu(wrapper, '/catalog').trigger('click'); await flushPromises()
    await menu(wrapper, '/').trigger('click'); await flushPromises()
    chunk.resolve(targetPage)
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(feedback(wrapper).exists()).toBe(false)
    expect(wrapper.get('[aria-label="保留草稿"]').element).toHaveProperty('value', '尚未保存')
  })
})
