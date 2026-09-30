import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import PaymentSettingsView from '../../src/views/PaymentSettingsView.vue'
import { api } from '../../src/api'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const policy = { instructions: '请转账', merchantAccountId: 'bank', offlineTimeoutMinutes: 600, wechatTimeoutMinutes: 45, revision: 3, configured: true, availablePaymentMethods: [], offlineEnabled: false, wechatEnabled: false, wechatConfigurationStatus: 'NOT_CONFIGURED' }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => vi.mocked(api).mockResolvedValue(policy))
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
async function setup() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: PaymentSettingsView }] })
  await router.push('/'); await router.isReady()
  const wrapper = mount(PaymentSettingsView, { global: { plugins: [router] } })
  wrappers.push(wrapper); await flushPromises(); return wrapper
}
it('allows both modes independently and submits enabled flags with the revision', async () => {
  const wrapper = await setup()
  await wrapper.get('input[data-payment-method="OFFLINE"]').setValue(true)
  await wrapper.get('input[data-payment-method="WECHAT"]').setValue(true)
  expect(wrapper.text()).toContain('未完成商户配置')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(api).toHaveBeenLastCalledWith('/payments/offline-policy', { method: 'PUT', body: JSON.stringify({ instructions: '请转账', merchantAccountId: 'bank', wechatTimeoutMinutes: 45, offlineTimeoutMinutes: 600, offlineEnabled: true, wechatEnabled: true, expectedRevision: 3 }) })
})
it('shows legacy enabled selections when the old response has no flags', async () => {
  vi.mocked(api).mockResolvedValue({ ...policy, offlineEnabled: undefined, wechatEnabled: undefined, availablePaymentMethods: ['OFFLINE'] })
  const wrapper = await setup()
  expect((wrapper.get('input[data-payment-method="OFFLINE"]').element as HTMLInputElement).checked).toBe(true)
  expect((wrapper.get('input[data-payment-method="WECHAT"]').element as HTMLInputElement).checked).toBe(false)
})
