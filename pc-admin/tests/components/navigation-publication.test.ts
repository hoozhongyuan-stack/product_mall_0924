import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SingletonConfigView from '../../src/views/SingletonConfigView.vue'
import { api, ApiError, type Account } from '../../src/api'
vi.mock('vue-router', async original=>({...await original<any>(),onBeforeRouteLeave:vi.fn()}))
vi.mock('../../src/api',async original=>({...await original<any>(),api:vi.fn()}))
const config={items:['HOME','CATEGORY','CART','ME'].map((key,index)=>({key,label:['首页','分类','购物车','我的'][index],iconAssetId:null,selectedIconAssetId:null}))}
const draft={revision:1,publicationRevision:1,publishedRevision:1,publishedVersionId:'old',config}
const account={accountId:'a',permissionCodes:['navigation.read','navigation.edit','navigation.publish']} as Account
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks();sessionStorage.clear()})
const button=(w:ReturnType<typeof mount>,label:string)=>w.findAll('button').find(b=>b.text()===label)!
function render(){const w=mount(SingletonConfigView,{props:{account,domain:'navigation'},global:{stubs:{PublicationHistory:true,AssetPicker:true,'el-input':true,'el-select':true,'el-option':true,'el-dialog':{props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'},'el-button':{props:['disabled'],template:'<button :disabled="disabled"><slot/></button>'}}}});wrappers.push(w);return w}
it('normalizes the old four-item configuration, saves v2 draft separately and does not publish it',async()=>{
 vi.mocked(api).mockImplementation(async(path,init)=>path.startsWith('/pages?')?{rows:[],total:0}:init?.method==='PUT'?{...draft,revision:2,config:JSON.parse(init.body as string).config}:draft as never)
 const w=render();await flushPromises();await w.get('[aria-label="导航名称 1"]').setValue('商城');await button(w,'保存草稿').trigger('click');await flushPromises()
 const write=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='PUT')!
 expect(JSON.parse(write[1]!.body as string).config).toMatchObject({schemaVersion:2,items:[{target:{type:'HOME'}},{target:{type:'CATEGORY'}},{target:{type:'CART'}},{target:{type:'ME'}}]})
 expect(w.text()).toContain('线上修订 1');expect(w.text()).toContain('线上配置未变化')
 expect(vi.mocked(api).mock.calls.some(c=>c[0].endsWith('/publish'))).toBe(false)
})
it('blocks publication with explicit target error and leaves previous online state unchanged',async()=>{
 const v2={schemaVersion:2,items:[{...config.items[0],target:{type:'MICRO',pageId:'d7367cb7-9723-4cba-bbb7-129b57158e32'}}]}
 const current={...draft,revision:2,config:v2}
 vi.mocked(api).mockImplementation(async(path)=>{
  if(path.startsWith('/pages?'))return {rows:[],total:0} as never
  if(path==='/navigation/preview')return {revision:2,config:v2} as never
  if(path==='/navigation/publish')throw new ApiError('导航关联页面尚未发布，请先发布页面。',422,'NAVIGATION_TARGET_UNPUBLISHED')
  return current as never
 })
 const w=render();await flushPromises();await button(w,'服务端预览').trigger('click');await flushPromises()
 await button(w,'发布配置').trigger('click');await button(w,'确认发布').trigger('click');await flushPromises()
 expect(w.text()).toContain('导航关联页面尚未发布');expect(w.text()).toContain('线上修订 1')
 expect(vi.mocked(api).mock.calls.some(c=>c[0].includes('/versions'))).toBe(false)
})
