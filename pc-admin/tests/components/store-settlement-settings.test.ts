import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import StoreSettlementSettingsPanel from '../../src/views/stores/StoreSettlementSettingsPanel.vue'
import { api, confirmedWrite, type Account } from '../../src/api'
vi.mock('../../src/api',()=>({api:vi.fn(),confirmedWrite:vi.fn()}))
const account={accountId:'admin',permissionCodes:['stores.manage']} as Account
const value={receivedWindowDays:7,revision:2,appliesTo:'NEW_ORDERS'}
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks()})
async function setup(){vi.mocked(api).mockResolvedValue(value);const w=mount(StoreSettlementSettingsPanel,{props:{account}});wrappers.push(w);await flushPromises();return w}
describe('store aftersale settings',()=>{
 it('loads the server period and explains snapshot and funding boundaries',async()=>{
  const w=await setup();expect(w.get('#store-aftersale-days').element).toHaveProperty('value','7')
  expect(w.text()).toContain('旧订单保持原期限');expect(w.text()).toContain('设置售后期不会提前入账')
 })
 it('confirms integer days with independent revision then clears password',async()=>{
  const w=await setup();vi.mocked(confirmedWrite).mockResolvedValue({...value,receivedWindowDays:10,revision:3})
  await w.get('#store-aftersale-days').setValue('10');await w.get('form[aria-label="售后期设置"]').trigger('submit')
  await w.get('#store-settlement-password').setValue('synthetic-password');await w.get('form[aria-label="确认保存售后期"]').trigger('submit');await flushPromises()
  expect(confirmedWrite).toHaveBeenCalledWith('stores.settlement.configure','synthetic-password','/stores/settlement-settings','PUT',{expectedRevision:2,receivedWindowDays:10},'aftersale-policy',2)
  expect(w.text()).toContain('售后期已保存');expect(w.find('#store-settlement-password').exists()).toBe(false)
 })
 it('rejects fractions and out of range periods',async()=>{
  const w=await setup()
  for(const invalid of ['','0','366','2.5']){await w.get('#store-aftersale-days').setValue(invalid);expect(w.get('button[type="submit"]').attributes('disabled')).toBeDefined()}
 })
 it('requires rereading after an ambiguous write and never exposes input password',async()=>{
  const w=await setup();vi.mocked(confirmedWrite).mockRejectedValue(new Error('synthetic-password'))
  await w.get('#store-aftersale-days').setValue('10');await w.get('form[aria-label="售后期设置"]').trigger('submit')
  await w.get('#store-settlement-password').setValue('synthetic-password');await w.get('form[aria-label="确认保存售后期"]').trigger('submit');await flushPromises()
  expect(w.text()).not.toContain('synthetic-password');expect(w.text()).toContain('重新读取售后期');expect(w.get('button[type="submit"]').attributes('disabled')).toBeDefined()
 })
})
