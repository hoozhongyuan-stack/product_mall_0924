import type { PendingOperation } from '../../../shared/persistent-operation'
export const statuses:Record<string,string>
export const actionLabels:Record<string,string>
export const recordLabels:Record<string,string>
export function availableActions(order:{status:string;unitReviewRequired?:boolean}):string[]
export function actionPermission(action:string|undefined):string
export function actionBody(action:string,form:Record<string,unknown>,revision:number):{error?:string;body:Record<string,unknown>}
export function pendingProcurement(storage:Storage,actor:string,target:string,value?:PendingOperation|null,now?:number):PendingOperation|null
export function clearProcurementSnapshots(storage:Storage):boolean
export function resultMatches(p:PendingOperation,result:unknown):boolean
export function recoveryDecision(result:unknown,p:PendingOperation):'COMPLETED'|'RETRY'|'UNKNOWN'
export function proofUrl(proof:unknown):string
