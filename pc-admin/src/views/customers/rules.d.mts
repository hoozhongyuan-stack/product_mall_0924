export interface RulesForm { grades: {id:string;amount:string}[]; earnYuan:string; earnPoints:string; deductPoints:string; deductYuan:string; maxPercent:string; validDays:string; refundValidDays:string; reason:string }
export interface RulesBody { expectedRevision:number; grades:{id:string;minimumSpendFen:number}[]; points:{earnUnitFen:number;earnPoints:number;deductPoints:number;deductFen:number;maxPercent:number;validDays:number;refundValidDays:number};reason:string }
export interface PendingRules { key:string;body:RulesBody }
export function yuanToFen(value:string):number|null
export function ruleError(form:RulesForm):string
export function rulesBody(form:RulesForm,revision:number):RulesBody
export function pendingRules(storage:Storage,accountId:string,value?:PendingRules|null):PendingRules|null
