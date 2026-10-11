import { watch } from 'vue'
import type { Account } from '../../../api'
import { usePersistentOperation } from '../../../shared/persistent-operation'
import { pendingProcurement, clearProcurementSnapshots, recoveryDecision, resultMatches } from './domain.mjs'
export function useProcurementOperation(account:()=>Account,target:()=>string,onSuccess:(result:unknown)=>void|Promise<void>,dirty:()=>boolean) {
 watch(() => JSON.stringify([account().accountId, account().permissionCodes]), () => {
  // Clear before the transport restores its next identity, without deleting other order requests on navigation.
  clearProcurementSnapshots(sessionStorage)
 }, { flush: 'sync' })
 return usePersistentOperation({account,target,onSuccess,dirty,idempotencyBodyField:'requestKey',readPermission:'stores.procurement.read',writePermission:'stores.procurement.manage',storage:pendingProcurement,recoveryPath:key=>`/stores/procurement/orders/${target()}/operations/${key}`,recoveryDecision,resultMatches,definitiveRejection:status=>[400,401,403,409,422].includes(status)})
}
