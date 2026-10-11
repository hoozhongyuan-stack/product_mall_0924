import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ElementPlus from 'element-plus'
import InventoryView from '../../src/views/InventoryView.vue'
import { api } from '../../src/api'
import { confirmAction } from '../../src/shared/confirm'
vi.mock('../../src/shared/confirm',()=>({confirmAction:vi.fn()}))
vi.mock('../../src/api', async original => ({...await original<any>(),api:vi.fn()}))
const wrappers: ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.resetAllMocks()})
async function setup(tab='warehouses') {
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:InventoryView,meta:{inventoryTab:tab}}]});await router.push('/');await router.isReady()
  vi.mocked(api).mockImplementation(async path=>path==='/warehouses'?{items:Array.from({length:45},(_,i)=>({warehouseId:`w${i}`,code:`WH${i}`,name:`仓库${i}`,enabled:i%2===0,isDefault:i===0}))}:{items:[],page:1,pageSize:20,total:0})
  const w=mount(InventoryView,{props:{account:{accountId:'actor',permissionCodes:['inventory.read','inventory.manage']} as any},global:{plugins:[router,ElementPlus]}});wrappers.push(w);await flushPromises();return w
}
it('warehouse management paginates a complete collection and exposes query, selection and batch review',async()=>{
  const w=await setup(); expect(w.findAll('tbody tr')).toHaveLength(20)
  expect(w.find('.admin-list-pager').exists()).toBe(true)
  await w.get('input[placeholder="输入仓库名称或编号"]').setValue('WH44')
  await w.get('form.inventory-filters').trigger('submit');await flushPromises()
  expect(w.findAll('tbody tr')).toHaveLength(1);expect(w.text()).toContain('仓库44')
  await w.get('tbody input[type=checkbox]').setValue(true);expect(w.find('.admin-selection-bar').text()).toContain('1')
  await w.findAll('button').find(b=>b.text()==='重置')!.trigger('click');await flushPromises();expect(w.find('.admin-selection-bar').text()).toContain('0')
})
it('inventory document creation opens a protected modal rather than an inline form',async()=>{
  const w=await setup('inbounds');await w.findAll('button').find(b=>b.text().includes('创建入库单'))!.trigger('click');await flushPromises()
  expect(w.find('.el-dialog').exists()).toBe(true)
  expect(w.find('.el-dialog input').exists()).toBe(true)
})

it('warehouse close button retains unsaved draft when cancelled and closes only after confirmation',async()=>{
  const w=await setup();await w.findAll('button').find(b=>b.text()==='新建仓库')!.trigger('click');await flushPromises()
  await w.get('input[placeholder="例如 WH-001"]').setValue('UNSAVED')
  vi.mocked(confirmAction).mockResolvedValueOnce(false)
  await w.get('.el-dialog__headerbtn').trigger('click');await flushPromises()
  expect(w.get('input[placeholder="例如 WH-001"]').element).toHaveProperty('value','UNSAVED')
  expect(w.get('.el-dialog').isVisible()).toBe(true)
  vi.mocked(confirmAction).mockResolvedValueOnce(true)
  await w.get('.el-dialog__headerbtn').trigger('click');await flushPromises()
  expect(w.find('form.inventory-form').exists()).toBe(false)
  expect(api).not.toHaveBeenCalledWith('/warehouses',expect.objectContaining({method:'POST'}))
})
