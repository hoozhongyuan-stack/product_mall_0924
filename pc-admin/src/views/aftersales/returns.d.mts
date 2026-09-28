export interface AcceptanceForm { mode:string; receivedQuantity:number; salableQuantity:number; refundQuantity:number; amount:string; reason:string }
export function acceptanceError(form: AcceptanceForm, quantity:number, allowZero?:boolean): string
export function acceptanceBody(form: AcceptanceForm, revision:number): Record<string, unknown>
export function wechatRefundLabel(status:string | null | undefined): string
export interface PendingAcceptance { key:string; body:Record<string, unknown> }
export function pendingAcceptance(storage:Storage | {getItem:(key:string)=>string|null;setItem:(key:string,value:string)=>unknown;removeItem:(key:string)=>unknown},accountId:string,caseId:string,value?:PendingAcceptance|null): PendingAcceptance|null
export function wechatFailureLabel(code:string | null | undefined): string
