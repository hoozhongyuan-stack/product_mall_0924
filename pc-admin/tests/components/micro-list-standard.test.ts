import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import MicroPageView from '../../src/views/MicroPageView.vue'
import AdminListPager from '../../src/shared/list/AdminListPager.vue'
import { api } from '../../src/api'
vi.mock('../../src/api',async original=>({...await original<any>(),api:vi.fn()}))
const wrappers: ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks()})
it('micro-page management uses server paging and selected-page review without publishing',async()=>{
  vi.stubGlobal('fetch',vi.fn(async (url:string)=>new Response(JSON.stringify({success:true,data:url.includes('/products')?{rows:[]}:[]}))))
  vi.mocked(api).mockImplementation(async path=>path.startsWith('/pages?')?{rows:[{pageId:'p',name:'评审页面',revision:1,publishedRevision:null}],page:Number(new URLSearchParams(path.split('?')[1]).get('page')),pageSize:Number(new URLSearchParams(path.split('?')[1]).get('pageSize')),total:41}:[])
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/pages/micro',component:MicroPageView}]});await router.push('/pages/micro');await router.isReady()
  const w=mount(MicroPageView,{props:{account:{accountId:'a',permissionCodes:['page.read','page.edit']} as any},global:{plugins:[router],stubs:{ElDialog:{props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'}}}});wrappers.push(w);await flushPromises()
  expect(vi.mocked(api).mock.calls.some(c=>c[0].includes('pageSize=20'))).toBe(true)
  await w.get('tbody input[type=checkbox]').setValue(true);expect(w.find('.admin-selection-bar').text()).toContain('1')
  w.getComponent(AdminListPager).vm.$emit('change',1,50);await flushPromises()
  expect(vi.mocked(api).mock.calls.some(c=>c[0].includes('pageSize=50'))).toBe(true);expect(w.find('.admin-selection-bar').text()).toContain('0')
  expect(vi.mocked(api).mock.calls.every(c=>!c[1]?.method)).toBe(true)
})
