import {afterEach,describe,expect,it,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import {createRouter,createMemoryHistory} from 'vue-router'
import StoreDetailView from '../../src/views/stores/StoreDetailView.vue'
import StoreAccountDetailView from '../../src/views/stores/StoreAccountDetailView.vue'
import {api,type Account} from '../../src/api'
vi.mock('../../src/api',()=>({api:vi.fn()}))
const account={permissionCodes:['stores.read','stores.manage']} as Account
const store={warehouseId:'warehouse-private',id:'s1',name:'湘江路店',contactName:'张店长',contactPhone:'13800138000',address:'湘江路12号',city:'长沙',latitude:28,longitude:112,openingHours:'09:00–21:00',enabled:true,acceptingOrders:true,supportedModes:['PICKUP'],deliveryRadiusMeters:0,deliveryFeeFen:0,revision:1}
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.clearAllMocks()})
async function setup(path:string,component=StoreDetailView,permissions=account){const router=createRouter({history:createMemoryHistory(),routes:[{path:'/stores/accounts/:storeId',component:StoreAccountDetailView},{path:'/stores/:storeId',component:StoreDetailView},{path:'/stores',component:{template:'<p>list</p>'}}]});await router.push(path);const w=mount(component,{props:{account:permissions},global:{plugins:[router]}});wrappers.push(w);await flushPromises();return w}
describe('store editor',()=>{
 it('saves current revision, assigns staff and handles absent map key',async()=>{
  vi.mocked(api).mockImplementation(async(path,init)=>{if(path.includes('map-search'))throw new Error('高德地图密钥未配置');if(path.includes('memberSearch'))return {members:[{id:'member-1',memberNo:'m20260930203055li3',name:'张小仓'}]};if(path.endsWith('/staff'))return init?.method==='POST'?{}:{items:[]};return store})
  const w=await setup('/stores/s1')
  await w.find('input[maxlength="100"]').setValue('新店名');await w.find('form').trigger('submit');await flushPromises();const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='PATCH');expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('warehouseId');expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({name:'新店名',revision:1});expect(w.text()).toContain('前置仓资料已保存')
  await w.find('input[placeholder="输入前置仓名称或地址"]').setValue('长沙');await w.findAll('button').find(b=>b.text()==='搜索高德位置')!.trigger('click');await flushPromises();expect(w.text()).toContain('高德地图密钥未配置')
  await w.find('input[placeholder="输入会员编号或昵称"]').setValue('m20260930203055li3');await w.findAll('button').find(b=>b.text()==='查找会员')!.trigger('click');await flushPromises();await w.findAll('button').find(b=>b.text().includes('选择 张小仓'))!.trigger('click');await w.findAll('form')[1]!.trigger('submit');await flushPromises();const staffCall=vi.mocked(api).mock.calls.find(c=>c[0]==='/stores/s1/staff'&&c[1]?.method==='POST');expect(JSON.parse(String(staffCall?.[1]?.body))).toMatchObject({memberId:'member-1'})
 })
 it('requires member selection and clears selection after search changes',async()=>{
  vi.mocked(api).mockImplementation(async path=>path.includes('memberSearch')?{members:[{id:'member-1',memberNo:'m20260930203055li3',name:'张小仓'}]}:path.endsWith('/staff')?{items:[]}:store)
  const w=await setup('/stores/s1');const input=w.find('input[placeholder="输入会员编号或昵称"]');const save=()=>w.findAll('button').find(b=>b.text()==='保存人员授权')!
  await input.setValue('m20260930203055li3');expect(save().attributes('disabled')).toBeDefined();await w.findAll('button').find(b=>b.text()==='查找会员')!.trigger('click');await flushPromises();await w.findAll('button').find(b=>b.text().includes('选择 张小仓'))!.trigger('click');expect(save().attributes('disabled')).toBeUndefined();await input.setValue('其他');expect(save().attributes('disabled')).toBeDefined()
 })
 it('reports no matching member without posting',async()=>{
  vi.mocked(api).mockImplementation(async path=>path.includes('memberSearch')?{members:[]}:path.endsWith('/staff')?{items:[]}:store)
  const w=await setup('/stores/s1');await w.find('input[placeholder="输入会员编号或昵称"]').setValue('missing');await w.findAll('button').find(b=>b.text()==='查找会员')!.trigger('click');await flushPromises();expect(w.text()).toContain('未找到已启用的会员');expect(vi.mocked(api).mock.calls.some(c=>c[1]?.method==='POST')).toBe(false)
 })
 it('edits existing authorization using selected member identity',async()=>{
  const existing={id:'staff-1',memberId:'member-1',memberNo:'m20260930203055li3',name:'张小仓',permissions:['accounts'],enabled:false,revision:1}
  vi.mocked(api).mockImplementation(async(path,init)=>path.endsWith('/staff')?(init?.method==='POST'?existing:{items:[existing]}):store)
  const w=await setup('/stores/s1');await w.findAll('button').find(b=>b.text()==='编辑授权')!.trigger('click');expect(w.text()).toContain('已选择：张小仓');await w.findAll('form')[1]!.trigger('submit');await flushPromises();const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='POST');expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({memberId:'member-1',permissions:['accounts'],enabled:false})
 })
 it('ignores staff list responses from the previous warehouse after navigation',async()=>{
  let finishA!:(value:unknown)=>void
  const pendingA=new Promise(resolve=>{finishA=resolve})
  const memberA={id:'staff-a',memberId:'member-a',memberNo:'ma',name:'甲仓人员',permissions:['products'],enabled:true,revision:1}
  const memberB={...memberA,id:'staff-b',memberId:'member-b',memberNo:'mb',name:'乙仓人员'}
  vi.mocked(api).mockImplementation(async path=>path==='/stores/s1/staff'?pendingA:path==='/stores/s2/staff'?{items:[memberB]}:{...store,id:path==='/stores/s2'?'s2':'s1'})
  const w=await setup('/stores/s1');await w.vm.$router.push('/stores/s2');await flushPromises();expect(w.text()).toContain('乙仓人员');finishA({items:[memberA]});await flushPromises();expect(w.text()).toContain('乙仓人员');expect(w.text()).not.toContain('甲仓人员')
  await w.findAll('button').find(b=>b.text()==='编辑授权')!.trigger('click');expect(w.text()).toContain('已选择：乙仓人员')
 })
 it('does not apply previous warehouse authorization completion or unlock a current save',async()=>{
  let finishA!:(value:unknown)=>void,finishB!:(value:unknown)=>void
  const pendingA=new Promise(resolve=>{finishA=resolve}),pendingB=new Promise(resolve=>{finishB=resolve})
  const memberA={id:'staff-a',memberId:'member-a',memberNo:'ma',name:'甲仓人员',permissions:['products'],enabled:true,revision:1},memberB={id:'staff-b',memberId:'member-b',memberNo:'mb',name:'乙仓人员',permissions:['orders'],enabled:true,revision:1}
  vi.mocked(api).mockImplementation(async(path,init)=>path.endsWith('/staff')?(init?.method==='POST'?(path.includes('/s1/')?pendingA:pendingB):{items:[path.includes('/s1/')?memberA:memberB]}):{...store,id:path==='/stores/s2'?'s2':'s1'})
  const w=await setup('/stores/s1');await w.findAll('button').find(b=>b.text()==='编辑授权')!.trigger('click');await w.findAll('form')[1]!.trigger('submit');await w.vm.$router.push('/stores/s2');await flushPromises();await w.findAll('button').find(b=>b.text()==='编辑授权')!.trigger('click');expect(w.text()).toContain('已选择：乙仓人员');expect(w.findAll('button').find(b=>b.text()==='保存人员授权')!.attributes('disabled')).toBeUndefined()
  await w.findAll('form')[1]!.trigger('submit');finishA({});await flushPromises();expect(w.text()).toContain('已选择：乙仓人员');expect(w.text()).not.toContain('前置仓人员授权已保存');expect(w.findAll('button').find(b=>b.text()==='保存中…')!.attributes('disabled')).toBeDefined();finishB({});await flushPromises();expect(w.text()).toContain('前置仓人员授权已保存')
 })
 it('locks fields for an account without manage permission',async()=>{vi.mocked(api).mockResolvedValue(store);const w=await setup('/stores/s1',StoreDetailView,{permissionCodes:['stores.read']} as Account);expect(w.find('fieldset').attributes('disabled')).toBeDefined();expect(w.text()).not.toContain('保存前置仓');expect(w.text()).not.toContain('保存人员授权')})
 it('reports a load failure with retry',async()=>{vi.mocked(api).mockRejectedValue(new Error('服务暂不可用'));const w=await setup('/stores/s1');expect(w.text()).toContain('服务暂不可用');vi.mocked(api).mockImplementation(async path=>path.endsWith('/staff')?{items:[]}:store);await w.findAll('button').find(b=>b.text()==='重新读取')!.trigger('click');await flushPromises();expect(w.find('input').element.value).toBe('湘江路店')})
 it('shows unknown balances and disabled withdrawal on account detail',async()=>{vi.mocked(api).mockResolvedValue({storeId:'s1',storeName:'湘江路店',settlementReady:false,withdrawalReady:false,reason:'结算规则尚未配置',balance:{pendingFen:null,availableFen:null,frozenFen:null,paidFen:null},income:[],withdrawals:[]});const w=await setup('/stores/accounts/s1',StoreAccountDetailView);expect(w.text()).toContain('结算规则尚未配置');expect(w.text()).not.toContain('¥0.00');expect(w.find('button[disabled]').text()).toBe('提现待配置')})
})
