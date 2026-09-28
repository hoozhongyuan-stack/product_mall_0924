const amount = value => Number.isSafeInteger(value) && value >= 0
export function refundAmounts(row) {
 const goods=row.goodsRefundAmountFen ?? row.effectiveRefundAmountFen ?? row.amountFen
 const shipping=row.shippingRefundAmountFen ?? 0
 const total=row.totalRefundAmountFen ?? goods
 if(!amount(goods)||!amount(shipping)||!amount(total)||total!==goods+shipping) throw new Error('服务端退款金额不完整，请刷新核查。')
 return {goods,shipping,total,zeroCash:total===0}
}
export function pendingBenefitSettlement(storage,accountId,caseId,value=undefined) {
 const name=`mall:benefit-settlement:${accountId}:${caseId}`
 if(value===null){storage.removeItem(name);return null}
 if(value!==undefined){storage.setItem(name,JSON.stringify(value));return value}
 const raw=storage.getItem(name);if(!raw)return null
 try{
  const parsed=JSON.parse(raw)
  if(parsed && /^[a-f0-9-]{36}$/i.test(parsed.key) && parsed.body && Object.keys(parsed.body).length===1 && Number.isSafeInteger(parsed.body.expectedRevision) && parsed.body.expectedRevision>0)return parsed
 }catch{}
 storage.removeItem(name);return null
}
