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
  vi.mocked(api).mockImplementation(async(path,init)=>{if(path.includes('map-search'))throw new Error('高德地图密钥未配置');if(path.endsWith('/staff'))return init?.method==='POST'?{}:{items:[]};return store})
  const w=await setup('/stores/s1')
  await w.find('input[maxlength="100"]').setValue('新店名');await w.find('form').trigger('submit');await flushPromises();const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='PATCH');expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('warehouseId');expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({name:'新店名',revision:1});expect(w.text()).toContain('门店资料已保存')
  await w.find('input[placeholder="输入门店名称或地址"]').setValue('长沙');await w.findAll('button').find(b=>b.text()==='搜索高德位置')!.trigger('click');await flushPromises();expect(w.text()).toContain('高德地图密钥未配置')
  await w.find('input[maxlength="36"]').setValue('member-1');await w.findAll('form')[1]!.trigger('submit');await flushPromises();expect(vi.mocked(api).mock.calls.find(c=>c[0]==='/stores/s1/staff'&&c[1]?.method==='POST')).toBeTruthy()
 })
 it('locks fields for an account without manage permission',async()=>{vi.mocked(api).mockResolvedValue(store);const w=await setup('/stores/s1',StoreDetailView,{permissionCodes:['stores.read']} as Account);expect(w.find('fieldset').attributes('disabled')).toBeDefined();expect(w.text()).not.toContain('保存门店');expect(w.text()).not.toContain('保存人员授权')})
 it('reports a load failure with retry',async()=>{vi.mocked(api).mockRejectedValue(new Error('服务暂不可用'));const w=await setup('/stores/s1');expect(w.text()).toContain('服务暂不可用');vi.mocked(api).mockImplementation(async path=>path.endsWith('/staff')?{items:[]}:store);await w.findAll('button').find(b=>b.text()==='重新读取')!.trigger('click');await flushPromises();expect(w.find('input').element.value).toBe('湘江路店')})
 it('shows unknown balances and disabled withdrawal on account detail',async()=>{vi.mocked(api).mockResolvedValue({storeId:'s1',storeName:'湘江路店',settlementReady:false,withdrawalReady:false,reason:'结算规则尚未配置',balance:{pendingFen:null,availableFen:null,frozenFen:null,paidFen:null},income:[],withdrawals:[]});const w=await setup('/stores/accounts/s1',StoreAccountDetailView);expect(w.text()).toContain('结算规则尚未配置');expect(w.text()).not.toContain('¥0.00');expect(w.find('button[disabled]').text()).toBe('提现待配置')})
})
