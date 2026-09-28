import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute } from 'vue-router'
import { useCouponOperation } from '../../src/views/coupons/operation'
import type { Account } from '../../src/api'

const account: Account = { accountId: 'a1', loginName: 'owner', displayName: 'Owner', kind: 'OWNER', enabled: true, revision: 1, groupIds: [], permissionCodes: ['coupon.read', 'coupon.manage'] }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => vi.stubGlobal('confirm', vi.fn(() => false)))
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); sessionStorage.clear() })
async function setup() {
  const dirty = ref(true)
  let operation!: ReturnType<typeof useCouponOperation>
  const page = defineComponent({ setup() {
    const route = useRoute()
    operation = useCouponOperation(() => account, () => String(route.params.id), () => {}, () => dirty.value)
    return () => h('input', { value: route.params.id })
  } })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/coupons/:id', component: page }, { path: '/other', component: { template: '<div>other</div>' } }] })
  await router.push('/coupons/one')
  await router.isReady()
  wrappers.push(mount(RouterView, { global: { plugins: [router] } }))
  await flushPromises()
  return { router, dirty, operation }
}

describe('coupon route guards', () => {
  it('keeps the current campaign and form when same-component navigation is cancelled', async () => {
    const { router } = await setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await router.push('/coupons/two')
    expect(router.currentRoute.value.params.id).toBe('one')
    expect(confirm).toHaveBeenCalledWith('有未保存修改，确定离开？')
    confirm.mockReturnValue(true)
    await router.push('/coupons/two')
    expect(router.currentRoute.value.params.id).toBe('two')
  })
  it('blocks a parameter switch while a write is busy without a discard prompt', async () => {
    const { router, operation } = await setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    operation.busy.value = true
    await router.push('/coupons/two')
    expect(router.currentRoute.value.params.id).toBe('one')
    expect(confirm).not.toHaveBeenCalled()
  })
  it('protects unresolved operations even if the form is clean and retains the original identity', async () => {
    const { router, operation, dirty } = await setup()
    dirty.value = false
    operation.pending.value = { key: 'original', path: '/coupons/one', method: 'PUT', body: {}, action: '' }
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await router.push('/coupons/two')
    expect(router.currentRoute.value.params.id).toBe('one')
    expect(operation.pending.value?.key).toBe('original')
  })
  it('still protects route leave and allows clean navigation', async () => {
    const { router, dirty } = await setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await router.push('/other')
    expect(router.currentRoute.value.path).toBe('/coupons/one')
    dirty.value = false
    await router.push('/coupons/two')
    expect(router.currentRoute.value.params.id).toBe('two')
  })
})
