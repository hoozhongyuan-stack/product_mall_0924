export const EVENT_OPTIONS: ReadonlyArray<readonly [string, string]>
export const STATUS_OPTIONS: ReadonlyArray<readonly [string, string]>
export function eventLabel(value: string): string
export function statusLabel(value: string): string
export function reasonLabel(value: string): string
export function canRecover(task: { status: string; recoverable: boolean; attemptCount: number }, hasPermission: boolean): boolean
export function taskQuery(filters: { eventType: string; status: string; createdFrom: string; createdTo: string }, cursor?: string): string
