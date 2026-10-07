import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import CouponDetailView from '../../src/views/CouponDetailView.vue'
import { ElInput, ElSelect, ElOption, ElButton } from 'element-plus'
import CouponsView from '../../src/views/CouponsView.vue'
import CouponRulePreview from '../../src/views/coupons/CouponRulePreview.vue'
import CampaignEditor from '../../src/views/coupons/CampaignEditor.vue'
import CouponIssuancePanel from '../../src/views/coupons/CouponIssuancePanel.vue'
import type { Campaign } from '../../src/views/coupons/types'
import type { Account } from '../../src/api'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
vi.mock('../../src/shared/confirm', () => ({ confirmAction: vi.fn() }))
import { confirmAction } from '../../src/shared/confirm'
import { api } from '../../src/api'
const account = { accountId: 'a', permissionCodes: ['coupon.read', 'coupon.manage'] } as Account
const campaign = { id:'c', code:'AUTUMN', title:'秋日礼遇长名称活动', kind:'FULL_REDUCTION', minGoodsFen:10000, discountFen:1000, productIds:[], validFrom:'2026-10-01T00:00:00Z', validUntil:'2026-11-01T00:00:00Z', issuedQuantity:1, totalQuantity:100, remainingQuantity:99, status:'PUBLISHED', issuanceEnabled:false, claimMode:'BOTH', redeemEligible:false }
const Pagination = defineComponent({ props:['currentPage','pageSize','total'], emits:['current-change','size-change'], template:'<div class="test-pagination"><button @click="$emit(\'size-change\',50)">50条</button><button @click="$emit(\'current-change\',2)">第2页</button></div>' })
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w=>w.unmount()); vi.clearAllMocks() })
const global = { components: { ElInput, ElSelect, ElOption, ElButton }, stubs:{ RouterLink:{ template:'<a><slot/></a>' }, ElPagination:Pagination } }
describe('coupon workspace layout',()=>{
 it('shows incomplete amounts and a fallback title before a coupon is filled in',()=>{
  const wrapper=mount(CouponRulePreview,{props:{title:' ',kind:'FULL_REDUCTION',minimum:'invalid',discount:'',productCount:2}});wrappers.push(wrapper)
  expect(wrapper.text()).toContain('优惠券活动')
  expect(wrapper.text()).toContain('满 待填写 减 待填写')
  expect(wrapper.text()).toContain('指定 2 个商品')
 })
 it('uses the common table surface and accessible issuance progress, with explicit paused state',async()=>{
  vi.mocked(api).mockResolvedValue({items:[campaign],pagination:{page:1,pageSize:20,total:101}})
  const wrapper=mount(CouponsView,{props:{account},global});wrappers.push(wrapper);await flushPromises()
  expect(wrapper.find('.table-wrap table').exists()).toBe(true)
  expect(wrapper.find('progress').attributes('max')).toBe('100')
  expect(wrapper.find('progress').attributes('value')).toBe('1')
  expect(wrapper.text()).toContain('发放暂停')
  await wrapper.get('.test-pagination button').trigger('click');await flushPromises()
  expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toContain('pageSize=50')
  expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toContain('page=1')
  await wrapper.findAll('.test-pagination button')[1]!.trigger('click');await flushPromises()
  expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toContain('page=2')
 })
 it('keeps legacy quota explicitly unknown and supports retry after a read error',async()=>{
  vi.mocked(api).mockRejectedValueOnce(new Error('暂时无法读取')).mockResolvedValue({items:[{...campaign,status:'LEGACY'}],pagination:{page:1,pageSize:20,total:1}})
  const wrapper=mount(CouponsView,{props:{account},global});wrappers.push(wrapper);await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('暂时无法读取')
  await wrapper.get('[role=alert] button').trigger('click');await flushPromises()
  expect(wrapper.find('progress').exists()).toBe(false)
  expect(wrapper.text()).toContain('历史发放额度未登记')
 })
 it('keeps the empty state inside the common table and hides writes from readers',async()=>{
  vi.mocked(api).mockResolvedValue({items:[],pagination:{page:1,pageSize:20,total:0}})
  const wrapper=mount(CouponsView,{props:{account:{...account,permissionCodes:['coupon.read']}},global});wrappers.push(wrapper);await flushPromises()
  expect(wrapper.get('.table-wrap .empty-state').text()).toContain('暂无匹配活动')
  expect(wrapper.text()).not.toContain('创建活动')
 })
 it('renders issuance records with the common table and sends the selected page size',async()=>{
  vi.mocked(api).mockResolvedValue({items:[{id:'i',memberId:'member',memberLabel:'m20261007123456A7x',kind:'ADMIN',quantity:1,actorLabel:'运营',reason:'活动奖励',createdAt:'2026-10-07T00:00:00Z'}],pagination:{page:1,pageSize:20,total:101}})
  const wrapper=mount(CouponIssuancePanel,{props:{account,campaign:{...campaign,productNames:[],revision:1,selfClaimLimit:1} as Campaign,disabled:false},global});wrappers.push(wrapper);await flushPromises()
  expect(wrapper.get('.table-wrap').text()).toContain('活动奖励')
  expect(wrapper.text()).not.toContain('向会员发券')
  await wrapper.get('.test-pagination button').trigger('click');await flushPromises()
  expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toContain('issuances?page=1&pageSize=50')
 })
 it('shows a live rule summary and separates editing sections without changing the model', async()=>{
  const form={code:'A',title:'秋日券',kind:'FULL_REDUCTION' as const,minimum:'100',discount:'10',productIds:[],redeemEligible:false,validFrom:'2026-10-01T00:00',validUntil:'2026-11-01T00:00',totalQuantity:'100',selfClaimLimit:'1',claimMode:'BOTH' as const,issuanceEnabled:true}
  const wrapper=mount(CampaignEditor,{props:{modelValue:form,account,disabled:false,productNames:[]},global});wrappers.push(wrapper)
  expect(wrapper.find('.coupon-rule-preview').text()).toContain('满 ¥100.00 减 ¥10.00')
  expect(wrapper.findAll('h2').map(n=>n.text())).toEqual(['基本信息','优惠规则','有效期','适用商品','领取与发放'])
  await wrapper.setProps({modelValue:{...form,kind:'CASH',discount:'25'}})
  expect(wrapper.find('.coupon-rule-preview').text()).toContain('现金券 ¥25.00')
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
 })
})

