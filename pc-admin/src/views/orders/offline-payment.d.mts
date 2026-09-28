export interface ReconciliationForm { merchantAccountId: string; externalTradeNo: string; amount: string; paidAt: string; note: string; verified: boolean }
export function amountFen(value: string): number | null
export function reconciliationError(value: ReconciliationForm, payableFen: number): string
export function paymentStatus(order: { status: string; paymentReviewStatus?: string; orderKind?:string; paymentMethod?:string }): string
export function sameEvidence(left: Record<string, unknown>, right: Record<string, unknown>): boolean
