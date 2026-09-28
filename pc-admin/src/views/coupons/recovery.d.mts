import type {PendingCoupon} from './form.mjs'
export function resultMatches(pending:PendingCoupon,result:unknown):boolean
export function recoveryDecision(response:unknown,pending:PendingCoupon):'UNKNOWN'|'RETRY'|'COMPLETED'
export function definitiveRejection(status:number):boolean
