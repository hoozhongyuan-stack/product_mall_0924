import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import StoresView from '../../src/views/stores/StoresView.vue'
import StoreAccountsView from '../../src/views/stores/StoreAccountsView.vue'
import { api, type Account } from '../../src/api'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
const account = { permissionCodes: ['stores.read','stores.manage','stores.accounts.read'] } as Account
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers.length = 0; vi.clearAllMocks() })
describe('store management', () => {
 it('shows store state and sends an explicit search', async () => {
  vi.mocked(api).mockResolvedValue({ items: [{ id:'s1',name:'湘江路店',contactName:'张店长',contactPhone:'13800138000',address:'湘江路 12 号',enabled:true,acceptingOrders:false,supportedModes:['PICKUP'],openingHours:'09:00–21:00' }],total:1 })
  const w=mount(StoresView,{props:{account},global:{stubs:['RouterLink']}});wrappers.push(w);await flushPromises()
  expect(w.text()).toContain('湘江路店');expect(w.text()).toContain('暂停接单');expect(w.text()).toContain('到店自提')
  await w.find('input[type="search"]').setValue('湘江');await w.find('form').trigger('submit');await flushPromises();expect(vi.mocked(api).mock.lastCall?.[0]).toContain('search=%E6%B9%98%E6%B1%9F')
 })
 it('never formats an unconfigured account as zero income',async()=>{
  vi.mocked(api).mockResolvedValue({items:[{storeId:'s1',storeName:'湘江路店',settlementReady:false,reason:'结算规则尚未配置',balance:{pendingFen:null,availableFen:null,frozenFen:null,paidFen:null}}],total:1})
  const w=mount(StoreAccountsView,{props:{account},global:{stubs:['RouterLink']}});wrappers.push(w);await flushPromises();expect(w.text()).toContain('待配置');expect(w.text()).not.toContain('¥0.00')
 })
})
