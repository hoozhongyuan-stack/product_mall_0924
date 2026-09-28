import {yuanToFen} from '../../shared/money.mjs'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function campaignError(f){
 if(!/^[A-Za-z0-9_-]{1,64}$/.test(f.code.trim())||!f.title.trim()||f.title.trim().length>100)return '填写 1 至 64 位字母、数字、下划线或短横线编码，以及 1 至 100 字标题。'
 if(!['FULL_REDUCTION','CASH'].includes(f.kind)||!['SELF','ADMIN','BOTH'].includes(f.claimMode))return '请选择券类型和领取方式。'
 const discount=yuanToFen(f.discount),minimum=yuanToFen(f.minimum)
 if(discount===null||discount<1)return '优惠金额须大于 0，最多两位小数。'
 if(f.kind==='FULL_REDUCTION'&&(minimum===null||minimum<1))return '满减门槛须大于 0，最多两位小数。'
 if(!Number.isFinite(Date.parse(f.validFrom))||!Number.isFinite(Date.parse(f.validUntil))||Date.parse(f.validUntil)<=Date.parse(f.validFrom))return '结束时间须晚于开始时间。'
 if(!/^\d+$/.test(f.totalQuantity)||Number(f.totalQuantity)<1||Number(f.totalQuantity)>1000000)return '发放总量须为 1 至 1000000 的整数。'
 if(!/^\d+$/.test(f.selfClaimLimit)||Number(f.selfClaimLimit)<1||Number(f.selfClaimLimit)>Math.min(Number(f.totalQuantity),100))return '每人主动领取上限须为正整数，且不超过总量。'
 if(f.productIds.length>100||f.productIds.some(id=>!uuid.test(id))||new Set(f.productIds).size!==f.productIds.length)return '最多选择 100 个不同商品。'
 return ''
}
export function campaignBody(f,revision){return {...(revision===undefined?{}:{expectedRevision:revision}),code:f.code.trim(),title:f.title.trim(),kind:f.kind,minGoodsFen:f.kind==='CASH'?0:yuanToFen(f.minimum),discountFen:yuanToFen(f.discount),productIds:[...f.productIds],redeemEligible:f.redeemEligible,validFrom:new Date(f.validFrom).toISOString(),validUntil:new Date(f.validUntil).toISOString(),totalQuantity:Number(f.totalQuantity),selfClaimLimit:Number(f.selfClaimLimit),claimMode:f.claimMode,issuanceEnabled:f.issuanceEnabled}}
export function issueError(memberId,quantity,reason,canRepeat){if(!uuid.test(memberId))return '请从查询结果选择有效会员。';if(!/^\d+$/.test(quantity)||Number(quantity)<1||Number(quantity)>100)return '发放数量须为 1 至 100 的整数。';if(Number(quantity)>1&&!canRepeat)return '一次发放多张需要重复发券权限。';if(Number(quantity)>1&&!reason.trim())return '重复发放须填写原因。';if(reason.length>200)return '发放原因最多 200 字。';return ''}
export function pendingCoupon(storage,actor,target,value=undefined){const key=`mall:coupon:${actor}:${target}`;if(value===null){storage.removeItem(key);return null}if(value!==undefined){storage.setItem(key,JSON.stringify(value));return value}const raw=storage.getItem(key);if(!raw)return null;try{const p=JSON.parse(raw);if(uuid.test(p.key)&&typeof p.path==='string'&&/^\/coupon-campaigns(?:\/[0-9a-f-]{36}(?:\/(?:publish|distribution|issuances))?)?$/.test(p.path)&&['POST','PUT'].includes(p.method)&&p.body&&typeof p.body==='object'&&!Array.isArray(p.body)&&!('password' in p)&&!('password' in p.body)&&['','coupon.publish','coupon.issue'].includes(p.action)&&(!('expectedRevision' in p.body)||Number.isSafeInteger(p.body.expectedRevision)))return p}catch{}storage.removeItem(key);return null}
