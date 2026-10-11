import {afterEach,expect,it,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import {ElInput} from 'element-plus'
import CodeVersionsView from '../../src/views/CodeVersionsView.vue'
import SubscriptionMessageTasksView from '../../src/views/SubscriptionMessageTasksView.vue'
import type{Account} from '../../src/api'
vi.mock('../../src/api',async original=>({...await original<object>(),api:vi.fn()}))
import{api}from '../../src/api'
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.clearAllMocks()})
const cursor={props:['page','pageSize','hasPrevious','hasNext'],emits:['next','previous','size'],template:`<nav><button @click="$emit('next')">下一页测试</button><button @click="$emit('size',50)">50条测试</button></nav>`}
const global={components:{ElInput},stubs:{ReleaseWorkflowPanel:true,ElDialog:{props:['modelValue'],template:'<div v-if="modelValue" role="dialog"><slot/></div>'},AdminCursorPager:cursor}}
const account={accountId:'reader',permissionCodes:['code.version.read','notification.read']} as Account
it('versions and sync jobs use independent signed cursor tables, selection and true server keyword filters',async()=>{
 vi.mocked(api).mockImplementation(async path=>String(path).includes('readiness')?{checks:[],uploadKey:{revision:0}}:String(path).includes('code-sync-jobs')?{items:[{taskId:'job1',status:'FAILED',failureCode:'SOURCE_INVALID',createdAt:'2026-10-01T00:00:00Z'}],nextCursor:'job+opaque'}:{items:[{versionId:'version1',versionLabel:'v1',storageStatus:'READY',sourceDigest:'a'.repeat(64),packageBytes:100}],nextCursor:'version+opaque'})
 const w=mount(CodeVersionsView,{props:{account},global});wrappers.push(w);await flushPromises()
 expect(w.findAll('.admin-list-table table')).toHaveLength(2)
 await w.get('#code-versions-title').trigger('click')
 const section=w.get('[aria-labelledby=code-versions-title]');await section.get('tbody input').setValue(true);expect(section.text()).toContain('已选 1')
 await section.findAll('button').find(x=>x.text()==='下一页测试')!.trigger('click');await flushPromises();expect(vi.mocked(api).mock.calls.some(([path])=>String(path).includes('cursor=version%2Bopaque'))).toBe(true);expect(section.get('tbody input').element).toHaveProperty('checked',false)
 await section.findAll('button').find(x=>x.text()==='50条测试')!.trigger('click');await flushPromises();expect(vi.mocked(api).mock.lastCall?.[0]).toContain('limit=50');expect(vi.mocked(api).mock.lastCall?.[0]).not.toContain('cursor=')
 await section.get('form input').setValue('release-7');await section.get('form').trigger('submit');await flushPromises();expect(vi.mocked(api).mock.lastCall?.[0]).toContain('q=release-7')
 await section.findAll('button').find(x=>x.text()==='查看详情')!.trigger('click');await flushPromises();expect(w.find('[role=dialog]').exists()).toBe(true);expect(w.find('el-drawer').exists()).toBe(false)
})
it('message task table clears selection on query or cursor navigation, retains opaque cursor and 50 row limit',async()=>{
 vi.mocked(api).mockResolvedValue({items:[{taskId:'task1',eventType:'ORDER_PAID',status:'UNKNOWN',reasonCode:'UNKNOWN_RESULT',attemptCount:1}],nextCursor:'task+opaque'})
 const w=mount(SubscriptionMessageTasksView,{props:{account},global});wrappers.push(w);await flushPromises();expect(w.find('.admin-list-table table').exists()).toBe(true)
 await w.get('tbody input').setValue(true);await w.findAll('button').find(x=>x.text()==='下一页测试')!.trigger('click');await flushPromises();expect(vi.mocked(api).mock.lastCall?.[0]).toContain('cursor=task%2Bopaque');expect(w.get('tbody input').element).toHaveProperty('checked',false)
 await w.findAll('button').find(x=>x.text()==='50条测试')!.trigger('click');await flushPromises();expect(vi.mocked(api).mock.lastCall?.[0]).toContain('limit=50');expect(vi.mocked(api).mock.lastCall?.[0]).not.toContain('cursor=');expect(w.text()).not.toContain('每页最多 20 条')
 expect(w.findAll('button').some(button=>button.text()==='重发')).toBe(false);expect(w.text()).toContain('待核验')
})
