export type OrderBenefits = {status:'SKIPPED';reason?:string} | {
 status:'PENDING'|'SETTLED';effectiveSpendFen:number;earnedPoints:number;returnedPoints:number
 clawedBackPoints:number;couponRestored:boolean;policyRevision?:number
}
