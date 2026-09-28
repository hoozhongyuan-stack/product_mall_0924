export interface Page<T>{items:T[];pagination:{page:number;pageSize:number;total:number}}
export interface ExchangeSku {id:string;skuCode:string;productId:string;productName:string;specs:{name:string;value:string}[];saleUnit:string;fulfillmentKind:'SHIP'|'REDEEM';redeemValidUntil:string|null;catalogOnSale:boolean;existingOfferId:string|null}
export interface Offer {id:string;skuId:string;productId:string;productName:string;skuCode:string;specs:{name:string;value:string}[];saleUnit:string;fulfillmentKind:'SHIP'|'REDEEM';redeemValidUntil:string|null;pointsPrice:number;status:'DRAFT'|'ON_SALE'|'OFF_SALE';revision:number;createdAt:string;imageUrl?:string|null;availableQuantity?:number}
export const offerStatus=(v:string)=>({DRAFT:'草稿',ON_SALE:'兑换开启',OFF_SALE:'兑换暂停'}[v]||v)
export const specs=(v:{name:string;value:string}[])=>v.map(s=>`${s.name}：${s.value}`).join(' · ')||'默认规格'
