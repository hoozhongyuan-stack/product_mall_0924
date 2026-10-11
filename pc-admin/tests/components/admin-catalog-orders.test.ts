import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {flushPromises,mount} from '@vue/test-utils'
import OrdersView from '../../src/views/OrdersView.vue'
import OrderDetailDialog from '../../src/views/orders/OrderDetailDialog.vue'
import ProductManagement from '../../src/views/catalog/ProductManagement.vue'
import {api} from '../../src/api'
vi.mock('../../src/api',()=>({api:vi.fn(),ApiError:class extends Error{}}))
const account={accountId:'admin',permissionCodes:['catalog.read','fulfillment.ship']} as any
const paid={orderId:'ship',orderNo:'DD001',status:'PAID',shipEligible:true,revision:3,paymentMethod:'OFFLINE',paymentReviewStatus:'PAID',payableFen:1000,createdAt:'2026-10-11',deliveryMode:'EXPRESS',items:[]}
const unpaid={...paid,orderId:'unpaid',orderNo:'DD002',status:'PENDING_PAYMENT',shipEligible:false}
const local={...paid,orderId:'local',orderNo:'DD003',deliveryMode:'DELIVERY'}
const wrappers:ReturnType<typeof mount>[]=[]
const stubs={RouterLink:{template:'<a><slot/></a>'},ElDialog:{template:'<section v-if="modelValue"><slot/><slot name="footer"/></section>',props:['modelValue']},OrderActionDialog:true,ProductCreateForm:true,ProductSpecEditor:true,ProductMediaEditor:true,ProductDescriptionEditor:true,ProductSalePanel:true}
beforeEach(()=>{vi.mocked(api).mockImplementation(async(path:string)=>{
 if(path.startsWith('/orders?'))return {items:[paid,unpaid,local],page:1,pageSize:20,total:120} as any
 if(path==='/fulfillment/carriers')return {items:[{code:'SF',name:'顺丰',enabled:true}]} as any
 if(path.startsWith('/product-rows?'))return {rows:[{productId:'p1',productNo:'SP001',name:'商品',skuCount:1,onSaleSkuCount:1,status:'ON_SALE',matchedSkuIds:[],minListPriceFen:100,maxListPriceFen:100}],page:1,pageSize:20,total:120} as any
 if(path==='/categories'||path==='/member-grades')return [] as any
 if(path==='/shipments/batch')return {results:[{orderId:'ship',success:true}]} as any
 return paid as any
})})
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.clearAllMocks();sessionStorage.clear()})
async function setup(component:any){const w=mount(component,{props:{account},global:{stubs}});wrappers.push(w);await flushPromises();return w}
describe('catalog and order list standard',()=>{
 it('allows mixed current-page selection and sends only eligible shipment rows',async()=>{
  const w=await setup(OrdersView)
  for(const n of ['DD001','DD002','DD003'])await w.get(`[aria-label="选择订单 ${n}"]`).setValue(true)
  await w.findAll('button').find(b=>b.text().startsWith('批量发货'))!.trigger('click');await flushPromises()
  expect(w.text()).toContain('可发货 1 单');expect(w.text()).toContain('待收款，不能发货');expect(w.text()).toContain('前置仓配送')
  await w.get('.order-bulk-row select').setValue('SF');await w.get('.order-bulk-row input').setValue('SF001')
  await w.findAll('button').find(b=>b.text()==='确认发货 1 单')!.trigger('click');await flushPromises()
  const call=vi.mocked(api).mock.calls.find(([p])=>p==='/shipments/batch')!
  expect(JSON.parse(call[1]!.body as string).items).toHaveLength(1);expect(w.text()).toContain('未执行：待收款，不能发货')
 })
 it('clears order selection on filter edits and sends chosen page size',async()=>{
  const w=await setup(OrdersView);await w.get('[aria-label="选择订单 DD001"]').setValue(true)
  await w.get('input[type="search"]').setValue('DD')
  expect((w.get('[aria-label="选择订单 DD001"]').element as HTMLInputElement).checked).toBe(false)
  const pager=w.findComponent({name:'AdminListPager'});expect(pager.exists()).toBe(true)
  pager.vm.$emit('change',2,50);await flushPromises()
  expect(vi.mocked(api).mock.calls.some(([p])=>p.includes('page=2')&&p.includes('pageSize=50'))).toBe(true)
 })
 it('opens details inside a dialog without route navigation',async()=>{
  const w=await setup(OrdersView);const details=w.findAll('button').find(b=>b.text()==='查看详情')
  expect(details).toBeDefined();await details!.trigger('click');await flushPromises()
  expect(vi.mocked(api)).toHaveBeenCalledWith('/orders/ship');expect(w.findComponent({name:'OrderDetailDialog'}).exists()).toBe(true)
 })
 it('passes chosen product page size and clears selection',async()=>{
  const w=await setup(ProductManagement);await w.get('[aria-label="选择商品 SP001"]').setValue(true)
  const pager=w.findComponent({name:'AdminListPager'});expect(pager.exists()).toBe(true)
  pager.vm.$emit('change',1,100);await flushPromises()
  expect(vi.mocked(api).mock.calls.some(([p])=>p.startsWith('/product-rows?')&&p.includes('pageSize=100'))).toBe(true)
  expect((w.get('[aria-label="选择商品 SP001"]').element as HTMLInputElement).checked).toBe(false)
 })
 it('keeps unknown shipment identities and blocks fresh batch retries',async()=>{
  const original=vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation((path,init)=>path==='/shipments/batch'?Promise.reject(new Error('网络中断')):original(path,init))
  const w=await setup(OrdersView)
  await w.get('[aria-label="选择订单 DD001"]').setValue(true)
  await w.findAll('button').find(b=>b.text()==='批量发货')!.trigger('click');await flushPromises()
  await w.get('.order-bulk-row select').setValue('SF');await w.get('.order-bulk-row input').setValue('SF001')
  await w.findAll('button').find(b=>b.text()==='确认发货 1 单')!.trigger('click');await flushPromises()
  const pending=JSON.parse(sessionStorage.getItem('mall-order-action:admin:ship:ship')!)
  expect(pending).toMatchObject({actor:'admin',orderId:'ship',action:'ship',path:'/orders/ship/shipment',phase:'write',body:{expectedRevision:3,trackingNo:'SF001'}})
  expect(w.text()).toContain('核查发货结果')
  expect(w.findAll('button').some(b=>b.text()==='确认发货 1 单')).toBe(false)
  await w.findAll('button').find(b=>b.text()==='关闭')!.trigger('click');await flushPromises()
  await w.get('[aria-label="选择订单 DD001"]').setValue(true)
  await w.findAll('button').find(b=>b.text()==='批量发货')!.trigger('click');await flushPromises()
  expect(w.text()).toContain('可发货 0 单');expect(w.text()).toContain('上次发货结果待核查')
 })
 it('splits a 100-order page into service limits and clears acknowledged identities',async()=>{
  const original=vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation((path,init)=>{
   if(path.startsWith('/orders?'))return Promise.resolve({items:Array.from({length:100},(_,n)=>({...paid,orderId:`ship-${n}`,orderNo:`DD${n}`})),page:1,pageSize:100,total:100}) as any
   if(path==='/shipments/batch')return Promise.resolve({results:JSON.parse(init!.body as string).items.map((item:any)=>({orderId:item.orderId,success:true}))}) as any
   return original(path,init)
  })
  const w=await setup(OrdersView);await w.get('[aria-label="选择当前页全部订单"]').setValue(true)
  await w.findAll('button').find(b=>b.text()==='批量发货')!.trigger('click');await flushPromises()
  for(const [n,row] of w.findAll('.order-bulk-row').entries()){await row.get('select').setValue('SF');await row.get('input').setValue(`SF${n}`)}
  await w.findAll('button').find(b=>b.text()==='确认发货 100 单')!.trigger('click');await flushPromises()
  const calls=vi.mocked(api).mock.calls.filter(([p])=>p==='/shipments/batch')
  expect(calls.map(([,init])=>JSON.parse(init!.body as string).items.length)).toEqual([50,50])
  expect(sessionStorage.length).toBe(0)
 })
 it('shows each missing server result as unknown and retains its original request',async()=>{
  const original=vi.mocked(api).getMockImplementation()!
  vi.mocked(api).mockImplementation((path,init)=>path==='/shipments/batch'?Promise.resolve({results:[]}):original(path,init))
  const w=await setup(OrdersView);await w.get('[aria-label="选择订单 DD001"]').setValue(true)
  await w.findAll('button').find(b=>b.text()==='批量发货')!.trigger('click');await flushPromises()
  await w.get('.order-bulk-row select').setValue('SF');await w.get('.order-bulk-row input').setValue('SF001')
  await w.findAll('button').find(b=>b.text()==='确认发货 1 单')!.trigger('click');await flushPromises()
  expect(w.text()).toContain('结果未知，请逐单查看详情核对');expect(sessionStorage.getItem('mall-order-action:admin:ship:ship')).not.toBeNull()
 })
 it('hides optional product columns and preserves identity and operation columns',async()=>{
  const w=await setup(ProductManagement)
  w.findComponent({name:'AdminListColumns'}).vm.$emit('update:modelValue',['category','price','sku','status']);await flushPromises()
  expect(w.findAll('.catalog-product-table th').map(h=>h.text())).toEqual(['','商品','操作'])
 })

 it('keeps detail reads isolated when another order is opened and retries read errors',async()=>{
  let finish!:(value:any)=>void
  vi.mocked(api).mockImplementation(path=>path==='/orders/old'?new Promise(resolve=>{finish=resolve}):Promise.resolve({...paid,orderId:'new',orderNo:'NEW'}))
  const w=mount(OrderDetailDialog,{props:{account,orderId:'old'},global:{stubs}});wrappers.push(w)
  await w.setProps({orderId:'new'});await flushPromises()
  finish({...paid,orderNo:'OLD'});await flushPromises()
  expect(w.text()).toContain('NEW');expect(w.text()).not.toContain('OLD')
  vi.mocked(api).mockRejectedValueOnce(new Error('暂无读取权限'))
  await w.setProps({orderId:'blocked'});await flushPromises();expect(w.text()).toContain('暂无读取权限')
  expect(w.text()).not.toContain('NEW')
  await w.findAll('button').find(b=>b.text()==='重新读取')!.trigger('click');await flushPromises()
  expect(w.text()).toContain('NEW');expect(vi.mocked(api).mock.calls.every(([,init])=>!init?.method)).toBe(true)
 })

})
