import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import AdminListPager from '../../src/shared/list/AdminListPager.vue'
import AdminStatusBadge from '../../src/shared/list/AdminStatusBadge.vue'
import AdminSelectedRowsDialog from '../../src/shared/list/AdminSelectedRowsDialog.vue'
import { useCurrentPageSelection } from '../../src/shared/list/useCurrentPageSelection'
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
it('pager emits real page and size choices including first-page size reset', async () => {
  const w = mount(AdminListPager, {props:{page:2,pageSize:20,total:80}, global:{stubs:{ElPagination:{emits:['current-change','size-change'],template:'<div><button @click="$emit(\'current-change\',3)">page</button><button @click="$emit(\'size-change\',50)">size</button></div>'}}}})
  wrappers.push(w)
  await w.findAll('button')[0]!.trigger('click'); await w.findAll('button')[1]!.trigger('click')
  expect(w.emitted('change')).toEqual([[3,20],[1,50]])
})
it('selection ignores out-of-page ids and resets after a new server result', async () => {
  const rows = ref([{id:'a'},{id:'b'}])
  let selection!: ReturnType<typeof useCurrentPageSelection<{id:string}>>
  const w = mount(defineComponent({setup(){selection=useCurrentPageSelection(()=>rows.value,row=>row.id);return()=>null}})); wrappers.push(w)
  selection.toggle('foreign',true); expect(selection.selectedIds.value).toEqual([])
  selection.toggle('a',true); expect(selection.indeterminate.value).toBe(true)
  selection.togglePage(true); expect(selection.allSelected.value).toBe(true)
  rows.value=[{id:'c'}]; await flushPromises(); expect(selection.selectedIds.value).toEqual([])
  selection.togglePage(false); expect(selection.allSelected.value).toBe(false)
})
it('withdrawal approval remains processing rather than completed', () => {
  const w=mount(AdminStatusBadge,{props:{status:'APPROVED',label:'待打款'}}); wrappers.push(w)
  expect(w.classes()).toContain('admin-status-processing'); expect(w.text()).toBe('待打款')
})
it('selected-record preview escapes untrusted text and has no write action', () => {
  const w=mount(AdminSelectedRowsDialog,{props:{modelValue:true,title:'核对所选',columns:[{key:'name',label:'名称'},{key:'missing',label:'空值'}],rows:[{name:'<script>alert(1)</script>'}]},global:{stubs:{ElDialog:{template:'<div><slot/><slot name="footer"/></div>'}}}}); wrappers.push(w)
  expect(w.find('script').exists()).toBe(false); expect(w.text()).toContain('<script>'); expect(w.text()).toContain('—'); expect(w.findAll('button').map(b=>b.text())).toEqual(['关闭'])
})
