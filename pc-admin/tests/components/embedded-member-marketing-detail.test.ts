import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ElButton, ElInput } from 'element-plus'
import MemberDetailView from '../../src/views/MemberDetailView.vue'
import CouponDetailView from '../../src/views/CouponDetailView.vue'
import PointsExchangeDetailView from '../../src/views/PointsExchangeDetailView.vue'
import type {Account} from '../../src/api'
vi.mock('../../src/api',async original=>({...await original<object>(),api:vi.fn()}))
vi.mock('../../src/shared/confirm',()=>({confirmAction:vi.fn()}))
import {api} from '../../src/api'
import {confirmAction} from '../../src/shared/confirm'
const id='11a27082-c544-4ed8-8b70-3c1070bf560b',sku='21a27082-c544-4ed8-8b70-3c1070bf560b'
const account={accountId:'owner',permissionCodes:['member.read','coupon.read','coupon.manage','exchange.read','exchange.manage']} as Account
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.clearAllMocks();sessionStorage.clear()})
async function routerFor(path:string){const router=createRouter({history:createMemoryHistory(),routes:[{path,component:{template:'<div/>'}}]});await router.push(path.replace(':memberId',id));await router.isReady();return router}
const offer={id,skuId:sku,productId:sku,productName:'兑换商品',skuCode:'SKU',saleUnit:'瓶',pointsPrice:20,fulfillmentKind:'SHIP',status:'DRAFT',specs:[],revision:1,createdAt:'2026-10-11T00:00:00Z'}
describe('embedded details retain domain lifecycle',()=>{
 it('reads the explicit embedded member identity while standalone member deep links still read route params',async()=>{
  vi.mocked(api).mockImplementation(async path=>String(path).includes('/points?')?{items:[],pagination:{total:0}}:{memberNo:'M001',grade:{name:'普通'},enabled:true,points:{availablePoints:0,settledPoints:0,frozenPoints:0,debtPoints:0},effectiveSpendFen:0})
  const router=await routerFor('/members')
  const w=mount(MemberDetailView,{props:{account,memberId:id,embedded:true},global:{plugins:[router]}});wrappers.push(w);await flushPromises()
  expect(api).toHaveBeenCalledWith(`/members/${id}`);expect(w.text()).not.toContain('返回会员列表')
  w.unmount();vi.mocked(api).mockClear()
  const deepRouter=await routerFor('/members/:memberId')
  const deep=mount(MemberDetailView,{props:{account},global:{plugins:[deepRouter]}});wrappers.push(deep);await flushPromises();expect(api).toHaveBeenCalledWith(`/members/${id}`);expect(deep.text()).toContain('返回会员列表')
 })
 it('refuses to close a changed coupon draft when discard is cancelled or the form changes during confirmation',async()=>{
  const router=await routerFor('/coupons');vi.mocked(confirmAction).mockResolvedValue(false)
  const editor={name:'CampaignEditor',props:['modelValue'],emits:['update:modelValue'],template:'<div class="editor" />'}
  const w=mount(CouponDetailView,{props:{account,campaignId:'new',embedded:true},global:{plugins:[router],components:{ElButton},stubs:{CampaignEditor:editor,CouponRulePreview:true,CouponIssuancePanel:true}}});wrappers.push(w);await flushPromises()
  const child=w.getComponent({name:'CampaignEditor'}),form=child.props('modelValue')
  child.vm.$emit('update:modelValue',{...form,title:'初次编辑'});await flushPromises()
  const detail=w.vm as unknown as {mayClose:()=>Promise<boolean>};expect(await detail.mayClose()).toBe(false)
  let resolve!:(value:boolean)=>void;vi.mocked(confirmAction).mockReturnValueOnce(new Promise(done=>{resolve=done}))
  const closing=detail.mayClose();child.vm.$emit('update:modelValue',{...form,title:'确认期间编辑'});await flushPromises();resolve(true);expect(await closing).toBe(false)
  vi.mocked(confirmAction).mockResolvedValue(true);expect(await detail.mayClose()).toBe(true)
 })
 it('restores unknown exchange requests in the embedded identity and retains the original key after refusing close',async()=>{
  const key='31a27082-c544-4ed8-8b70-3c1070bf560b';const pending={key,path:`/exchange-offers/${id}`,method:'PUT',body:{expectedRevision:1,pointsPrice:30},action:''}
  sessionStorage.setItem(`mall:exchange:owner:${id}`,JSON.stringify(pending));vi.mocked(api).mockResolvedValue(offer);vi.mocked(confirmAction).mockResolvedValue(false)
  const router=await routerFor('/exchange-offers'),w=mount(PointsExchangeDetailView,{props:{account,offerId:id,embedded:true},global:{plugins:[router],components:{ElButton,ElInput}}});wrappers.push(w);await flushPromises()
  expect(w.text()).toContain('操作结果待确认');expect(w.text()).not.toContain('返回积分商城商品');expect(await (w.vm as unknown as {mayClose:()=>Promise<boolean>}).mayClose()).toBe(false)
  expect(JSON.parse(sessionStorage.getItem(`mall:exchange:owner:${id}`)!)).toEqual(pending)
 })
})
