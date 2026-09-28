import test from 'node:test'
import assert from 'node:assert/strict'
import { acceptanceError, acceptanceBody, wechatRefundLabel } from '../src/views/aftersales/returns.mjs'
const form = { mode:'RECEIVED', receivedQuantity:2, salableQuantity:1, refundQuantity:2, amount:'', reason:'收到退货，检查包装及商品后处理' }
test('acceptance validates actual counts, waiver and reason',()=>{
 assert.equal(acceptanceError(form,2),'')
 assert.match(acceptanceError({...form,salableQuantity:3},2),/可售/)
 assert.match(acceptanceError({...form,refundQuantity:3},2),/退款数量/)
 assert.match(acceptanceError({...form,mode:'WAIVED_RETURN'},2),/免寄回/)
 assert.match(acceptanceError({...form,reason:'短'},2),/依据/)
})
test('blank refund amount asks server calculation; money is exact integer fen',()=>{
 assert.equal(acceptanceBody(form,4).refundAmountFen,null)
 assert.equal(acceptanceBody({...form,amount:'10.01'},4).refundAmountFen,1001)
 assert.match(acceptanceError({...form,amount:'1e2'},2),/金额/)
 assert.match(acceptanceError({...form,amount:'0.001'},2),/金额/)
})
test('unknown and processing refund outcomes require queries',()=>{
 assert.match(wechatRefundLabel('UNKNOWN'),/查单/)
 assert.match(wechatRefundLabel('PROCESSING'),/处理中/)
 assert.match(wechatRefundLabel('SUCCEEDED'),/成功/)
})
test('counts remain integers, bounded and consistent across received and waived decisions',()=>{
 assert.match(acceptanceError({...form,mode:'OTHER'},2),/方式/)
 assert.match(acceptanceError({...form,receivedQuantity:1.5},2),/收到/)
 assert.match(acceptanceError({...form,receivedQuantity:3},2),/收到/)
 assert.match(acceptanceError({...form,salableQuantity:-1},2),/可售/)
 assert.match(acceptanceError({...form,refundQuantity:1.5},2),/退款数量/)
 assert.match(acceptanceError({...form,receivedQuantity:1,salableQuantity:1},2),/退款数量/)
 assert.equal(acceptanceError({...form,mode:'WAIVED_RETURN',receivedQuantity:0,salableQuantity:0},2),'')
 assert.equal(acceptanceBody({...form,mode:'WAIVED_RETURN',receivedQuantity:0,salableQuantity:0,amount:'10'},7).refundAmountFen,1000)
 assert.match(acceptanceError({...form,amount:'0'},2),/金额/)
 assert.match(acceptanceError({...form,reason:'长'.repeat(501)},2),/依据/)
 assert.match(wechatRefundLabel('FAILED'),/原退款单/)
 assert.match(wechatRefundLabel('PREPARED'),/尚未发起/)
 assert.match(wechatRefundLabel(null),/尚无/)
})
test('pending acceptance retains exact body and key within its account and case only',async()=>{
 const {pendingAcceptance}=await import('../src/views/aftersales/returns.mjs')
 const store=new Map();const storage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}
 const pending={key:'same-request',body:acceptanceBody(form,4)}
 pendingAcceptance(storage,'a','case',pending)
 assert.deepEqual(pendingAcceptance(storage,'a','case'),pending)
 assert.equal(pendingAcceptance(storage,'b','case'),null)
 assert.equal(pendingAcceptance(storage,'a','other'),null)
 pendingAcceptance(storage,'a','case',null)
 assert.equal(pendingAcceptance(storage,'a','case'),null)
})
test('provider conflict and closed refunds explain why automatic actions are blocked',async()=>{
 const {wechatFailureLabel}=await import('../src/views/aftersales/returns.mjs')
 assert.match(wechatFailureLabel('WECHAT_REFUND_EVENT_CONFLICT'),/人工核查/)
 assert.match(wechatFailureLabel('WECHAT_REFUND_CLOSED'),/重新发起/)
 assert.equal(wechatFailureLabel(''), '')
})
