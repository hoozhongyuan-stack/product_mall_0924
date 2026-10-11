import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ElButton, ElInput, ElSelect, ElOption } from 'element-plus'
import AccountsView from '../../src/views/AccountsView.vue'
import GroupsView from '../../src/views/GroupsView.vue'
import MembersView from '../../src/views/MembersView.vue'
import CouponsView from '../../src/views/CouponsView.vue'
import PointsExchangeView from '../../src/views/PointsExchangeView.vue'
import AfterSalesView from '../../src/views/AfterSalesView.vue'
import type {Account} from '../../src/api'
vi.mock('../../src/api', () => ({api:vi.fn(),confirmedWrite:vi.fn(),ApiError:class extends Error{status=409}}))
import {api,confirmedWrite} from '../../src/api'
const account={accountId:'owner',kind:'OWNER',permissionCodes:['account.read','permission.read','member.read','coupon.read','exchange.read','aftersale.read'],groupIds:[]} as unknown as Account
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.clearAllMocks()})
const pager={props:['page','pageSize','total'],emits:['change'],template:`<div><button @click="$emit('change',2,pageSize)">第2页测试</button><button @click="$emit('change',1,50)">50条测试</button></div>`}
const dialog={props:['modelValue'],template:'<div v-if="modelValue" role="dialog"><slot/></div>'}
const global={components:{ElButton,ElInput,ElSelect,ElOption},stubs:{AdminListPager:pager,ElDialog:dialog,PasswordDialog:true,MemberDetailView:{props:['memberId'],template:'<div class="embedded-target">{{memberId}}</div>'},CouponDetailView:{props:['campaignId'],template:'<div class="embedded-target">{{campaignId}}</div>'},PointsExchangeDetailView:{props:['offerId'],template:'<div class="embedded-target">{{offerId}}</div>'},AfterSaleDetailView:{props:['caseId'],template:'<div class="embedded-target">{{caseId}}</div>'}}}
const member={id:'m1',memberNo:'M001',nickname:'会员甲',grade:{name:'普通'},enabled:true,effectiveSpendFen:0,points:{availablePoints:20,frozenPoints:0,debtPoints:0}}
const campaign={id:'c1',title:'活动甲',code:'C1',status:'DRAFT',productIds:[],kind:'CASH',discountFen:100,totalQuantity:10,issuedQuantity:0,remainingQuantity:10,validFrom:'2026-10-01T00:00:00Z',validUntil:'2026-11-01T00:00:00Z'}
const offer={id:'e1',productName:'兑换甲',skuCode:'S1',status:'DRAFT',specs:[],pointsPrice:20,saleUnit:'瓶',fulfillmentKind:'SHIP'}
const aftersale={caseId:'a1',orderNo:'D001',title:'售后甲',status:'PENDING_REVIEW',quantity:1,amountFen:100,kind:'REFUND'}
async function setup(view:unknown,path:string,rows:unknown){vi.mocked(api).mockResolvedValue(rows);const router=createRouter({history:createMemoryHistory(),routes:[{path,component:view as never},{path:`${path}/:id`,component:{template:'<div>独立详情</div>'}}]});await router.push(path);await router.isReady();const w=mount(view as never,{props:{account},global:{...global,plugins:[router]}});wrappers.push(w);await flushPromises();return{w,router}}
describe('list operation interactions',()=>{
 for(const [view,path,item,url,target] of [[MembersView,'/members',member,'/members?','m1'],[CouponsView,'/coupons',campaign,'/coupon-campaigns?','c1'],[PointsExchangeView,'/exchange-offers',offer,'/exchange-offers?','e1'],[AfterSalesView,'/aftersales',aftersale,'/aftersales?','a1']] as const){
  it(`${path} clears selection on real server page changes and opens embedded details without navigating`,async()=>{
   const rows=path==='/aftersales'?{items:[item],total:51,page:1,pageSize:20}:{items:[item],grades:[],pagination:{total:51,page:1,pageSize:20}}
   const {w,router}=await setup(view,path,rows)
   await w.get('tbody input[type=checkbox]').setValue(true);expect(w.text()).toContain('已选 1')
   await w.findAll('button').find(x=>x.text()==='50条测试')!.trigger('click');await flushPromises()
   expect(vi.mocked(api).mock.lastCall?.[0]).toContain(url);expect(vi.mocked(api).mock.lastCall?.[0]).toContain('pageSize=50');expect(w.get('tbody input[type=checkbox]').element).toHaveProperty('checked',false)
   await w.get('tbody a').trigger('click');await flushPromises()
   expect(router.currentRoute.value.path).toBe(path);expect(w.get('.embedded-target').text()).toBe(target)
   expect(confirmedWrite).not.toHaveBeenCalled()
  })
 }
 it('accounts paginates the complete authorized array and applies search/reset without bulk permission writes',async()=>{
  const rows=Array.from({length:25},(_,i)=>({accountId:`a${i}`,loginName:`operator${i}`,displayName:`运营${i}`,kind:'STAFF',groupIds:[],enabled:true}))
  vi.mocked(api).mockImplementation(async path=>path==='/accounts'?rows:[])
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/accounts',component:AccountsView}]});await router.push('/accounts');await router.isReady()
  const w=mount(AccountsView,{props:{account},global:{...global,plugins:[router]}});wrappers.push(w);await flushPromises();expect(w.findAll('tbody tr')).toHaveLength(20)
  await w.get('tbody input').setValue(true);await w.findAll('button').find(x=>x.text()==='第2页测试')!.trigger('click');expect(w.findAll('tbody tr')).toHaveLength(5);expect(w.get('tbody input').element).toHaveProperty('checked',false)
  await w.get('form input').setValue('operator24');await w.get('form').trigger('submit');expect(w.findAll('tbody tr')).toHaveLength(1)
  await w.findAll('button').find(x=>x.text()==='重置')!.trigger('click');expect(w.findAll('tbody tr')).toHaveLength(20);expect(confirmedWrite).not.toHaveBeenCalled()
 })
 it('permission groups render a selectable table with modal read-only comparison',async()=>{
  const {w}=await setup(GroupsView,'/groups',[{groupId:'g',name:'运营',code:'OPS',permissionCodes:['catalog.read'],enabled:true}])
  await w.get('tbody input').setValue(true);await w.findAll('button').find(x=>x.text()==='批量查看')!.trigger('click');await flushPromises();expect(w.get('[role=dialog]').text()).toContain('catalog.read');expect(confirmedWrite).not.toHaveBeenCalled()
 })
})
