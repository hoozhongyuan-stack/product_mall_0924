import type {Order} from './types'
export type OrderAction='receipt'|'anomaly'|'query'|'prepare'|'deliver'|'delivered'|'pickup'|'ship'|'correct'|'tracking'|'redeem'|'aftersale'
export function orderActions(order:Order|null,permissions:string[]):OrderAction[]
export function localActionBody(action:string,revision:number,form:{note:string;person:string;phone:string;pickupCode:string}):Record<string,unknown>
export const actionLabels:Record<OrderAction,string>
export function actionPermission(action:OrderAction):string
export function validatePending(value:unknown,actor:string,orderId:string,action:OrderAction):boolean
export function pendingActions(actor:string,orderId:string,storage:Pick<Storage,'getItem'>):OrderAction[]

export function receiptRecoveryMatches(order:Order,pending:{path:string;actor:string;body:Record<string,unknown>}):boolean
