export interface Carrier { code: string; name: string }
export interface FulfillmentLine {
  quantity: number
  fulfillment?: { redeemedQuantity?: number; voidedQuantity?: number; remainingQuantity?: number }
}
export declare function shipmentError(carrierCode: string, trackingNo: string, carriers: Carrier[]): string
export declare function redemptionError(quantity: string, available: number): string
export declare function remaining(line: FulfillmentLine): number
export declare function fulfillmentLabel(status: string | undefined): string
export declare function shipmentEligible(order: { status: string; shipEligible?: boolean; fulfillmentStatus?: string; fulfillment?: { shipStatus?: string }; items?: { fulfillmentKind: string; fulfillment?: { status: string } }[] }): boolean
export interface ShippingQuantityLine{quantity:number;fulfillmentKind?:string;fulfillment?:{refundedQuantity?:number}}
export function shippingQuantity(line:ShippingQuantityLine):{purchased:number;refunded:number;remaining:number}|null
export function shipmentQuantityError(lines?:ShippingQuantityLine[]):string
