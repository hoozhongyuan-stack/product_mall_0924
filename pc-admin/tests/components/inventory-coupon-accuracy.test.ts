import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ElementPlus from 'element-plus'
import InventoryView from '../../src/views/InventoryView.vue'
import CouponsView from '../../src/views/CouponsView.vue'
import CouponDetailView from '../../src/views/CouponDetailView.vue'
import CouponIssuancePanel from '../../src/views/coupons/CouponIssuancePanel.vue'
import type { Account } from '../../src/api'
import type { Campaign } from '../../src/views/coupons/types'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
import { api } from '../../src/api'
const account = { accountId: 'accuracy', permissionCodes: ['inventory.read','coupon.read','coupon.issue','member.read','coupon.publish'] } as Account
const campaign = { id:'expired', code:'ENDED', title:'已结束测试券', kind:'CASH', minGoodsFen:0, discountFen:100, productIds:[], productNames:[], validFrom:'2026-10-01T00:00:00Z', validUntil:'2026-10-10T00:00:00Z', status:'PUBLISHED', revision:1, totalQuantity:100, issuedQuantity:1, remainingQuantity:99, selfClaimLimit:1, claimMode:'BOTH', issuanceEnabled:true, redeemEligible:false } as Campaign
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w=>w.unmount()); vi.useRealTimers(); vi.clearAllMocks() })
async function setup(component: typeof InventoryView | typeof CouponsView | typeof CouponDetailView, path:string) {
 const router=createRouter({history:createMemoryHistory(),routes:[{path, component, meta:{inventoryTab:'balances'}}]})
 await router.push(path.replace(':campaignId','expired'));await router.isReady()
 const wrapper=mount(component,{props:{account},global:{plugins:[router,ElementPlus]}});wrappers.push(wrapper);await flushPromises();return wrapper
}
describe('accurate inventory and coupon presentation',()=>{
 it('renders unit-specific totals and never a mixed-unit 899',async()=>{
  const balances=[{warehouseId:'w',skuId:'a',productName:'A',baseUnit:'件',onHandBaseUnits:480,reservedBaseUnits:0,availableBaseUnits:480,poolSkuCodes:['A'],poolAnchorSkuCode:'A'}, {warehouseId:'w',skuId:'b',productName:'B',baseUnit:'瓶',onHandBaseUnits:419,reservedBaseUnits:2,availableBaseUnits:417,poolSkuCodes:['B','B-BOX'],poolAnchorSkuCode:'B'}]
  vi.mocked(api).mockImplementation(async path=>String(path).startsWith('/warehouses')?{items:[]} as never:{items:balances,page:1,pageSize:20,total:2} as never)
  const wrapper=await setup(InventoryView,'/inventory')
  const totals=wrapper.get('[aria-label="当前页库存汇总"]').text()
  expect(totals).toContain('480 件');expect(totals).toContain('419 瓶');expect(totals).toContain('417 瓶');expect(totals).not.toContain('899')
 })
 it('uses the same ended lifecycle in list, detail and disabled issuance',async()=>{
  vi.useFakeTimers({toFake:['Date','setInterval','clearInterval']});vi.setSystemTime(new Date('2026-10-10T00:00:00Z'))
  vi.mocked(api).mockResolvedValue({items:[campaign],pagination:{page:1,pageSize:20,total:1}} as never)
  const list=await setup(CouponsView,'/coupons')
  expect(list.text()).toContain('已结束');expect(list.text()).not.toContain('发放开启')
  vi.mocked(api).mockImplementation(async path=>String(path).includes('/issuances?')?{items:[],pagination:{page:1,pageSize:20,total:0}} as never:campaign as never)
  const detail=await setup(CouponDetailView,'/coupons/:campaignId')
  expect(detail.text()).toContain('活动已结束，不能继续发券。');expect(detail.text()).not.toContain('允许新增领取与发放')
  expect(detail.findAll('button').find(b=>b.text()==='向会员发券')!.attributes('disabled')).toBeDefined()
  expect(detail.findAll('button').find(b=>b.text()==='暂停新增发放')!.attributes('disabled')).toBeDefined()
 })
 it('updates an open issuance panel at expiry and prevents a new issue request',async()=>{
  vi.useFakeTimers({toFake:['Date','setInterval','clearInterval']});vi.setSystemTime(new Date('2026-10-09T23:59:59Z'))
  vi.mocked(api).mockResolvedValue({items:[],pagination:{page:1,pageSize:20,total:0}} as never)
  const wrapper=mount(CouponIssuancePanel,{props:{account,campaign,disabled:false},global:{plugins:[ElementPlus],stubs:{RouterLink:true}}});wrappers.push(wrapper);await flushPromises()
  const button=wrapper.findAll('button').find(b=>b.text()==='向会员发券')!
  expect(button.attributes('disabled')).toBeUndefined()
  await button.trigger('click');await vi.advanceTimersByTimeAsync(1000);await flushPromises()
  expect(button.attributes('disabled')).toBeDefined();expect(wrapper.text()).toContain('活动已结束，不能继续发券。')
  expect(wrapper.findAll('button').find(b=>b.text()==='确认发放')!.attributes('disabled')).toBeDefined()
  expect(vi.mocked(api).mock.calls.every(call=>!String(call[0]).endsWith('/issuances'))).toBe(true)
 })
})
