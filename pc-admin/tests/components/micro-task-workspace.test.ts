import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import MicroPageView from '../../src/views/MicroPageView.vue'
import { api } from '../../src/api'
vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.resetAllMocks(); sessionStorage.clear() })
async function setup(runtime=1, blocked=false, codePermission=false) {
  const config={schemaVersion:2,pageType:'MICRO',theme:{pageBackgroundColor:'#ffffff',headerBackgroundColor:'#ffffff',brandTextColor:'#111111'},components:[]}
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({success:true,data:[]}))))
  vi.mocked(api).mockImplementation(async path => {
    if(path==='/pages/capabilities')return {runtimeSchemaVersion:runtime} as never
    if(path.startsWith('/pages?'))return {rows:[{pageId:'p1',name:'活动页',revision:1,publishedRevision:null}],total:1,page:1,pageSize:10} as never
    if(path.endsWith('/draft'))return {pageId:'p1',name:'活动页',revision:1,publicationRevision:0,config} as never
    if(path.endsWith('/release-report'))return {pageId:'p1',revision:1,publicationRevision:0,publishedVersionId:null,schemaVersion:2,runtimeSchemaVersion:runtime,runtimeSupported:runtime>=2,canPublish:!blocked,diff:{addedComponentIds:[],removedComponentIds:[],updatedComponentIds:[],orderChanged:false,themeChanged:false,metadataChanged:false},issues:blocked?[{severity:'ERROR',code:'INVALID_REFERENCE',message:'所选链接不可用',path:'components'}]:[]} as never
    if(path.endsWith('/preview'))return {revision:1,config} as never
    return [] as never
  })
  const account={accountId:'actor',permissionCodes:['page.read','page.edit','page.publish', ...(codePermission ? ['code.version.read'] : [])]}
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/pages/micro',component:MicroPageView,props:{account}}]})
  await router.push('/pages/micro?pageId=p1')
  const wrapper=mount(defineComponent({template:'<RouterView />'}),{global:{plugins:[router],stubs:{PublicationHistory:true,PageCopyPanel:true,PageReusePanel:true,PageMetadataEditor:true,HomePagePreview:true,PageComponentEditor:true,ElDialog:true}}})
  wrappers.push(wrapper);await flushPromises();return wrapper
}
function button(w:ReturnType<typeof mount>,name:string){return w.findAll('button').find(b=>b.text()===name)!}
it('opens a selected page in a compact workspace and leaves management accessible',async()=>{
  const w=await setup()
  expect(w.get('details.micro-page-list').attributes('open')).toBeUndefined()
  expect(w.get('details.micro-page-list summary').text()).toContain('活动页')
  const html=w.html();expect(html.indexOf('home-editor-grid')).toBeLessThan(html.indexOf('page-metadata-panel'))
  expect(w.text()).toContain('保存草稿 → 检查并预览 → 发布')
  expect(w.text()).toContain('当前小程序暂不支持此页面内容')
  expect(w.get('[aria-label="页面背景色十六进制值"]').exists()).toBe(true)
})
it('checks references before generating preview and never enables publish for unsupported runtime',async()=>{
  const w=await setup(1)
  await button(w,'检查并预览').trigger('click');await flushPromises()
  const calls=vi.mocked(api).mock.calls.map(([path])=>path)
  expect(calls.indexOf('/pages/p1/release-report')).toBeLessThan(calls.indexOf('/pages/p1/preview'))
  expect(button(w,'发布微页面').attributes('disabled')).toBeDefined()
})
it('keeps publishing blocked when a current report identifies an invalid reference even with preview',async()=>{
  const w=await setup(4,true)
  await button(w,'检查并预览').trigger('click');await flushPromises()
  expect(button(w,'发布微页面').attributes('disabled')).toBeDefined()
  expect(w.text()).toContain('发布检查未通过')
})

it.each([true,false])('shows deployment next-step only for code.version.read: %s',async permitted=>{
  const w=await setup(1,false,permitted)
  expect(w.find('a[href="/store/code-versions"]').exists()).toBe(permitted)
})
it('rechecks the latest report at final publish submission',async()=>{
  const w=await setup(4)
  await button(w,'检查并预览').trigger('click');await flushPromises()
  expect(button(w,'发布微页面').attributes('disabled')).toBeUndefined()
  const vm=w.getComponent(MicroPageView).vm as unknown as {publishPassword:string;report:{canPublish:boolean};publish:()=>Promise<void>}
  vm.publishPassword='synthetic-test-password'
  vm.report={...vm.report,canPublish:false}
  vi.mocked(api).mockClear()
  await vm.publish();await flushPromises()
  expect(api).not.toHaveBeenCalled()
})
