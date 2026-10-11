import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import AuditView from '../../src/views/AuditView.vue'
import { api } from '../../src/api'
import { assetQuery } from '../../src/shared/media-library'
vi.mock('../../src/api',async original=>({...await original<any>(),api:vi.fn()}))
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks()})
it('asset paging requests the chosen server page size and preserves upload-only restriction',()=>{
  const q=new URLSearchParams(assetQuery({page:2,pageSize:50,q:'酒'},false))
  expect(q.get('pageSize')).toBe('50');expect(q.get('binding')).toBe('UNBOUND');expect(q.get('page')).toBe('2')
})
it('audit current-page selection and detail do not grant export or write access',async()=>{
  vi.mocked(api).mockResolvedValue({items:[{id:'l1',occurredAt:'2026-10-11T00:00:00Z',result:'FAILED',actionCode:'catalog.update',actorId:'a',objectType:'product',objectId:'p',before:{name:'<script>x</script>'},after:{name:'new'}}],nextCursor:null,from:'2026-10-01',to:'2026-10-11',timeZone:'Asia/Shanghai'} as any)
  const w=mount(AuditView,{props:{account:{accountId:'a',permissionCodes:['audit.read']} as any},global:{stubs:{ElDialog:{props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'}}}});wrappers.push(w);await flushPromises()
  await w.get('tbody input[type=checkbox]').setValue(true)
  expect(w.find('.admin-selection-bar').text()).toContain('1')
  await w.findAll('button').find(b=>b.text()==='查看脱敏详情')!.trigger('click');await flushPromises()
  expect(w.find('script').exists()).toBe(false);expect(w.text()).toContain('<script>');expect(w.text()).not.toContain('创建导出任务')
  await w.findAll('button').find(b=>b.text()==='重新读取')!.trigger('click');await flushPromises();expect(w.find('.admin-selection-bar').text()).toContain('0')
  expect(vi.mocked(api).mock.calls.every(c=>!c[1]?.method)).toBe(true)
})
