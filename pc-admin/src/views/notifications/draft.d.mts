export interface DraftFields {
  draftAppId: string
  draftTemplateId: string
}
export type EventType = 'ORDER_PAID' | 'ORDER_SHIPPED' | 'REFUND_SUCCEEDED'
export type DraftStatus = 'UNBOUND' | 'INCOMPLETE' | 'DRAFT_UNVERIFIED'
export interface TemplateRow extends DraftFields {
  eventType: EventType
  revision: number
  status: DraftStatus
  enabled: false
}
export const EVENT_TYPES: readonly EventType[]
export function parseTemplateRow(value: unknown): TemplateRow
export function parseTemplateSettings(value: unknown): TemplateRow[]
export function draftIssue(form: DraftFields): string
export function draftBody(form: DraftFields, expectedRevision: number): DraftFields & { expectedRevision: number }
export function reconcileDraft(current: DraftFields, submitted: DraftFields, server: TemplateRow): {
  editedDuringSave: boolean
  form: DraftFields
}
