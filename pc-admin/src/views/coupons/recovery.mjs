const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const object=v=>!!v&&typeof v==='object'&&!Array.isArray(v)
const validDate=v=>typeof v==='string'&&Number.isFinite(Date.parse(v))
function campaignShape(r){return uuid.test(r.id)&&Number.isSafeInteger(r.revision)&&r.revision>0&&['DRAFT','PUBLISHED'].includes(r.status)&&typeof r.code==='string'&&typeof r.title==='string'&&['CASH','FULL_REDUCTION'].includes(r.kind)&&['SELF','ADMIN','BOTH'].includes(r.claimMode)&&['minGoodsFen','discountFen','totalQuantity','selfClaimLimit','issuedQuantity','remainingQuantity'].every(k=>Number.isSafeInteger(r[k])&&r[k]>=0)&&typeof r.redeemEligible==='boolean'&&typeof r.issuanceEnabled==='boolean'&&Array.isArray(r.productIds)&&r.productIds.every(id=>uuid.test(id))&&Array.isArray(r.productNames)&&r.productNames.every(p=>object(p)&&uuid.test(p.id)&&typeof p.name==='string')&&validDate(r.validFrom)&&validDate(r.validUntil)}
export function resultMatches(p,r){
 if(!object(p)||!object(p.body)||!object(r))return false
 const match=/^\/coupon-campaigns(?:\/([0-9a-f-]{36})(?:\/(publish|distribution|issuances))?)?$/.exec(p.path)
 if(!match)return false
 const [,id,operation]=match,b=p.body
 if(operation==='issuances')return p.action==='coupon.issue'&&r.campaignId===id&&r.memberId===b.memberId&&r.quantity===b.quantity&&Array.isArray(r.couponIds)&&r.couponIds.length===b.quantity&&r.couponIds.every(v=>uuid.test(v))&&new Set(r.couponIds).size===r.couponIds.length
 if(!campaignShape(r)||id&&r.id!==id||r.revision!==(id?b.expectedRevision+1:1))return false
 if(operation==='publish')return p.action==='coupon.publish'&&r.status==='PUBLISHED'
 if(operation==='distribution')return p.action==='coupon.publish'&&r.status==='PUBLISHED'&&r.issuanceEnabled===b.issuanceEnabled
 if(p.action!==''||r.status!=='DRAFT')return false
 return Object.entries(b).every(([k,v])=>k==='expectedRevision'||(k==='validFrom'||k==='validUntil'?Date.parse(r[k])===Date.parse(v):k==='productIds'?Array.isArray(v)&&r[k].length===v.length&&r[k].every((id,i)=>id===v[i]):r[k]===v))
}
export function recoveryDecision(response,pending){if(!object(response))return 'UNKNOWN';if(response.status==='NOT_FOUND')return 'RETRY';if(response.status==='COMPLETED'&&resultMatches(pending,response.result))return 'COMPLETED';return 'UNKNOWN'}
export function definitiveRejection(status){return [400,403,404,405,409,410,413,415,422].includes(status)}