describe('coupon refresh confirmation',()=>{
 it('retains a changed draft if edits continue while the asynchronous discard decision is open',async()=>{
  vi.mocked(api).mockImplementation(async path=>String(path).includes('/issuances?')?{items:[],pagination:{page:1,pageSize:20,total:0}}:{...campaign,status:'DRAFT',productNames:[],revision:1,selfClaimLimit:1})
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/coupons/:campaignId',component:CouponDetailView}]})
  await router.push('/coupons/c');await router.isReady()
  const wrapper=mount(CouponDetailView,{props:{account},global:{...global,plugins:[router]}});wrappers.push(wrapper);await flushPromises()
  const editor=wrapper.getComponent(CampaignEditor)
  const form=editor.props('modelValue')
  editor.vm.$emit('update:modelValue',{...form,title:'第一次编辑'});await flushPromises()
  let resolve!: (value:boolean)=>void
  vi.mocked(confirmAction).mockReturnValueOnce(new Promise<boolean>(done=>{resolve=done}))
  await wrapper.findAll('button').find(button=>button.text()==='刷新活动')!.trigger('click')
  editor.vm.$emit('update:modelValue',{...form,title:'确认期间继续编辑'});await flushPromises()
  const count=vi.mocked(api).mock.calls.length
  resolve(true);await flushPromises()
  expect(vi.mocked(api).mock.calls).toHaveLength(count)
  expect(wrapper.getComponent(CampaignEditor).props('modelValue').title).toBe('确认期间继续编辑')
 })
})
