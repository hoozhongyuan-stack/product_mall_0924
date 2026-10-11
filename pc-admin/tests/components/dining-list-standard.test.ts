import {afterEach,describe,expect,it,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import ElementPlus from 'element-plus'
import FinanceView from '../../src/views/dining-alliance/FinanceView.vue'
import {api,type Account} from '../../src/api'
vi.mock('../../src/api',()=>({api:vi.fn()}))
vi.mock('../../src/shared/confirm',()=>({confirmAction:vi.fn(async()=>true)}))
const account={accountId:'admin',permissionCodes:['dining.finance.read','dining.finance.manage']} as Account
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.forEach(w=>w.unmount());wrappers.length=0;vi.resetAllMocks()})
async function render(){vi.mocked(api).mockImplementation(async(path,init)=>{
 if(init?.method)return {id:'w1',status:'APPROVED',revision:2}
 if(String(path).includes('/restaurants?'))return {items:[{id:'r1',name:'餐厅'}],total:1}
 return {availableFen:500,pendingFen:0,heldFen:0,totalCommissionFen:500,frozenFen:100,incomes:[],withdrawals:[{id:'w1',amountFen:100,status:'PENDING',revision:1,reason:'',createdAt:'2026-10-11'}],incomePagination:{page:1,pageSize:20,total:125},withdrawalPagination:{page:1,pageSize:20,total:120}}
 });const w=mount(FinanceView,{props:{account},global:{plugins:[ElementPlus]}});wrappers.push(w);await flushPromises();return w}
describe('餐饮财务完整历史与弹窗审核',()=>{
 it('requests independent server history pages and filters',async()=>{const w=await render();expect(api).toHaveBeenCalledWith(expect.stringContaining('incomePageSize=20'));const pagers=w.findAllComponents({name:'AdminListPager'});expect(pagers).toHaveLength(2);pagers[0]!.vm.$emit('change',3,50);await flushPromises();expect(api).toHaveBeenCalledWith(expect.stringContaining('withdrawalPage=3'));expect(api).toHaveBeenCalledWith(expect.stringContaining('withdrawalPageSize=50'));expect(w.text()).not.toContain('最近100条')})
 it('opens a review modal and blocks blank rejection without writing',async()=>{const w=await render();await w.findAll('button').find(b=>b.text()==='审核')!.trigger('click');await flushPromises();expect(w.findComponent({name:'ElDialog'}).props('modelValue')).toBe(true);const decision=w.findAllComponents({name:'ElSelect'}).at(-1)!;decision.vm.$emit('update:modelValue','reject');await flushPromises();await w.findAll('button').find(b=>b.text()==='确认审核')!.trigger('click');await flushPromises();expect(w.text()).toContain('驳回时请填写原因');expect(vi.mocked(api).mock.calls.filter(c=>c[1]?.method)).toHaveLength(0)})
})
