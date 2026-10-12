import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import HomeSelectionDialog from '../../src/views/pages/HomeSelectionDialog.vue'
import { api, type Account } from '../../src/api'
vi.mock('../../src/api',async original=>({...await original<any>(),api:vi.fn()}))
const account={accountId:'actor',permissionCodes:['page.read','page.publish']} as Account
const target={pageId:'p1',name:'精选',revision:4,publishedRevision:2,publicationRevision:3,isHome:false}
const stub={props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'}
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks();sessionStorage.clear()})
function mountDialog(props={}){const w=mount(HomeSelectionDialog,{props:{account,target,...props},global:{stubs:{ElDialog:stub,ElButton:{props:['disabled'],template:'<button :disabled="disabled"><slot/></button>'}}}});wrappers.push(w);return w}
it('selects a published snapshot with both revisions and reads current selection after receipt',async()=>{
 vi.mocked(api).mockImplementation(async(path,init)=>init?.method==='POST'?{pageId:'p1',revision:8}:path==='/pages/home-selection'?{pageId:'old',revision:7}:{} as never)
 const w=mountDialog({pages:[{...target,pageId:'old',name:'原首页',isHome:true}]});await flushPromises()
 expect(w.text()).toContain('当前首页：原首页');expect(w.text()).not.toContain('设置修订')
 await w.findAll('button').find(b=>b.text()==='确认设为首页')!.trigger('click');await flushPromises()
 const write=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='POST')!
 expect(write[0]).toBe('/pages/p1/set-home');expect(JSON.parse(write[1]!.body as string)).toEqual({expectedHomeRevision:7,expectedPublicationRevision:3})
 expect(write[1]!.headers).toHaveProperty('Idempotency-Key');expect(w.text()).toContain('当前首页')
 expect(w.emitted('changed')).toHaveLength(1)
})
it('never submits an unpublished target and keeps the original request after an unknown result',async()=>{
 vi.mocked(api).mockResolvedValue({pageId:'old',revision:7} as never)
 const unpub=mountDialog({target:{...target,publishedRevision:null}});await flushPromises()
 expect(unpub.text()).toContain('先发布');expect(unpub.findAll('button').find(b=>b.text()==='确认设为首页')!.attributes('disabled')).toBeDefined();unpub.unmount()
 let writes=0
 vi.mocked(api).mockImplementation(async(path,init)=>{if(init?.method==='POST'){writes++;if(writes===1)throw new Error('连接中断');return {pageId:'p1',revision:8} as never}return {pageId:'old',revision:7} as never})
 const w=mountDialog();await flushPromises();await w.findAll('button').find(b=>b.text()==='确认设为首页')!.trigger('click');await flushPromises()
 await w.findAll('button').find(b=>b.text()==='恢复原请求')!.trigger('click');await flushPromises()
 const calls=vi.mocked(api).mock.calls.filter(c=>c[1]?.method==='POST')
 expect(calls[0]![1]!.headers).toEqual(calls[1]![1]!.headers)
 expect(calls[0]![1]!.body).toEqual(calls[1]![1]!.body)
})
it('restores an interrupted request after remount and can close safely after readback',async()=>{
 const original={pageId:'p1',name:'精选',key:'original',body:{expectedHomeRevision:7,expectedPublicationRevision:3}}
 sessionStorage.setItem('mall-home-selection:actor',JSON.stringify(original))
 vi.mocked(api).mockImplementation(async(path,init)=>init?.method==='POST'?{pageId:'p1',revision:8}:{pageId:'p1',revision:8} as never)
 const w=mountDialog({target:null});await flushPromises();expect(w.text()).toContain('原请求恢复')
 await w.findAll('button').find(b=>b.text()==='恢复原请求')!.trigger('click');await flushPromises()
 expect(w.text()).toContain('已将“精选”设为首页');expect(sessionStorage.getItem('mall-home-selection:actor')).toBeNull()
 await w.findAll('button').find(b=>b.text()==='关闭')!.trigger('click');expect(w.emitted('close')).toHaveLength(1)
})
it('blocks corrupted recovery records and storage failure before a write',async()=>{
 sessionStorage.setItem('mall-home-selection:actor','broken')
 vi.mocked(api).mockResolvedValue({pageId:'old',revision:7} as never)
 const broken=mountDialog({target:null});await flushPromises();expect(broken.text()).toContain('已阻止新操作');expect(api).not.toHaveBeenCalled();broken.unmount();sessionStorage.clear()
 const w=mountDialog();await flushPromises();vi.spyOn(sessionStorage,'setItem').mockImplementation(()=>{throw new Error('disabled')})
 await w.findAll('button').find(b=>b.text()==='确认设为首页')!.trigger('click');await flushPromises();expect(w.text()).toContain('操作未提交');expect(vi.mocked(api).mock.calls.some(c=>c[1]?.method)).toBe(false)
})
it('keeps conflict or unreadable pointer recoverable and respects permission changes',async()=>{
 vi.mocked(api).mockResolvedValueOnce({pageId:'old'} as never).mockResolvedValue({pageId:'old',revision:7} as never)
 const w=mountDialog();await flushPromises();expect(w.text()).toContain('格式不正确')
 await w.findAll('button').find(b=>b.text()==='重新读取当前首页')!.trigger('click');await flushPromises()
 vi.mocked(api).mockRejectedValueOnce(new (await import('../../src/api')).ApiError('首页已变化',409,'HOME_REVISION_CONFLICT'))
 await w.findAll('button').find(b=>b.text()==='确认设为首页')!.trigger('click');await flushPromises();expect(w.text()).toContain('首页已变化');expect(sessionStorage.getItem('mall-home-selection:actor')).toBeNull()
 await w.setProps({account:{...account,permissionCodes:['page.read']}});expect(w.findAll('button').find(b=>b.text()==='确认设为首页')!.attributes('disabled')).toBeDefined()
})
