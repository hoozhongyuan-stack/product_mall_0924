export interface Region {code:string;name:string;level:'province'|'city'|'district'}
export interface Point {latitude:number;longitude:number}
export interface Template {id:string;name:string;mode:'FIXED'|'PIECE'|'WEIGHT';firstFeeFen:number;firstUnits:number;extraFeeFen:number;extraUnits:number;freeThresholdFen:number|null;regions:Region[];blockedRegions:Region[];productIds:string[];enabled:boolean}
export interface Zone {id:string;storeId:string;name:string;kind:'CIRCLE'|'POLYGON'|'EXCLUDE';center:Point|null;radiusMeters:number|null;polygon:Point[];priority:number;feeMode:'FIXED'|'DISTANCE';feeFen:number;firstMeters:number;extraMeters:number;extraFeeFen:number;minimumFen:number;freeThresholdFen:number|null;startTime:string;endTime:string;enabled:boolean}
export interface Campaign {enabled:boolean;name:string;startsAt:string;endsAt:string;minimumFen:number;storeIds:string[];deliveryModes:string[]}
export interface ShippingConfig {revision:number;templates:Template[];zones:Zone[];campaign:Campaign;skuWeights:{skuId:string;weightGrams:number}[]}
export interface Target {productId:string;productName:string;skus:{skuId:string;skuCode:string;specLabel:string;saleUnit:string;weightGrams:number|null;unitVersionId:string|null;weightConfigured:boolean}[]}
export const money=(fen:number|null)=>fen===null?'未设置':`¥${(fen/100).toFixed(2)}`
export const blankTemplate=():Template=>({id:'',name:'',mode:'FIXED',firstFeeFen:800,firstUnits:1,extraFeeFen:200,extraUnits:1,freeThresholdFen:19900,regions:[],blockedRegions:[],productIds:[],enabled:true})
export const blankZone=(storeId:string):Zone=>({id:'',storeId,name:'',kind:'CIRCLE',center:null,radiusMeters:1800,polygon:[],priority:10,feeMode:'FIXED',feeFen:600,firstMeters:3000,extraMeters:1000,extraFeeFen:200,minimumFen:3900,freeThresholdFen:9900,startTime:'09:00',endTime:'23:00',enabled:true})
