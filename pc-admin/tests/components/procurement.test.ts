import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { reactive } from 'vue'
import { api, ApiError, type Account } from '../../src/api'
import { confirmAction } from '../../src/shared/confirm'
import List from '../../src/views/stores/procurement/ProcurementListView.vue'
import Detail from '../../src/views/stores/procurement/ProcurementDetailView.vue'
import Settings from '../../src/views/stores/procurement/ProcurementSettingsView.vue'
vi.mock('../../src/api',async original=>({...await original<object>(),api:vi.fn()}))
vi.mock('../../src/shared/confirm',()=>({confirmAction:vi.fn().mockResolvedValue(true)}))
const id='11a27082-c544-4ed8-8b70-3c1070bf560b'
const permissions=['stores.procurement.read','stores.procurement.manage','stores.procurement.finance']
const account=()=>reactive<Account>({accountId:'actor',loginName:'a',displayName:'A',kind:'OWNER',enabled:true,revision:1,groupIds:[],permissionCodes:[...permissions]})
const order=(status='REVIEW',revision=1)=>({id,orderNo:'P20261011001',storeId:'s',storeName:'深圳1号仓',status,revision,goodsAmountFen:6000,freightFen:500,payableFen:6500,createdAt:'2026-10-11T01:00:00Z',paymentInstructions:'银行转账',paymentProof:null,shipment:null,lines:[{id:'line',skuId:'sku',productName:'测试礼盒',specLabel:'2瓶装',saleUnit:'盒',quantity:1,purchaseCostFen:6000,conversionRatio:2,baseUnit:'瓶',unitVersionId:'unit'}],records:[]})
const mounted:ReturnType<typeof mount>[]=[]
afterEach(()=>{mounted.splice(0).forEach(w=>w.unmount());vi.mocked(api).mockReset();vi.mocked(confirmAction).mockReset().mockResolvedValue(true);sessionStorage.clear()})
async function setup(component:typeof Detail|typeof List|typeof Settings,path=`/stores/procurement/${id}`,actor=account()){
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/stores/procurement/:orderId',component:Detail,props:()=>({account:actor})},{path:'/stores/procurement',component:List,props:()=>({account:actor})},{path:'/stores/procurement/settings',component:Settings,props:()=>({account:actor})}]})
 await router.push(path)
 const w=mount(RouterView,{global:{plugins:[router],stubs:{ElDialog:{props:['modelValue','title'],template:'<div v-if="modelValue" role="dialog"><h3>{{title}}</h3><slot/><slot name="footer"/></div>'}}}})
 mounted.push(w);await flushPromises();return{w,actor,router}
}
async function click(w:ReturnType<typeof mount>,label:string){const b=w.findAll('button').find(x=>x.text()===label);expect(b,`button ${label}`).toBeTruthy();await b!.trigger('click');await flushPromises()}
async function input(w:ReturnType<typeof mount>,label:string,value:string){const l=w.findAll('label').find(x=>x.text().startsWith(label));expect(l).toBeTruthy();await l!.find('input,textarea').setValue(value)}
describe('procurement list',()=>{
 it('loads filters and pages and exposes current state',async()=>{
  vi.mocked(api).mockResolvedValue({items:[order()],total:25})
  const{w}=await setup(List,'/stores/procurement')
  expect(w.text()).toContain('深圳1号仓');await w.find('input').setValue('P2026');await w.find('select').setValue('PAID');await w.find('form').trigger('submit');await flushPromises()
  expect(vi.mocked(api).mock.calls.at(-1)![0]).toContain('q=P2026&status=PAID')
  await click(w,'下一页');expect(vi.mocked(api).mock.calls.at(-1)![0]).toContain('page=2')
  await click(w,'重置');expect(vi.mocked(api).mock.calls.at(-1)![0]).toContain('page=1')
 })
 it('retains retry on read failure and clears unauthorized identity',async()=>{
  vi.mocked(api).mockRejectedValueOnce(new Error('读取失败')).mockResolvedValue({items:[],total:0})
  const{w,actor}=await setup(List,'/stores/procurement');expect(w.text()).toContain('读取失败');await click(w,'重试');expect(w.text()).toContain('暂无符合')
  actor.permissionCodes=[];await flushPromises();expect(w.text()).not.toContain('深圳1号仓')
 })
})
describe('procurement order actions',()=>{
 it('validates review money then submits frozen revision and matching requestKey',async()=>{
  vi.mocked(api).mockResolvedValueOnce(order()).mockResolvedValue(order('QUOTE',2))
  const{w}=await setup(Detail);await click(w,'审单并确认运费');await input(w,'运费（元）','1.001');await click(w,'确认提交');expect(w.text()).toContain('最多两位小数')
  await input(w,'运费（元）','8.50');await click(w,'确认提交')
  const call=vi.mocked(api).mock.calls.find(x=>x[0].endsWith('/review'))!;const body=JSON.parse(call[1]!.body as string)
  expect(body).toMatchObject({expectedRevision:1,freightFen:850});expect(call[1]!.headers).toMatchObject({'Idempotency-Key':body.requestKey});expect(w.find('[role=dialog]').exists()).toBe(false)
 })
 it('requires finance permission and password and verifies real payment',async()=>{
  vi.mocked(api).mockImplementation(async(path)=>path==='/auth/confirm'?{confirmationToken:'ok',expiresAt:''}:path.endsWith('/confirm-payment')?order('PAID',2):order('PROOF'))
  const{w,actor}=await setup(Detail);await click(w,'确认实际到账');await input(w,'到账凭证编号','BANK-01');await click(w,'确认提交');expect(vi.mocked(api).mock.calls.some(x=>x[0]==='/auth/confirm')).toBe(false)
  await input(w,'管理员密码','secret');await click(w,'确认提交');expect(vi.mocked(api).mock.calls.find(x=>x[0]==='/auth/confirm')![1]!.body).toContain('stores.procurement.confirm-payment')
  actor.permissionCodes=['stores.procurement.read'];await flushPromises();expect(w.text()).not.toContain('确认实际到账')
 })
 it('preserves unknown requests and recovers then retries the same frozen body',async()=>{
  vi.mocked(api).mockResolvedValueOnce(order('PAID')).mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce({status:'NOT_FOUND'}).mockResolvedValueOnce(order('SHIPPED',2))
  const{w}=await setup(Detail);await click(w,'填写物流并发货');await input(w,'物流公司','顺丰');await input(w,'物流单号','SF123');await click(w,'确认提交')
  expect(w.text()).toContain('原请求已保留');const first=vi.mocked(api).mock.calls.find(x=>x[0].endsWith('/ship'))![1]!.body
  await click(w,'读取操作结果');await click(w,'使用原请求重试');const calls=vi.mocked(api).mock.calls.filter(x=>x[0].endsWith('/ship'));expect(calls[1]![1]!.body).toBe(first);expect(w.text()).toContain('待收货')
 })
 it('refuses stale permission while confirmation is open and prompts dirty close',async()=>{
  vi.mocked(api).mockResolvedValue(order())
  const{w,actor}=await setup(Detail);await click(w,'审单并确认运费');await input(w,'运费（元）','9');vi.mocked(confirmAction).mockResolvedValueOnce(false);await click(w,'取消');expect(w.find('[role=dialog]').exists()).toBe(true)
  let resolve!:(x:boolean)=>void;vi.mocked(confirmAction).mockImplementationOnce(()=>new Promise(r=>{resolve=r}));await w.findAll('button').find(b=>b.text()==='确认提交')!.trigger('click');actor.permissionCodes=['stores.procurement.read'];await flushPromises();resolve(true);await flushPromises();expect(vi.mocked(api).mock.calls.some(x=>x[1]?.method==='POST')).toBe(false)
 })
 it('keeps rejected draft and requires reading fresh data',async()=>{
  vi.mocked(api).mockResolvedValueOnce(order('EXCEPTION')).mockRejectedValueOnce(new ApiError('状态已变更',409,'PROCUREMENT_STATE_CONFLICT')).mockResolvedValue(order('EXCEPTION',2))
  const{w}=await setup(Detail);await click(w,'补齐凭证并重新发货');await input(w,'处理／退款凭证编号','PHOTO-1');await input(w,'处理备注','缺件已补齐');await click(w,'确认提交');expect(w.text()).toContain('状态已变更');expect(w.find('[role=dialog] input').element).toHaveProperty('value','PHOTO-1');await click(w,'重新读取订单');expect(w.find('[role=dialog]').exists()).toBe(true)
 })
 it.each(['reject','reject-proof','close-cancel','verify-units'])('opens and validates %s with appropriate action fields',async name=>{
  const status={reject:'REVIEW','reject-proof':'PROOF','close-cancel':'CANCEL_REVIEW','verify-units':'PAID'}[name]!
  const labels={reject:'拒绝供货','reject-proof':'退回付款凭证','close-cancel':'确认退款并关闭','verify-units':'复核冻结单位'}[name]!
  vi.mocked(api).mockResolvedValue({...order(status),unitReviewRequired:name==='verify-units'})
  const{w}=await setup(Detail);await click(w,labels);await click(w,'确认提交');expect(w.find('[role=dialog]').text()).toContain('请填写')
 })
 it('renders private proof and frozen unit details; failed reads can recover',async()=>{
  vi.mocked(api).mockRejectedValueOnce(new Error('失败')).mockResolvedValue({...order('SHIPPED'),paymentProof:{assetId:id,previewUrl:`/api/v1/admin/stores/procurement/proofs/${id}/file`},shipment:{carrier:'顺丰',tracking:'SF'},records:[{id:'r',kind:'ship',actorName:'A',note:'已发货',reference:'SF',createdAt:'2026-10-11T01:00:00Z'}]})
  const{w}=await setup(Detail);expect(w.text()).toContain('失败');await click(w,'重新读取订单');expect(w.find('img').attributes('src')).toContain('/proofs/');expect(w.text()).toContain('1 盒 = 2 瓶');expect(w.text()).toContain('已发货');expect(w.text()).toContain('平台发货');expect(w.text()).not.toContain('ship ·')
 })
})
describe('procurement settings',()=>{
 it('requires validation then saves through password confirmation',async()=>{
  vi.mocked(api).mockImplementation(async path=>path==='/auth/confirm'?{confirmationToken:'token'}:{paymentInstructions:'银行转账',revision:2})
  const{w}=await setup(Settings,'/stores/procurement/settings');await w.find('textarea').setValue('');await w.find('form').trigger('submit');await flushPromises();expect(w.text()).toContain('请填写采购收款说明')
  await w.find('textarea').setValue('收款银行卡');await w.find('form').trigger('submit');await flushPromises();expect(w.text()).toContain('请填写管理员密码')
  await input(w,'管理员密码','secret');await w.find('form').trigger('submit');await flushPromises();expect(w.text()).toContain('已保存');expect(vi.mocked(api).mock.calls.find(x=>x[1]?.method==='PUT')![1]!.body).toContain('expectedRevision')
 })
 it('preserves errors and drafts until explicit reload; read-only blocks writing',async()=>{
  vi.mocked(api).mockResolvedValueOnce({paymentInstructions:'原说明',revision:1}).mockRejectedValueOnce(new Error('未知')).mockResolvedValue({paymentInstructions:'新说明',revision:2})
  const{w,actor}=await setup(Settings,'/stores/procurement/settings');await w.find('textarea').setValue('草稿');await input(w,'管理员密码','secret');await w.find('form').trigger('submit');await flushPromises();expect(w.text()).toContain('未知');expect(w.find('textarea').element).toHaveProperty('value','草稿')
  vi.mocked(confirmAction).mockResolvedValueOnce(false);await click(w,'重新读取');expect(w.find('textarea').element).toHaveProperty('value','草稿');await click(w,'重新读取');expect(w.find('textarea').element).toHaveProperty('value','新说明')
  actor.permissionCodes=['stores.procurement.read'];await flushPromises();expect(w.find('textarea').attributes('disabled')).toBeDefined();expect(w.text()).not.toContain('保存收款说明')
 })
})

