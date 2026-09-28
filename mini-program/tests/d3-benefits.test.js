const test=require('node:test'), assert=require('node:assert/strict')
test('order cumulative benefits are clearly separate from a single refund',()=>{
 const {presentBenefits}=require('../lib/benefits')
 const result=presentBenefits({status:'SETTLED',effectiveSpendFen:1200,earnedPoints:12,returnedPoints:100,clawedBackPoints:1,couponRestored:false,policyRevision:2})
 assert.equal(result.effectiveSpendLabel,'¥12');assert.match(result.scopeCopy,/订单累计/)
 assert.match(result.statusLabel,/结算/);assert.equal(presentBenefits(null),null)
 assert.equal(presentBenefits({status:'SKIPPED',reason:'MISSING_HISTORICAL_SNAPSHOT'}).hasAmounts,false)
 assert.throws(()=>presentBenefits({status:'SETTLED',effectiveSpendFen:-1}),/权益/)
})
test('paid zero order uses settlement wording without claiming actual receipt',()=>{
 const result=require('../lib/orders').present({orderId:'zero',status:'PAID',payableFen:0,goodsTotalFen:100,shippingFeeFen:0,couponDiscountFen:100,pointsDiscountFen:0,paymentMethod:'OFFLINE',items:[]})
 assert.match(result.paymentLabel,/零现金/);assert.match(result.resultTitle,/零现金/)
 assert.doesNotMatch(result.resultTitle,/收款/)
})
