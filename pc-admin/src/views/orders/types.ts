import type {OrderBenefits} from '../../shared/benefits'
export interface PaymentPolicy {
  offlineEnabled?: boolean
  wechatEnabled?: boolean
  wechatConfigurationStatus?: 'NOT_CONFIGURED' | 'PENDING_VERIFICATION'
  instructions: string
  merchantAccountId: string
  offlineTimeoutMinutes: number
  wechatTimeoutMinutes: number
  revision: number
  configured: boolean
  availablePaymentMethods: string[]
}
export interface OrderLine {
  pointsUnitPrice?:number; pointsTotal?:number
  orderLineId: string; name: string; skuCode: string; quantity: number; saleUnit: string
  fulfillmentKind: string; goodsAmountFen: number; payableFen: number
  fulfillment?: { status: string; refundedQuantity?: number; redeemedQuantity?: number; remainingQuantity?: number; voidedQuantity?: number; validUntil?: string | null; events?: RedemptionRecord[] }
}
export interface Carrier { code: string; name: string; enabled: boolean; revision: number }
export interface Shipment {
  shipmentId: string; warehouseId?: string; warehouseName?: string; carrierCode: string; carrierName: string; trackingNo: string
  shippedAt: string; shippedByName?: string; confirmedAt?: string | null
  corrections?: { oldCarrierName: string; oldTrackingNo: string; newCarrierName: string; newTrackingNo: string; reason: string; correctedAt: string; actorName?: string }[]
  correctionCount?: number
}
export interface RedemptionRecord {
  eventId: string; kind: string; quantity: number; remainingQuantity: number; occurredAt: string; actorName?: string
  reversedAt?: string | null; reason?: string | null
}
export interface RedemptionLookup {
  voucherId: string; orderId: string; orderNo: string; pointsUnitPrice?:number; pointsTotal?:number
  orderLineId: string; name: string; skuCode?: string
  quantity: number; redeemedQuantity: number; voidedQuantity: number; remainingQuantity: number; validUntil: string | null
  status: string; revision: number; events?: RedemptionRecord[]; eventCount?: number
}
export interface PaymentReport { reportId: string; note: string; reportedAt: string }
export interface Receipt {
  receiptId: string; merchantAccountId: string; externalTradeNo: string; amountFen: number
  paidAt: string; appliedAt: string | null; anomaly?: { reason: string; status: string } | null
}
export interface Reconciliation {
  reconciliationId: string; confirmationObjectId: string; confirmationRevision: number
  merchantAccountId: string; externalTradeNo: string; amountFen: number; paidAt: string; note: string
  actorId: string; actorName?: string; createdAt?: string; authorizedAt?: string | null; receiptId?: string | null; outcome?: string
}
export interface Order {
  orderKind?:'CASH'|'POINTS';exchangePoints?:number
  orderBenefits?:OrderBenefits | null
  orderId: string; orderNo: string; revision: number; status: 'PENDING_PAYMENT' | 'PAID' | 'CLOSED'; fulfillmentStatus?: string; shipEligible?: boolean
  fulfillment?: { shipStatus?: string }; shipment?: Shipment | null
  paymentMethod: string; paymentReviewStatus: string; paymentInstructions: { instructions: string; merchantAccountId: string; revision: number; timeoutMinutes: number } | null
  goodsTotalFen: number; shippingFeeFen: number; couponDiscountFen: number; pointsDiscountFen: number; payableFen: number
  createdAt: string; expiresAt: string; paidAt: string | null; closedAt: string | null; closeReason: string | null
  address: { recipientName: string; phone: string; province: string; city: string; district: string; detail: string } | null
  items: OrderLine[]; paymentReports?: PaymentReport[]; receipts?: Receipt[]; confirmations?: Reconciliation[]; receiptCount?: number; confirmationCount?: number
}
export interface OrderList { items: Order[]; total: number; page: number; pageSize: number }
export const money = (fen: number) => `¥${(fen / 100).toFixed(2)}`
export const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

export const outcomeLabel = (value: string | undefined) => ({ DRAFT: '待授权确认', AUTHORIZED: '已授权，待处理', FAILED: '处理失败，可重试', PENDING: '结算待处理', PAID: '已确认收款', ANOMALY: '到账异常' }[value || ''] || value || '待处理')
export const anomalyLabel = (value: string) => ({ UNKNOWN_ORDER: '无法匹配订单', METHOD_MISMATCH: '支付方式不符', AMOUNT_MISMATCH: '到账金额不符', CLOSED_ORDER: '订单关闭后到账', ALREADY_PAID: '订单已付款，额外到账', SETTLEMENT_FAILED: '结算失败', IDENTIFIER_CONFLICT: '流水身份冲突' }[value] || value)
