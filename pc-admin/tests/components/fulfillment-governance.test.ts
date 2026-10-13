import {afterEach,expect,it,vi} from 'vitest'
import {flushPromises,mount} from '@vue/test-utils'
import {ref} from 'vue'
import OrdersView from '../../src/views/OrdersView.vue'
import OrderDetailDialog from '../../src/views/orders/OrderDetailDialog.vue'
import OrderActionDialog from '../../src/views/orders/OrderActionDialog.vue'
import ProductCreateForm from '../../src/views/catalog/ProductCreateForm.vue'
import ProductManagement from '../../src/views/catalog/ProductManagement.vue'
import RedemptionView from '../../src/views/RedemptionView.vue'
import RedemptionScopeFields from '../../src/views/catalog/RedemptionScopeFields.vue'
import {fulfillmentOwnerLabel,redemptionScopeLabel} from '../../src/shared/fulfillment-policy'
import {api,type Account} from '../../src/api'
vi.mock('../../src/api',async original=>({...await original<any>(),api:vi.fn()}))
const account={accountId:'governance',permissionCodes:['order.read','catalog.read','catalog.write','fulfillment.ship','fulfillment.redeem']} as Account
const line={orderLineId:'l1',name:'次卡',skuCode:'CARD',quantity:5,saleUnit:'次',fulfillmentKind:'REDEEM',payableFen:1000,fulfillment:{status:'PARTIAL',redeemScope:'BOTH',redeemStores:[{storeId:'s1',name:'深圳前置仓'}],remainingQuantity:3,availableQuantity:2,frozenQuantity:1,redeemedQuantity:2,validUntil:'2028-12-31',canRedeemPlatform:true}}
const order={orderId:'o1',orderNo:'ORDER1',revision:3,status:'PAID',paymentMethod:'OFFLINE',paymentReviewStatus:'CONFIRMED',payableFen:1000,createdAt:'2026-10-12',fulfillmentStatus:'WAITING_REDEMPTION',deliveryMode:'REDEEM',fulfillmentOwner:'REDEEM',inventoryWarehouse:null,items:[line]}
const wrappers:ReturnType<typeof mount>[]=[]
const stubs={RouterLink:{template:'<a><slot/></a>'},ElDialog:{props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'},ProductMediaEditor:true,ProductDescriptionEditor:true}
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());sessionStorage.clear();vi.resetAllMocks()})
function render(component:any,props:any){const w=mount(component,{props,global:{stubs,provide:{'admin-account':ref(account)}}});wrappers.push(w);return w}
it('filters platform orders by execution owner and delivery mode',async()=>{
 vi.mocked(api).mockResolvedValue({items:[order],page:1,pageSize:20,total:1})
 const w=render(OrdersView,{account});await flushPromises()
 await w.get('[aria-label="交付方式"]').setValue('REDEEM');await w.get('[aria-label="履约方"]').setValue('REDEEM');await w.get('form').trigger('submit');await flushPromises()
 expect(vi.mocked(api).mock.calls.some(([p])=>p.includes('deliveryMode=REDEEM')&&p.includes('fulfillmentOwner=REDEEM'))).toBe(true)
})
it('shows frozen scope and available rights separately in order detail',async()=>{
 vi.mocked(api).mockResolvedValue(order)
 const w=render(OrderDetailDialog,{account,orderId:'o1'});await flushPromises()
 expect(w.text()).toContain('平台＋指定前置仓');expect(w.text()).toContain('深圳前置仓');expect(w.text()).toContain('售后冻结 1');expect(w.text()).toContain('当前可核销 2')
})
it('cannot resend a restored platform local-fulfillment request',async()=>{
 const pending={actor:account.accountId,orderId:'o1',action:'prepare',key:crypto.randomUUID(),phase:'write',path:'/orders/o1/local-fulfillment',body:{expectedRevision:3,action:'PREPARE',note:''}}
 sessionStorage.setItem('mall-order-action:governance:o1:prepare',JSON.stringify(pending))
 vi.mocked(api).mockResolvedValue({...order,deliveryMode:'DELIVERY',fulfillmentStatus:'WAITING_PREPARATION'})
 const w=render(OrderActionDialog,{account,orderId:'o1',action:'prepare'});await flushPromises()
 await w.findAll('button').find(b=>b.text()==='查询订单结果')!.trigger('click');await flushPromises()
 await w.get('[data-test="action-submit"]').trigger('click');await flushPromises()
 expect(vi.mocked(api).mock.calls.some(([,init])=>init?.method==='POST')).toBe(false)
 expect(w.text()).toContain('前置仓中心')
})
it('saves new virtual product scope and rejects an empty warehouse selection',async()=>{
 vi.mocked(api).mockImplementation(async p=>p==='/products/redemption-stores'?{items:[{storeId:'s1',name:'深圳前置仓'}]}:{})
 const w=render(ProductCreateForm,{categories:[{id:'r',parentId:null,name:'服务',status:'ACTIVE'},{id:'c',parentId:'r',name:'次卡',status:'ACTIVE'}]});await flushPromises()
 await w.get('input[placeholder="例如 PROD-001"]').setValue('CARD');await w.get('input[placeholder="请输入商品名称"]').setValue('次卡');await w.get('select').setValue('c')
 await w.get('[aria-label="商品类型"]').setValue('REDEEM');await flushPromises()
 await w.get('[aria-label="核销范围"]').setValue('BOTH');await flushPromises()
 await w.findAll('button').find(b=>b.text()==='生成 SKU 组合')!.trigger('click');await flushPromises()
 await w.get('[aria-label="SKU 编码 默认规格"]').setValue('CARD1');await w.get('[aria-label="日常价 CARD1"]').setValue('10')
 await w.get('form').trigger('submit');await flushPromises();expect(w.text()).toContain('至少选择一个前置仓')
 await w.get('[aria-label="授权核销 深圳前置仓"]').setValue(true);await w.get('form').trigger('submit');await flushPromises()
 const call=vi.mocked(api).mock.calls.find(([p,init])=>p==='/products'&&init?.method==='POST')!
 expect(JSON.parse(String(call[1]!.body))).toMatchObject({redeemScope:'BOTH',redeemStoreIds:['s1']})
})
it('changes virtual product policy with its revision without changing historical order rights',async()=>{
 const product={productId:'p1',productNo:'CARD',name:'次卡',categoryId:'c',fulfillmentKind:'REDEEM',redeemValidUntil:'2028-12-31',redeemScope:'BOTH',redeemStoreIds:['s1'],redeemStores:[{storeId:'s1',name:'深圳前置仓'}],productRevision:4,status:'DRAFT',descriptionHtml:'',mainImage:null,galleryImages:[],video:null,specAxes:[],skus:[]}
 vi.mocked(api).mockImplementation(async(p,init)=>p==='/categories'?[{id:'r',parentId:null,name:'服务',status:'ACTIVE'},{id:'c',parentId:'r',name:'次卡',status:'ACTIVE'}]:p==='/member-grades'?[]:p==='/products/redemption-stores'?{items:product.redeemStores}:p.startsWith('/product-rows')?{rows:[product],page:1,pageSize:20,total:1}:p==='/products/p1'?init?.method?{...product,...JSON.parse(String(init.body)),productRevision:5}:product:{})
 const w=render(ProductManagement,{account});await flushPromises();await w.findAll('button').find(b=>b.text()==='编辑商品')!.trigger('click');await flushPromises()
 expect(w.get('[aria-label="核销范围"]').element).toHaveProperty('value','BOTH')
 await w.get('[aria-label="核销范围"]').setValue('PLATFORM');await w.get('form.catalog-editor').trigger('submit');await flushPromises()
 const call=vi.mocked(api).mock.calls.find(([p,init])=>p==='/products/p1'&&init?.method==='PATCH')!
 expect(JSON.parse(String(call[1]!.body))).toMatchObject({expectedRevision:4,redeemScope:'PLATFORM',redeemStoreIds:[]})
 expect(order.items[0]!.fulfillment.redeemScope).toBe('BOTH')
})
it('warehouse policy options recover from failure and retain saved selection',async()=>{
 vi.mocked(api).mockRejectedValueOnce(Error('网络中断')).mockResolvedValue({items:[{storeId:'s1',name:'深圳前置仓'}]})
 const w=render(RedemptionScopeFields,{scope:'STORE',storeIds:['old'],savedStores:[{storeId:'old',name:'历史前置仓'}]});await flushPromises()
 expect(w.text()).toContain('历史前置仓');expect(w.text()).toContain('网络中断')
 await w.findAll('button').find(b=>b.text()==='重新读取前置仓')!.trigger('click');await flushPromises()
 await w.get('[aria-label="授权核销 深圳前置仓"]').setValue(true)
 expect(w.emitted('update:storeIds')?.[0]).toEqual([['old','s1']])
})
it('standalone platform redemption uses available rights and opens persistent order operation',async()=>{
 const voucher={...line.fulfillment,voucherId:'v1',orderId:'o1',orderNo:'ORDER1',orderLineId:'l1',name:'次卡',quantity:5,redeemedQuantity:2,remainingQuantity:3,availableQuantity:2,status:'READY',revision:7}
 vi.mocked(api).mockImplementation(async p=>p==='/orders/o1'?order:voucher)
 const w=render(RedemptionView,{account});await w.get('#redemption-code').setValue('V1');await w.get('form.redeem-lookup-form').trigger('submit');await flushPromises()
 await w.get('#redeem-quantity').setValue('3');expect(w.text()).toContain('超过可核销数量')
 await w.get('#redeem-quantity').setValue('2');await w.get('form.redeem-action').trigger('submit');await flushPromises()
 const dialog=w.getComponent(OrderActionDialog);expect(dialog.props('redemptionCode')).toBe('V1');expect(dialog.props('redemptionQuantity')).toBe('2')
 expect(w.text()).toContain('售后冻结 1');expect(vi.mocked(api).mock.calls.some(([p])=>p.endsWith('/use'))).toBe(false)
})
it('renders explicit fulfillment owners and frozen redemption range',()=>{
 expect(fulfillmentOwnerLabel({fulfillmentOwner:'PLATFORM',storeName:'导购仓'})).toBe('平台后台')
 expect(fulfillmentOwnerLabel({fulfillmentOwner:'STORE',fulfillmentStoreName:'深圳前置仓'})).toBe('深圳前置仓中心')
 expect(fulfillmentOwnerLabel({fulfillmentOwner:'REDEEM'})).toBe('按订单项核销范围')
 expect(redemptionScopeLabel({redeemScope:'PLATFORM'})).toBe('仅平台')
 expect(redemptionScopeLabel({redeemScope:'STORE',redeemStores:[{storeId:'s1',name:'深圳前置仓'}]})).toBe('指定前置仓：深圳前置仓')
})
