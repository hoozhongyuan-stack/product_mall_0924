export interface Page<T>{items:T[];pagination:{page:number;pageSize:number;total:number}}
export interface Campaign {id:string;code:string;title:string;kind:'FULL_REDUCTION'|'CASH';minGoodsFen:number;discountFen:number;productIds:string[];productNames:{id:string;name:string}[];redeemEligible:boolean;validFrom:string;validUntil:string;status:'DRAFT'|'PUBLISHED'|'LEGACY';revision:number;totalQuantity:number;issuedQuantity:number;remainingQuantity:number;selfClaimLimit:number;claimMode:'SELF'|'ADMIN'|'BOTH';issuanceEnabled:boolean}
export interface Issuance {id:string;memberId:string;memberLabel:string;kind:'SELF'|'ADMIN';quantity:number;reason:string;actorLabel:string;createdAt:string}
export const money=(v:number)=>`¥${(v/100).toFixed(2)}`
export const date=(v:string)=>new Date(v).toLocaleString('zh-CN',{hour12:false})
export const status=(v:string)=>({DRAFT:'草稿',PUBLISHED:'已发布',LEGACY:'历史活动'}[v]||v)
export const mode=(v:string)=>({SELF:'用户领取',ADMIN:'后台发放',BOTH:'领取与发放'}[v]||v)
