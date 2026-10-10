import type {OrderBenefits} from '../../shared/benefits'
export interface AfterSaleEvent { action: string; status: string; reason: string; occurredAt: string; actorName?: string }
export interface OfflineRefund {
  reconciliationId: string; confirmationObjectId: string; confirmationRevision: number
  preparedById: string; preparedByName: string; amountFen: number; merchantAccountId: string
  externalRefundNo: string; refundedAt: string; refundMethod: string; proofReference: string; note: string; createdAt?: string
  authorizedAt: string | null; outcome: string; canConfirm: boolean; refundNo?: string; originalTradeNo?: string
  authorizedByName?: string; anomalyReason?: string
}
export interface ReturnAcceptance {
 mode:string; receivedQuantity:number; salableQuantity:number; damagedQuantity:number; refundQuantity:number
 refundAmountFen:number; maxRefundAmountFen:number; refundPoints?:number; reason:string; acceptedAt:string
}
export interface WechatRefund {
 available:boolean; status:string | null; refundNo?:string; canDispatch:boolean; canQuery:boolean; failureCode?:string
}
export interface AfterSaleCase {
  storeNotes?: {id:string;kind:string;note:string;receivedQuantity:number;salableQuantity:number;occurredAt:string;platformReviewRequired:boolean}[]
  orderKind?:'CASH'|'POINTS';exchangePoints?:number;requestedRefundPoints?:number;pointsToReturn?:number;effectiveRefundPoints?:number;refundablePoints?:number;returnedPoints?:number
  caseId: string; orderId: string; orderNo: string; lineId: string; title: string; kind: string; redemptionScope: string
  quantity: number; amountFen: number; reason: string; status: string; revision: number; createdAt: string; updatedAt: string
  memberName?: string; paymentMethod: string; events: AfterSaleEvent[]; eventCount?: number
  returnShipment?: {carrierName:string;trackingNo:string;revision:number;submittedAt:string} | null
  returnAcceptance?: ReturnAcceptance | null; effectiveRefundQuantity?:number; effectiveRefundAmountFen?:number
  orderBenefits?:OrderBenefits | null
  goodsRefundAmountFen?:number; shippingRefundAmountFen?:number; totalRefundAmountFen?:number; canSettleBenefits?:boolean
  wechatRefund?:WechatRefund | null; refundStatus?:string | null
  offlineRefund?: OfflineRefund | null; refundSource?: { merchantAccountId: string; originalTradeNo: string; amountFen: number } | null
}
export interface AfterSaleList { items: AfterSaleCase[]; total: number; page: number; pageSize: number }
export const money = (fen: number) => `¥${(fen / 100).toFixed(2)}`
export const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
