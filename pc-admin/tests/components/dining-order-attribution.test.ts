import {afterEach,it,expect,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import {createRouter,createMemoryHistory} from 'vue-router'
import OrdersView from '../../src/views/OrdersView.vue'
import {api,type Account} from '../../src/api'
import {adminSections} from '../../src/navigation'
vi.mock('../../src/api',()=>({api:vi.fn()}))
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks()})
it('puts alliance orders under orders and removes duplicate cost navigation',()=>{
 expect(adminSections.find(s=>s.name==='订单')!.paths).toContain('/orders/dining-alliance')
 expect(adminSections.find(s=>s.name==='餐饮联盟')!.paths).not.toContain('/dining-alliance/orders')
 expect(adminSections.find(s=>s.name==='餐饮联盟')!.paths).not.toContain('/dining-alliance/purchase-costs')
})
it('queries ownership and fulfillment separately while rendering historical snapshots',async()=>{
 vi.mocked(api).mockImplementation(async path=>path.startsWith('/dining-alliance/restaurants')?{items:[{id:'r1',name:'测试餐厅'}]}:path.startsWith('/stores')?{items:[{id:'s1',name:'测试前置仓'}]}:{items:[{orderId:'o1',orderNo:'ORDER1',status:'PENDING_PAYMENT',source:'DINING',dining:{name:'历史餐厅',tableNumber:'A01'},storeName:'历史前置仓',fulfillmentWarehouse:{id:'w1',name:'历史库'},payableFen:100,createdAt:'2026-10-11',expiresAt:'2026-10-12'}],total:1,pageSize:20})
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:OrdersView}]});await router.push('/')
 const w=mount(OrdersView,{props:{account:{permissionCodes:['order.read','stores.read','dining.read']} as Account},global:{plugins:[router],stubs:{ElDialog:true}}});wrappers.push(w);await flushPromises()
 const selects=w.findAll('select');await selects[0]!.setValue('s1');await selects[1]!.setValue('DINING');await selects[2]!.setValue('r1');await w.find('form').trigger('submit');await flushPromises()
 expect(vi.mocked(api).mock.calls.some(c=>c[0].includes('source=DINING')&&c[0].includes('restaurantId=r1')&&c[0].includes('storeId=s1'))).toBe(true)
 expect(w.text()).toContain('历史餐厅');expect(w.text()).toContain('A01');expect(w.text()).toContain('历史前置仓');expect(w.text()).toContain('历史库')
})
