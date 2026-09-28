export interface Grade {id:string;code:string;name:string;rank:number;minimumSpendFen?:number}
export interface Points {settledPoints:number;frozenPoints:number;availablePoints:number;debtPoints:number;expiredPendingPoints:number;expiringPoints:number;nextExpiryAt:string|null}
export interface Member {id:string;grade:Grade;enabled:boolean;createdAt:string;effectiveSpendFen:number;gradeEffectiveAt:string|null;points:Points;ruleRevision?:number;gradePolicyRevision?:number}
export interface Page<T> {items:T[];pagination:{total:number;page:number;pageSize:number}}
export interface Rule {revision:number;grades:Grade[];points:{earnUnitFen:number;earnPoints:number;deductPoints:number;deductFen:number;maxPercent:number;validDays:number;refundValidDays:number}}
export interface PointEvent {id:string;kind:string;amount:number;balance?:number;orderId?:string|null;sourceRef:string;createdAt:string;expiresAt?:string|null;effect:string}
export interface Consumption {id:string;orderId:string;amountFen:number;balanceFen:number;gradeBefore:Grade;gradeAfter:Grade;sourceRef:string;createdAt:string}
export const money=(fen:number)=>`¥${(fen/100).toFixed(2)}`
export const date=(v?:string|null)=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—'
export function pointsKind(kind:string){return ({GRANT:'积分发放',EXPIRE:'积分到期',RESERVE:'订单冻结',RELEASE:'冻结释放',CONSUME:'订单抵扣',REFUND:'退款返还',RETURN:'退款返还',CLAWBACK:'消费奖励扣回',EARN:'消费奖励',EARN_REVERSE:'奖励扣回',REVOKE:'奖励扣回',ADJUST:'积分调整'})[kind]||kind}
