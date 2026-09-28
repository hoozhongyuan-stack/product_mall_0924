import test from 'node:test'
import assert from 'node:assert/strict'
import { refundAmounts, pendingBenefitSettlement } from '../src/views/aftersales/settlement.mjs'
const key='fdb5c079-d9f4-49b5-835d-663c905c1d4d'
test('approved goods and independent shipping use server total for transfer',()=>{
 assert.deepEqual(refundAmounts({amountFen:2000,effectiveRefundAmountFen:1000,goodsRefundAmountFen:1000,shippingRefundAmountFen:1000,totalRefundAmountFen:2000}),{goods:1000,shipping:1000,total:2000,zeroCash:false})
 assert.equal(refundAmounts({amountFen:101}).total,101)
 assert.throws(()=>refundAmounts({amountFen:100,goodsRefundAmountFen:100,shippingRefundAmountFen:100,totalRefundAmountFen:100}),/金额/)
 assert.throws(()=>refundAmounts({amountFen:100,totalRefundAmountFen:-1}),/金额/)
})
test('zero cash is a valid settlement and never a positive cash transfer',()=>{
 assert.deepEqual(refundAmounts({amountFen:0,goodsRefundAmountFen:0,shippingRefundAmountFen:0,totalRefundAmountFen:0}),{goods:0,shipping:0,total:0,zeroCash:true})
})
test('benefit settlement retry is scoped to account and case and retains exact key and revision',()=>{
 const store=new Map();const storage={getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
 const pending={key,body:{expectedRevision:4}}
 pendingBenefitSettlement(storage,'a','c',pending)
 assert.deepEqual(pendingBenefitSettlement(storage,'a','c'),pending)
 assert.equal(pendingBenefitSettlement(storage,'b','c'),null)
 assert.equal(pendingBenefitSettlement(storage,'a','other'),null)
 pendingBenefitSettlement(storage,'a','c',null)
 assert.equal(pendingBenefitSettlement(storage,'a','c'),null)
 store.set('mall:benefit-settlement:a:c',JSON.stringify({key,body:{expectedRevision:0}}))
 assert.equal(pendingBenefitSettlement(storage,'a','c'),null)
})

test('zero-cash return accepts explicit zero only for a server zero-cash case',async()=>{
 const {acceptanceError}=await import('../src/views/aftersales/returns.mjs')
 const form={mode:'RECEIVED',receivedQuantity:1,salableQuantity:1,refundQuantity:1,amount:'0',reason:'零现金商品验收通过'}
 assert.equal(acceptanceError(form,1,true),'')
 assert.match(acceptanceError(form,1),/金额/)
})
test('malformed persisted intent is cleared without submitting a replacement',()=>{
 const map=new Map();const storage={getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)}
 for(const raw of ['{',JSON.stringify({key:'unsafe',body:{expectedRevision:1}}),JSON.stringify({key,body:{expectedRevision:1,amountFen:0}})]){
  map.set('mall:benefit-settlement:a:c',raw)
  assert.equal(pendingBenefitSettlement(storage,'a','c'),null)
  assert.equal(map.size,0)
 }
 assert.throws(()=>pendingBenefitSettlement({getItem(){throw Error('storage unavailable')}},'a','c'),/storage/)
 assert.throws(()=>refundAmounts({amountFen:0,goodsRefundAmountFen:0,shippingRefundAmountFen:1.1,totalRefundAmountFen:1.1}),/金额/)
})
