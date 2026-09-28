export interface Amounts { goods:number;shipping:number;total:number;zeroCash:boolean }
export function refundAmounts(row:{amountFen:number;effectiveRefundAmountFen?:number;goodsRefundAmountFen?:number;shippingRefundAmountFen?:number;totalRefundAmountFen?:number}):Amounts
export interface BenefitPending { key:string;body:{expectedRevision:number} }
export function pendingBenefitSettlement(storage:Storage,accountId:string,caseId:string,value?:BenefitPending|null):BenefitPending|null
