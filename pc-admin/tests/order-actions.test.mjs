import test from 'node:test'
import assert from 'node:assert/strict'
import { orderActions, localActionBody } from '../src/views/orders/order-actions.mjs'
const paid={orderId:'o',status:'PAID',paymentMethod:'OFFLINE',payableFen:100,deliveryMode:'DELIVERY',fulfillmentStatus:'WAITING_PREPARATION',items:[{fulfillmentKind:'SHIP'}]}
test('actions follow payment method, state and current permissions',()=>{
 assert.deepEqual(orderActions({...paid,status:'PENDING_PAYMENT'},['order.read']),[])
 assert.deepEqual(orderActions({...paid,status:'PENDING_PAYMENT'},['payment.offline.confirm']),['receipt'])
 assert.deepEqual(orderActions({...paid,status:'PENDING_PAYMENT',paymentMethod:'WECHAT'},['payment.offline.confirm']),['query'])
 assert.deepEqual(orderActions({...paid,status:'PAID',payableFen:0},['payment.offline.confirm']),[])
 assert.deepEqual(orderActions({...paid,status:'CLOSED'},['payment.offline.confirm']),['anomaly'])
 assert.deepEqual(orderActions({...paid,status:'PAID',orderKind:'POINTS',paymentMethod:'POINTS'},['payment.offline.confirm']),[])
})
test('local delivery and pickup retain separate state transitions',()=>{
 for(const [state,action] of [['WAITING_PREPARATION','prepare'],['WAITING_DELIVERY','deliver'],['DELIVERING','delivered']])assert.deepEqual(orderActions({...paid,fulfillmentStatus:state},['fulfillment.ship']),[action])
 assert.deepEqual(orderActions({...paid,deliveryMode:'PICKUP',fulfillmentStatus:'WAITING_PICKUP'},['fulfillment.ship']),['pickup'])
 assert.deepEqual(orderActions({...paid,fulfillmentStatus:'AFTER_SALE'},['fulfillment.ship','aftersale.read']),['aftersale'])
 assert.deepEqual(orderActions({...paid,fulfillmentStatus:'COMPLETED'},['fulfillment.ship']),[])
})
test('shipment corrections and read-only tracking are available independently',()=>{
 const express={...paid,deliveryMode:'EXPRESS',fulfillmentStatus:'WAITING_SHIPMENT',shipEligible:true}
 assert.deepEqual(orderActions(express,['fulfillment.ship']),['ship'])
 assert.deepEqual(orderActions({...express,shipEligible:false,shipment:{trackingNo:'SF1'}},['order.read']),['tracking'])
 assert.deepEqual(orderActions({...express,shipEligible:false,shipment:{trackingNo:'SF1'}},['order.read','fulfillment.ship']),['tracking','correct'])
 assert.deepEqual(orderActions({...paid,items:[{fulfillmentKind:'REDEEM',fulfillment:{status:'READY',remainingQuantity:1}}]},['fulfillment.redeem']),['redeem'])
})
test('local forms use existing service body and evidence rather than arbitrary snapshots',()=>{
 assert.deepEqual(localActionBody('prepare',4,{note:'核对完成'}),{action:'PREPARE',expectedRevision:4,note:'核对完成'})
 assert.deepEqual(localActionBody('deliver',4,{person:'小王',phone:'13800000000',note:'餐厅前台'}),{action:'DISPATCH',expectedRevision:4,note:'配送人员：小王；联系电话：13800000000；餐厅前台'})
 assert.deepEqual(localActionBody('pickup',4,{pickupCode:'ABC',note:''}),{action:'COMPLETE',expectedRevision:4,note:'',pickupCode:'ABC'})
 assert.throws(()=>localActionBody('unknown',4,{}),/不支持/)
})
test('pending action recovery accepts only the original actor and domain path',async()=>{
 const {validatePending,pendingActions}=await import('../src/views/orders/order-actions.mjs')
 const p={actor:'a',orderId:'o',action:'ship',key:'12345678-1234-1234-1234-123456789abc',path:'/orders/o/shipment',phase:'write',body:{expectedRevision:1,carrierCode:'SF',trackingNo:'SF1'}}
 assert.equal(validatePending(p,'a','o','ship'),true)
 assert.equal(validatePending({...p,path:'/accounts'},'a','o','ship'),false)
 assert.equal(validatePending({...p,actor:'other'},'a','o','ship'),false)
 const storage={getItem:key=>key==='mall-order-action:a:o:ship'?JSON.stringify(p):null}
 assert.deepEqual(pendingActions('a','o',storage),['ship'])
 assert.deepEqual(pendingActions('other','o',storage),[])
 assert.deepEqual(pendingActions('a','o',{getItem(){throw Error('blocked')}}),[])
})

test('cached body cannot switch approved local action or unrelated order voucher',async()=>{
 const {validatePending}=await import('../src/views/orders/order-actions.mjs')
 const p={actor:'a',orderId:'o',action:'prepare',key:'12345678-1234-1234-1234-123456789abc',path:'/orders/o/local-fulfillment',phase:'write',body:{expectedRevision:1,action:'PREPARE',note:''}}
 assert.equal(validatePending(p,'a','o','prepare'),true)
 assert.equal(validatePending({...p,body:{...p.body,action:'COMPLETE'}},'a','o','prepare'),false)
 assert.equal(validatePending({...p,key:'bad'},'a','o','prepare'),false)
 const v={...p,action:'redeem',path:'/orders/o/redemptions/v1/use',body:{expectedRevision:1,quantity:1}}
 assert.equal(validatePending(v,'a','o','redeem'),true)
 assert.equal(validatePending({...v,path:'/orders/other/redemptions/v1/use'},'a','o','redeem'),false)
})

test('receipt recovery binds the server record to order, actor and frozen evidence',async()=>{
 const {receiptRecoveryMatches}=await import('../src/views/orders/order-actions.mjs')
 const body={merchantAccountId:'BANK',externalTradeNo:'T1',amountFen:100,paidAt:'2026-01-01T10:00:00Z',note:'已核对'}
 const pending={phase:'confirm',path:'/payments/offline-reconciliations/r1/confirm',actor:'a',body}
 const order={confirmations:[{...body,reconciliationId:'r1',actorId:'a'}]}
 assert.equal(receiptRecoveryMatches(order,pending),true)
 assert.equal(receiptRecoveryMatches({...order,confirmations:[{...order.confirmations[0],actorId:'other'}]},pending),false)
 assert.equal(receiptRecoveryMatches(order,{...pending,body:{...body,amountFen:200}}),false)
 assert.equal(receiptRecoveryMatches({confirmations:[]},pending),false)
 assert.equal(receiptRecoveryMatches(order,{...pending,path:'/payments/offline-reconciliations/r2/confirm'}),false)
})

test('bounded list summaries expose voucher and shipment operations without detail queries',()=>{
 const summary={status:'PAID',fulfillmentStatus:'WAITING_REDEMPTION',redeemEligible:true,hasShipment:false}
 assert.deepEqual(orderActions(summary,['fulfillment.redeem']),['redeem'])
 assert.deepEqual(orderActions({...summary,redeemEligible:false},['fulfillment.redeem']),[])
 assert.deepEqual(orderActions({...summary,redeemEligible:false,hasShipment:true},['order.read','fulfillment.ship']),['tracking','correct'])
})
