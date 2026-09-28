const {money}=require('./catalog')
function presentBenefits(row){
 if(row==null)return null
 if(row.status==='SKIPPED')return {status:'SKIPPED',statusLabel:'历史规则缺失，需平台核查',scopeCopy:'历史订单权益规则缺失，未自动结算。',hasAmounts:false}
 if(!['PENDING','SETTLED','SKIPPED'].includes(row.status) || !['effectiveSpendFen','earnedPoints','returnedPoints','clawedBackPoints'].every(key=>Number.isSafeInteger(row[key])&&row[key]>=0) || typeof row.couponRestored!=='boolean')throw new Error('订单权益结果不完整，请重新加载。')
 return {...row,hasAmounts:true,effectiveSpendLabel:money(row.effectiveSpendFen),scopeCopy:'订单累计权益结果，不代表单笔售后的变动金额。',statusLabel:row.status==='SETTLED'?'已结算':row.status==='SKIPPED'?'历史规则缺失，需平台核查':'待履约及售后结束后结算'}
}
module.exports={presentBenefits}
