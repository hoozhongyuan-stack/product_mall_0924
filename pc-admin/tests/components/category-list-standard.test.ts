import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import {createRouter,createMemoryHistory} from 'vue-router'
import CategoryManagement from '../../src/views/catalog/CategoryManagement.vue'
import {api} from '../../src/api'
vi.mock('../../src/api',async original=>({...await original<any>(),api:vi.fn()}))
const rows=Array.from({length:45},(_,n)=>[{id:`r${n}`,parentId:null,name:`分类${n}`,sortOrder:n,status:n===44?'INACTIVE':'ACTIVE',revision:1},{id:`c${n}`,parentId:`r${n}`,name:n===44?'目标子类':`子类${n}`,sortOrder:0,status:'ACTIVE',revision:1}]).flat()
const wrappers:ReturnType<typeof mount>[]=[]
beforeEach(()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:200,json:async()=>({success:true,data:[]})}))
 vi.mocked(api).mockResolvedValue(rows as any)
})
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks();vi.unstubAllGlobals()})
async function setup(){const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:CategoryManagement}]});await router.push('/');const w=mount(CategoryManagement,{props:{account:{accountId:'actor',permissionCodes:['catalog.read']} as any},global:{plugins:[router],stubs:{CategoryImageEditor:true,ElDialog:{props:['modelValue'],template:'<section v-if="modelValue"><slot/><slot name="footer"/></section>'}}}});wrappers.push(w);await flushPromises();return w}
describe('category list keeps complete tree groups',()=>{
 it('pages by roots with their children, and clears selection when page size changes',async()=>{
  const w=await setup();expect(w.findAll('tbody tr')).toHaveLength(40)
  await w.get('[aria-label="选择当前页分类"]').setValue(true);expect(w.find('.admin-selection-bar').text()).toContain('40')
  const pager=w.findComponent({name:'AdminListPager'});expect(pager.props('total')).toBe(45)
  pager.vm.$emit('change',3,20);await flushPromises()
  expect(w.findAll('tbody tr')).toHaveLength(10);expect(w.find('.admin-selection-bar').text()).toContain('0')
  expect(w.findAll('tbody tr').map(r=>r.text())).toEqual(expect.arrayContaining([expect.stringContaining('分类40'),expect.stringContaining('子类40')]))
  pager.vm.$emit('change',1,50);await flushPromises();expect(w.findAll('tbody tr')).toHaveLength(90)
 })
 it('retains the parent of matching child rows and can compare selected categories read-only',async()=>{
  const w=await setup();await w.get('[aria-label="分类关键词"]').setValue('目标')
  await w.get('form[role=search]').trigger('submit');await flushPromises()
  expect(w.findAll('tbody tr')).toHaveLength(2);expect(w.text()).toContain('分类44');expect(w.text()).toContain('目标子类')
  await w.get('[aria-label="选择当前页分类"]').setValue(true)
  await w.findAll('button').find(b=>b.text()==='核对所选分类')!.trigger('click');await flushPromises()
  const comparison=w.findComponent({name:'AdminSelectedRowsDialog'})
  expect(comparison.props('rows')).toEqual(expect.arrayContaining([expect.objectContaining({name:'目标子类',parent:'分类44'})]))
  expect(vi.mocked(api).mock.calls.every(([,init])=>!init?.method)).toBe(true)
  expect(w.findAll('button').some(b=>b.text()==='编辑')).toBe(false)
 })
 it('filters active children without losing an inactive parent, and clears selection immediately on filter edit',async()=>{
  const w=await setup();w.findComponent({name:'AdminListPager'}).vm.$emit('change',3,20);await flushPromises()
  await w.get('[aria-label="选择当前页分类"]').setValue(true)
  await w.get('[aria-label="分类状态"]').setValue('ACTIVE');expect(w.find('.admin-selection-bar').text()).toContain('0')
  await w.get('[aria-label="分类关键词"]').setValue('目标');await w.get('form[role=search]').trigger('submit');await flushPromises()
  expect(w.findAll('tbody tr')).toHaveLength(2);expect(w.findAll('.admin-status-badge').map(b=>b.text())).toEqual(['停用','启用'])
 })
})
