import {yuanToFen} from '../../shared/money.mjs'
export {yuanToFen} from '../../shared/money.mjs'
export function ruleError(f) {
 const amounts=f.grades.map(g=>yuanToFen(g.amount))
 if(amounts.some(a=>a===null)||amounts[0]!==0)return '首个等级门槛须为 0，金额最多两位小数。'
 if(amounts.some((a,i)=>i>0&&a<=amounts[i-1]))return '等级门槛须按等级严格递增。'
 for(const k of ['earnPoints','deductPoints','maxPercent','validDays','refundValidDays']){
  const max=k==='maxPercent'?100:['validDays','refundValidDays'].includes(k)?3650:1000000
  if(!/^\d+$/.test(f[k])||Number(f[k])<1||Number(f[k])>max)return `积分与天数须为范围内正整数（${k==='maxPercent'?'上限 100':max}）。`
 }
 if([f.earnYuan,f.deductYuan].some(v=>{const a=yuanToFen(v);return a===null||a<1||a>1000000}))return '消费与抵扣金额须在 0.01 至 10000 元之间，最多两位小数。'
 if(!f.reason.trim()||f.reason.trim().length>200)return '请填写 1 至 200 字修改依据。'
 return ''
}
export function rulesBody(f,revision){return {expectedRevision:revision,grades:f.grades.map(g=>({id:g.id,minimumSpendFen:yuanToFen(g.amount)})),points:{earnUnitFen:yuanToFen(f.earnYuan),earnPoints:Number(f.earnPoints),deductPoints:Number(f.deductPoints),deductFen:yuanToFen(f.deductYuan),maxPercent:Number(f.maxPercent),validDays:Number(f.validDays),refundValidDays:Number(f.refundValidDays)},reason:f.reason.trim()}}
export function pendingRules(storage,accountId,value=undefined){const key=`mall:member-rules:${accountId}`;if(value===null){storage.removeItem(key);return null}if(value!==undefined){storage.setItem(key,JSON.stringify(value));return value}const raw=storage.getItem(key);if(!raw)return null;try{const p=JSON.parse(raw);if(p&&typeof p.key==='string'&&p.body&&Number.isSafeInteger(p.body.expectedRevision)&&Array.isArray(p.body.grades)&&p.body.points)return p}catch{}storage.removeItem(key);return null}
