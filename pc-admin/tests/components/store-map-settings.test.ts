import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import StoreMapSettingsView from '../../src/views/stores/StoreMapSettingsView.vue'
import { api, confirmedWrite, type Account } from '../../src/api'
vi.mock('../../src/api', () => ({ api: vi.fn(), confirmedWrite: vi.fn() }))
const account = { accountId: 'admin', permissionCodes: ['stores.manage'] } as Account
const config = { revision: 2, managed: true, source: 'MANAGED', encryptionReady: true, available: true, webServiceKey: { configured: true, tail: '1234' }, jsApiKey: { configured: false, tail: '' }, jsSecurityCode: { configured: false, tail: '' } }
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.resetAllMocks() })
async function setup(value = config) {
 vi.mocked(api).mockImplementation(async path => path.startsWith('/stores/profit-rules') ? {items:[],total:0} as never : path === '/stores/settlement-settings' ? { receivedWindowDays: 7, revision: 1, appliesTo: 'NEW_ORDERS' } as never : value as never)
 const wrapper = mount(StoreMapSettingsView, { props: { account } }); wrappers.push(wrapper)
 await flushPromises(); return wrapper
}
describe('store map credentials', () => {
 it('shows metadata only and uses empty password fields', async () => {
  const w = await setup()
  expect(w.text()).toContain('已配置'); expect(w.text()).toContain('1234')
  expect(w.findAll('input[type="password"]')).toHaveLength(3)
  expect(w.findAll('input[type="password"]').every(input => (input.element as HTMLInputElement).value === '')).toBe(true)
 })
 it('saves replacements with revision-bound confirmation and clears sensitive inputs', async () => {
  const w = await setup()
  vi.mocked(confirmedWrite).mockResolvedValue({ ...config, revision: 3 })
  await w.get('#amap-webServiceKey').setValue('synthetic-web-key')
  await w.get('form[aria-label="高德参数配置"]').trigger('submit')
  await w.get('#amap-password').setValue('synthetic-password')
  await w.get('form[aria-label="确认保存地图配置"]').trigger('submit'); await flushPromises()
  expect(confirmedWrite).toHaveBeenCalledWith('stores.map.configure', 'synthetic-password', '/stores/map-settings', 'PUT', { expectedRevision: 2, webServiceKey: 'synthetic-web-key', jsApiKey: '', jsSecurityCode: '' }, 'amap', 2)
  expect(w.text()).toContain('配置已保存'); expect(w.findAll('input[type="password"]').every(input => (input.element as HTMLInputElement).value === '')).toBe(true)
 })
 it('requires paired JS credentials and encryption before enabling save', async () => {
  const w = await setup()
  await w.get('#amap-jsApiKey').setValue('synthetic-js-key')
  expect(w.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  expect(w.text()).toContain('一起配置')
  const locked = await setup({ ...config, encryptionReady: false })
  await locked.get('#amap-webServiceKey').setValue('synthetic-web-key')
  expect(locked.get('button[type="submit"]').attributes('disabled')).toBeDefined()
 })
 it('shows unavailable credentials and allows recovery with a replacement Web key', async () => {
  const w = await setup({ ...config, available: false, jsApiKey: { configured: true, tail: '' } })
  expect(w.text()).toContain('当前地图凭据不可用')
  await w.get('#amap-webServiceKey').setValue('synthetic-recovery-key')
  expect(w.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
 })
 it('discards secrets and requires reread after an uncertain save failure', async () => {
  const w = await setup()
  vi.mocked(confirmedWrite).mockRejectedValue(new Error('synthetic-web-key'))
  await w.get('#amap-webServiceKey').setValue('synthetic-web-key')
  await w.get('form[aria-label="高德参数配置"]').trigger('submit')
  await w.get('#amap-password').setValue('synthetic-password')
  await w.get('form[aria-label="确认保存地图配置"]').trigger('submit'); await flushPromises()
  expect(w.text()).not.toContain('synthetic-web-key'); expect(w.text()).toContain('重新读取')
  expect(w.get('#amap-webServiceKey').element).toHaveProperty('value', '')
  expect(w.get('button[type="submit"]').attributes('disabled')).toBeDefined()
 })
})
