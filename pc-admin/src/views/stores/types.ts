export interface Store {
 warehouseId?:string; id:string; name:string;contactName:string;contactPhone:string;address:string;city:string
 latitude:number|null;longitude:number|null;openingHours:string;enabled:boolean;acceptingOrders:boolean
 supportedModes:string[];deliveryRadiusMeters:number|null;deliveryFeeFen:number|null;revision:number
}
export interface StoreAccount {
 storeId:string;storeName:string;settlementReady:boolean;withdrawalReady:boolean;reason:string
 balance:{pendingFen:number|null;availableFen:number|null;frozenFen:number|null;paidFen:number|null}
 income:StoreIncome[];withdrawals:StoreWithdrawal[]
}
export const modes=[{value:'PICKUP',label:'到店自提'},{value:'DELIVERY',label:'配送到家'},{value:'EXPRESS',label:'快递发货'}]
export function modeLabel(value:string){return modes.find(item=>item.value===value)?.label||value}
export function amount(value:number|null|undefined){return value==null?'待配置':`¥${(value/100).toFixed(2)}`}
export function blankStore():Store{return {id:'',name:'',contactName:'',contactPhone:'',address:'',city:'',latitude:null,longitude:null,openingHours:'',enabled:true,acceptingOrders:true,supportedModes:['PICKUP'],deliveryRadiusMeters:null,deliveryFeeFen:null,revision:0}}

export interface StoreIncome {id:string;orderId:string;orderNo:string;paidFen:number;costFen:number|null;platformFen:number|null;freightFen:number;storeFen:number|null;status:string;reason:string;availableAt:string|null;settledAt:string|null}
export interface StoreWithdrawal {id:string;amountFen:number;payeeName:string;bankName:string;bankAccount:string;status:string;revision:number;reason:string;createdAt:string;paidAt:string|null;paymentReference:string}
export function incomeStatus(value:string){return ({PENDING:'待结算',SETTLED:'已结算',HELD:'待人工核对'} as Record<string,string>)[value]||value}
export function withdrawalStatus(value:string){return ({PENDING_REVIEW:'待审核',APPROVED_PENDING_PAYMENT:'审核通过，待线下发放',PAID:'已线下发放',REJECTED:'已驳回'} as Record<string,string>)[value]||value}
