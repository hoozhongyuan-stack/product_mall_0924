import {afterEach,expect,it,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import {createRouter,createMemoryHistory} from 'vue-router'
import ReturnAcceptancePanel from '../../src/views/aftersales/ReturnAcceptancePanel.vue'
import type{Account} from '../../src/api'
import type{AfterSaleCase} from '../../src/views/aftersales/types'
vi.mock('../../src/shared/confirm',()=>({confirmAction:vi.fn()}))
import {confirmAction} from '../../src/shared/confirm'
const wrappers:ReturnType<typeof mount>[]=[]
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.clearAllMocks();sessionStorage.clear()})
it('embedded return acceptance refuses discard and rechecks changes while its close prompt is open',async()=>{
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/aftersales',component:{template:'<div/>'}}]});await router.push('/aftersales');await router.isReady()
 const w=mount(ReturnAcceptancePanel,{props:{account:{accountId:'actor',permissionCodes:['aftersale.return.accept']} as Account,item:{caseId:'case',status:'WAITING_RETURN',quantity:1,amountFen:100} as AfterSaleCase},global:{plugins:[router],stubs:{ElDialog:true}}});wrappers.push(w)
 await w.get('textarea').setValue('验收说明');vi.mocked(confirmAction).mockResolvedValue(false)
 const panel=w.vm as unknown as {mayClose:()=>Promise<boolean>};expect(typeof panel.mayClose).toBe('function');expect(await panel.mayClose()).toBe(false)
 let resolve!:(value:boolean)=>void;vi.mocked(confirmAction).mockReturnValueOnce(new Promise(done=>{resolve=done}));const closing=panel.mayClose();await w.get('textarea').setValue('确认期间继续编辑');await flushPromises();resolve(true);expect(await closing).toBe(false)
 vi.mocked(confirmAction).mockResolvedValue(true);expect(await panel.mayClose()).toBe(true)
})
