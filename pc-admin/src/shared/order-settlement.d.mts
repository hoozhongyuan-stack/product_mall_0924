export interface SettlementOrder{orderKind?:string;paymentMethod?:string;exchangePoints?:number;payableFen?:number}
export function isPointsOrder(order?:SettlementOrder|null):boolean
export function orderSettlementValue(order:SettlementOrder):string
export function paymentMethodLabel(order:SettlementOrder):string
