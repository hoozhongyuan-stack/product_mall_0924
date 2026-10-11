export interface Restaurant {id:string;name:string;address:string;contactName:string;contactPhone:string;latitude:number|null;longitude:number|null;enabled:boolean;revision:number;fulfillmentStore:{id:string;name:string}|null;tableCount:number}
export interface DiningTable {id:string;number:string;enabled:boolean;revision:number}
export interface DiningStaff {id:string;memberId:string;memberNo:string;name:string;enabled:boolean;permissions:string[];revision:number}
export interface MemberChoice {id:string;name:string;memberNo:string}
export interface ProductRatio {id?:string;productId:string;name:string;commissionBps:number|null;revision:number}
export interface PageResult<T> {items:T[];total:number}
export const base='/dining-alliance'
export const money=(fen:number)=>`¥${(fen/100).toFixed(2)}`
export function blankRestaurant():Restaurant{return {id:'',name:'',address:'',contactName:'',contactPhone:'',latitude:null,longitude:null,enabled:true,revision:0,fulfillmentStore:null,tableCount:0}}
export function reasonText(reason:unknown){return reason instanceof Error?reason.message:'操作失败，请重试。'}
export interface DiningIncome {orderId:string;orderNo:string;commissionFen:number;status:'ESTIMATED'|'PENDING'|'HELD'|'AVAILABLE';bookedAt?:string|null;reason:string;availableAt:string|null;tableNumber:string}
export interface DiningWithdrawal {id:string;amountFen:number;status:'PENDING'|'APPROVED'|'REJECTED';reason:string;revision:number;createdAt:string}
export interface DiningAccount {fundingSource?:'FULFILLMENT_STORE_PROFIT';fundingLabel?:string;availableFen:number;pendingFen:number;heldFen:number;totalCommissionFen:number;frozenFen:number;incomes:DiningIncome[];withdrawals:DiningWithdrawal[];payoutEnabled:boolean}
export const incomeLabel=(status:string)=>({ESTIMATED:'预计佣金',PENDING:'已入账，待过售后期',HELD:'待核对',AVAILABLE:'已满足提现条件'}[status]||status)
export const withdrawalLabel=(status:string)=>({PENDING:'待审核',APPROVED:'审核通过，待打款',REJECTED:'已驳回'}[status]||status)
export interface DiningOrder {id:string;orderNo:string;status:string;payableFen:number;createdAt:string;restaurantId:string;restaurantName:string;tableNumber:string;address:string;fulfillmentStore:{id:string;name:string};commission:DiningIncome;delivery:{status?:string;mode?:string}|null;lines:{id:string;productName:string;skuCode:string;quantity:number;saleUnit:string;payableFen:number}[]}
