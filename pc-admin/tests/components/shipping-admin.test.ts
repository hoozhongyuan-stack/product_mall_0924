import {afterEach,describe,expect,it,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import ShippingRulesView from '../../src/views/shipping/ShippingRulesView.vue'
import {createRouter,createMemoryHistory} from 'vue-router'
import StoreDeliveryRulesView from '../../src/views/shipping/StoreDeliveryRulesView.vue'
import StoreSelector from '../../src/views/shipping/StoreSelector.vue'
import FenInput from '../../src/views/shipping/FenInput.vue'
import ShippingTrial from '../../src/views/shipping/ShippingTrial.vue'
import {api,type Account} from '../../src/api'
vi.mock('../../src/api',()=>({api:vi.fn()}))
const account={accountId:'a',permissionCodes:['settlement.shipping.manage']} as Account
const template={id:'t1',name:'默认快递',mode:'FIXED',firstFeeFen:800,firstUnits:1,extraFeeFen:200,extraUnits:1,freeThresholdFen:19900,regions:[],blockedRegions:[],productIds:[],enabled:true}
const config={revision:1,templates:[template],zones:[],skuWeights:[],campaign:{enabled:false,name:'平台包邮',startsAt:'2026-10-12T00:00:00+08:00',endsAt:'2026-10-13T00:00:00+08:00',minimumFen:19900,storeIds:[],deliveryModes:['EXPRESS']}}
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.clearAllMocks()})
const stubs={ElDialog:{template:'<section v-if="modelValue"><slot/><slot name="footer"/></section>',props:['modelValue']},AdminListPager:true,ShippingTargets:true}
async function setup(){const w=mount(ShippingRulesView,{props:{account},global:{stubs}});wrappers.push(w);await flushPromises();return w}
describe('shipping management',()=>{
 it('currency entry keeps displayed amount synchronized with cent model and nullable threshold',async()=>{
 const w=mount(FenInput,{props:{modelValue:6800,label:'商品金额',nullable:true,'onUpdate:modelValue':value=>w.setProps({modelValue:value})}});wrappers.push(w)
 await w.find('input').setValue('228.50');await flushPromises();expect(w.props('modelValue')).toBe(22850);expect(w.find('input').element.value).toBe('228.5')
 await w.setProps({disabled:true});expect(w.find('input').element.value).toBe('228.5');await w.setProps({disabled:false});await w.find('input').setValue('');expect(w.emitted('update:modelValue')?.at(-1)).toEqual([null])
 })

 it('saves current revision and immutable template fields from modal',async()=>{
 vi.mocked(api).mockImplementation(async(path,init)=>{if(path==='/shipping/config')return config;if(path==='/shipping/templates/t1'&&init?.method==='PUT')return {...config,revision:2,templates:[{...template,name:'新版运费'}]};return {items:[]}})
 const w=await setup();await w.findAll('button').find(b=>b.text()==='编辑')!.trigger('click');await flushPromises();await w.findAll('input').find(i=>i.attributes('maxlength')==='100'&&!i.attributes('aria-label'))!.setValue('新版运费');await w.findAll('button').find(b=>b.text()==='保存模板')!.trigger('click');await flushPromises();const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='PUT');expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({name:'新版运费',expectedRevision:1,firstFeeFen:800});expect(w.text()).toContain('配置已保存');expect(template.name).toBe('默认快递')
 })
 it('holds unsaved values and requires refresh after uncertain write',async()=>{
 vi.mocked(api).mockImplementation(async(path,init)=>{if(init?.method==='PUT')throw new Error('网络断开');return path==='/shipping/config'?config:{items:[]}})
 const w=await setup();await w.findAll('button').find(b=>b.text()==='编辑')!.trigger('click');await flushPromises();await w.findAll('button').find(b=>b.text()==='保存模板')!.trigger('click');await flushPromises();expect(w.text()).toContain('请重新读取并核对');expect(w.findAll('button').find(b=>b.text()==='保存模板')!.attributes('disabled')).toBeDefined()
 })
 it('closes a created zone after response loss and recovery instead of allowing repost',async()=>{
 let created=false
 const warehouse={id:'s1',name:'测试仓',city:'深圳市',latitude:22.5,longitude:114.2}
 const savedZone={id:'z1',storeId:'s1',name:'回复丢失围栏',kind:'CIRCLE',center:warehouse,radiusMeters:1800,priority:10,feeMode:'FIXED',feeFen:600,minimumFen:3900,freeThresholdFen:9900,startTime:'09:00',endTime:'23:00',enabled:true}
 vi.mocked(api).mockImplementation(async(path,init)=>{if(path==='/shipping/zones'&&init?.method==='POST'){created=true;throw new Error('响应丢失')}if(path==='/shipping/config')return {...config,revision:created?2:1,zones:created?[savedZone]:[]};if(path==='/shipping/stores/s1')return warehouse;throw new Error('Unexpected unrelated endpoint')})
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/stores/:storeId/delivery-rules',component:StoreDeliveryRulesView}]});await router.push('/stores/s1/delivery-rules')
 const w=mount(StoreDeliveryRulesView,{props:{account},global:{plugins:[router],stubs:{...stubs,DeliveryFenceMap:true}}});wrappers.push(w);await flushPromises()
 expect(w.text()).toContain('测试仓');expect(vi.mocked(api).mock.calls.some(c=>c[0].startsWith('/stores'))).toBe(false)
 await w.findAll('button').find(b=>b.text()==='新增配送围栏')!.trigger('click');await flushPromises();await w.findAll('input').find(i=>i.attributes('maxlength')==='100'&&!i.attributes('aria-label'))!.setValue('回复丢失围栏');await w.findAll('button').find(b=>b.text()==='保存围栏')!.trigger('click');await flushPromises()
 expect(w.findAll('button').find(b=>b.text()==='保存围栏')!.attributes('disabled')).toBeDefined();await w.findAll('button').find(b=>b.text()==='重新读取配置')!.trigger('click');await flushPromises()
 expect(w.text()).toContain('回复丢失围栏');expect(w.text()).toContain('原编辑窗口已关闭');expect(w.findAll('button').some(b=>b.text()==='保存围栏')).toBe(false);expect(vi.mocked(api).mock.calls.filter(c=>c[0]==='/shipping/zones'&&c[1]?.method==='POST')).toHaveLength(1)
 })
 it('reads activity warehouses through shipping permission without stores read',async()=>{
 vi.mocked(api).mockResolvedValue({items:[{id:'s1',name:'运费权限仓'}],total:1})
 const w=mount(StoreSelector,{props:{modelValue:[]}});wrappers.push(w);await (w.vm as unknown as {search:()=>Promise<void>}).search();await flushPromises()
 expect(vi.mocked(api).mock.calls[0]?.[0]).toContain('/shipping/stores?');expect(vi.mocked(api).mock.calls.some(c=>c[0].startsWith('/stores'))).toBe(false)
 })
 it('creates an editable complete campaign when configuration is empty',async()=>{
 vi.mocked(api).mockImplementation(async(path,init)=>path==='/shipping/config'?(init?.method==='PATCH'?config:{...config,campaign:{}}):{items:[]})
 const w=await setup();await w.findAll('button').find(b=>b.text()==='平台包邮活动')!.trigger('click');await flushPromises();await w.findAll('button').find(b=>b.text()==='保存活动')!.trigger('click');await flushPromises();const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='PATCH');const body=JSON.parse(String(call?.[1]?.body));expect(body.campaign).toMatchObject({name:'平台包邮活动',enabled:false,minimumFen:19900});expect(new Date(body.campaign.endsAt).getTime()).toBeGreaterThan(new Date(body.campaign.startsAt).getTime())
 })
 it('does not read shipping config without permission' ,async()=>{const w=mount(ShippingRulesView,{props:{account:{...account,permissionCodes:[]}},global:{stubs}});wrappers.push(w);await flushPromises();expect(vi.mocked(api)).not.toHaveBeenCalled();expect(w.text()).toContain('没有配送规则配置权限')})
 it('presents server cost sponsorship without computing client fees',async()=>{
 vi.mocked(api).mockResolvedValue({eligible:true,pending:false,message:'',costFen:800,customerFeeFen:0,warehouseBearsFen:0,platformBearsFen:800,freeSource:'PLATFORM',distanceMeters:null})
 const w=mount(ShippingTrial,{props:{storeId:'s1',mode:'EXPRESS'},global:{stubs:{ShippingTargets:{template:'<button @click="$emit(\'targets\',[{productId:\'p\',productName:\'酒\',skus:[{skuId:\'s\',specLabel:\'瓶\'}]}])">load</button>'}}}});wrappers.push(w)
 await w.find('button').trigger('click');const vm=w.vm as unknown as {skuId:string;province:string;trial:()=>Promise<void>};vm.skuId='s';vm.province='广东省';await flushPromises();await vm.trial();await flushPromises();expect(JSON.parse(String(vi.mocked(api).mock.calls.find(c=>c[0]==='/shipping/trial')?.[1]?.body)).storeId).toBe('s1');expect(w.text()).toContain('平台承担 ¥8.00');expect(w.text()).toContain('用户运费 ¥0.00');expect(w.text()).toContain('平台活动')
 })
})
