export interface TransferForm { externalRefundNo: string; amount: string; refundedAt: string; refundMethod: string; proofReference: string; note: string; verified: boolean }
export function reviewError(decision: boolean | null, reason: string): string
export function transferError(value: TransferForm, amountFen: number): string
export function canConfirmTransfer(row: { canConfirm: boolean; preparedById: string; outcome: string } | null | undefined, accountId: string, codes: string[]): boolean
export function caseStatus(value: string): string
export function refundOutcome(value: string): string
export function refundMethodLabel(value: string): string