it('settings handles read failure, blocked identity, unload and late confirmation safely',async()=>{
 vi.mocked(api).mockRejectedValueOnce(new Error('无法读取')).mockResolvedValue({paymentInstructions:'原说明',revision:1})
 const{w,actor,router}=await setup(Settings,'/stores/procurement/settings');expect(w.text()).toContain('无法读取');await click(w,'重新读取')
 await w.find('textarea').setValue('未保存');const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);expect(event.defaultPrevented).toBe(true)
 vi.mocked(confirmAction).mockResolvedValueOnce(false);await router.push('/stores/procurement');expect(router.currentRoute.value.path).toContain('/settings')
 let resolve!:(x:unknown)=>void;vi.mocked(api).mockImplementationOnce(()=>new Promise(r=>{resolve=r}));await input(w,'管理员密码','secret');await w.find('form').trigger('submit');await flushPromises()
 actor.permissionCodes=[];await flushPromises();resolve({confirmationToken:'late'});await flushPromises();expect(vi.mocked(api).mock.calls.some(x=>x[1]?.method==='PUT')).toBe(false)
})

it('procurement domain validates every independent action and private cache read',async()=>{
 const {actionBody,pendingProcurement,clearProcurementSnapshots,resultMatches,recoveryDecision,proofUrl,availableActions}=await import('../../src/views/stores/procurement/domain.mjs')
 expect(actionBody('review',{freight:'0'},-1).error).toBeTruthy()
 expect(actionBody('review',{freight:'0'},0).body.freightFen).toBe(0)
 expect(actionBody('reject',{reason:'缺货'},0).body.reason).toBe('缺货')
 expect(actionBody('reject-proof',{reason:' '},0).error).toBeTruthy()
 expect(actionBody('ship',{carrier:'顺丰',tracking:'SF'},0).body.carrier).toBe('顺丰')
 expect(actionBody('close-cancel',{reference:'REF',refundConfirmed:false},0).error).toBeTruthy()
 expect(actionBody('close-cancel',{reference:'REF',note:'已退款',refundConfirmed:true},0).body.refundConfirmed).toBe(true)
 expect(actionBody('invalid',{reference:'R'},0).error).toBeTruthy()
 expect(actionBody('verify-units',{reference:'R',note:'已复核'},0).body.note).toBe('已复核')
 expect(availableActions({status:'RECEIVED',unitReviewRequired:true})).toEqual([])
 expect(availableActions({status:'PAYMENT',unitReviewRequired:false})).toEqual([])
 expect(availableActions({status:'CANCEL_REVIEW',unitReviewRequired:true})).toEqual(['close-cancel'])
 expect(availableActions({status:'PROOF',unitReviewRequired:true})).toEqual(['verify-units','confirm-payment','reject-proof'])
 expect(proofUrl(null)).toBe('')
 expect(pendingProcurement(sessionStorage,'a',id)).toBeNull()
 const p={key:id,path:`/stores/procurement/orders/${id}/ship`,method:'POST' as const,body:{expectedRevision:0,requestKey:id},action:''}
 pendingProcurement(sessionStorage,'a',id,p,0);expect(pendingProcurement(sessionStorage,'a',id,undefined,1)).toEqual(p)
 expect(recoveryDecision({status:'NOT_FOUND'},p)).toBe('RETRY')
 expect(recoveryDecision({status:'COMPLETED',result:order('SHIPPED',1)},p)).toBe('COMPLETED')
 expect(recoveryDecision({},p)).toBe('UNKNOWN');expect(resultMatches(p,null)).toBe(false)
 expect(pendingProcurement(sessionStorage,'a',id,undefined,3600001)).toBeNull()
 sessionStorage.setItem(`mall:procurement:a:${id}`,'{bad');expect(pendingProcurement(sessionStorage,'a',id)).toBeNull()
 pendingProcurement(sessionStorage,'a',id,{...p,path:'/evil'});expect(pendingProcurement(sessionStorage,'a',id)).toBeNull()
 pendingProcurement(sessionStorage,'a',id,p);sessionStorage.setItem('other','x');clearProcurementSnapshots(sessionStorage);expect(sessionStorage.getItem('other')).toBe('x');expect(clearProcurementSnapshots({get length(){throw new Error('blocked')}} as Storage)).toBe(false);expect(pendingProcurement(sessionStorage,'a',id)).toBeNull()
 pendingProcurement(sessionStorage,'a',id,p);pendingProcurement(sessionStorage,'a',id,null);expect(pendingProcurement(sessionStorage,'a',id)).toBeNull()
})

it('retains uncertain request across order navigation but purges it when identity changes',async()=>{
 vi.mocked(api).mockResolvedValueOnce(order('PAID')).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(order('PAID'))
 const{w,router,actor}=await setup(Detail);await click(w,'填写物流并发货');await input(w,'物流公司','顺丰');await input(w,'物流单号','SF1');await click(w,'确认提交')
 const key=`mall:procurement:actor:${id}`;expect(sessionStorage.getItem(key)).toBeTruthy()
 await router.push('/stores/procurement/22222222-2222-4222-8222-222222222222');await flushPromises();expect(sessionStorage.getItem(key)).toBeTruthy()
 actor.accountId='another';await flushPromises();expect(sessionStorage.getItem(key)).toBeNull()
})
