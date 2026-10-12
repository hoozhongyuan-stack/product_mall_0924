// Present frozen server facts. Never infer eligibility or calculate a delivery fee locally.
function shippingPresentation(source) {
 const snapshot=source&&source.shippingSnapshot
 if(!snapshot)return {shippingAllowed:true,shippingMessage:'',shippingFreeSource:'NONE'}
 const amounts=['costFen','customerFeeFen','warehouseBearsFen','platformBearsFen']
 const valid=amounts.every(key=>Number.isSafeInteger(snapshot[key])&&snapshot[key]>=0)&&
  snapshot.customerFeeFen===source.shippingFeeFen&&
  snapshot.costFen===snapshot.customerFeeFen+snapshot.warehouseBearsFen+snapshot.platformBearsFen&&
  ['NONE','WAREHOUSE','PLATFORM','MIXED'].includes(snapshot.freeSource)
 if(!valid)return {shippingAllowed:false,shippingMessage:'运费资料不完整，请重新核对报价。',shippingFreeSource:'NONE'}
 const shippingAllowed=snapshot.eligible===true&&snapshot.pending!==true
 const shippingMessage=!shippingAllowed?(snapshot.message||'当前地址暂不可配送，请重新核对。'):
  snapshot.freeSource==='PLATFORM'?'平台活动包邮':snapshot.freeSource==='WAREHOUSE'?'前置仓包邮':snapshot.freeSource==='MIXED'?'部分商品包邮':snapshot.message||''
 return {shippingAllowed,shippingMessage,shippingFreeSource:snapshot.freeSource}
}
module.exports={shippingPresentation}
