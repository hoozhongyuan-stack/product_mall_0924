export interface PendingExchange {key:string;path:string;method:'POST'|'PUT';body:Record<string,unknown>;action:string}
export function offerError(skuId:string,price:string):string
export function offerBody(skuId:string,price:string,revision?:number):Record<string,unknown>
export function resultMatches(pending:PendingExchange,result:unknown):boolean
export function recoveryDecision(response:unknown,pending:PendingExchange):'UNKNOWN'|'RETRY'|'COMPLETED'
export function definitiveRejection(status:number):boolean
export function pendingExchange(storage:Storage,actor:string,target:string,value?:PendingExchange|null):PendingExchange|null
