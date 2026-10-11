import { test } from 'node:test'
import assert from 'node:assert/strict'
import { actionBody, actionPermission, availableActions, pendingProcurement, resultMatches, proofUrl } from '../src/views/stores/procurement/domain.mjs'
const id = '11a27082-c544-4ed8-8b70-3c1070bf560b'
test('procurement validates integer money and frozen status actions', () => {
 assert.equal(actionBody('review', { freight: '1.001' }, 2).error.includes('两位'), true)
 assert.deepEqual(actionBody('review', { freight:'12.50' },2).body,{ expectedRevision:2, freightFen:1250 })
 assert.ok(actionBody('ship',{carrier:'',tracking:'x'},2).error)
 assert.ok(actionBody('close-cancel',{reference:'x',note:'',refundConfirmed:false},2).error)
 assert.equal(actionPermission('confirm-payment'),'stores.procurement.finance')
 assert.equal(actionPermission('ship'),'stores.procurement.manage')
 assert.deepEqual(availableActions({status:'PROOF',unitReviewRequired:false}),['confirm-payment','reject-proof'])
 assert.deepEqual(availableActions({status:'PAID',unitReviewRequired:true}),['verify-units','ship'])
})
test('private snapshots expire and reject invalid paths or request identities',()=>{
 const data = new Map(), storage = {getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)}
 const p = {key:id,path:`/stores/procurement/orders/${id}/ship`,method:'POST',body:{expectedRevision:1,requestKey:id},action:''}
 pendingProcurement(storage,'a',id,p,10)
 assert.equal(pendingProcurement(storage,'a',id,undefined,11).key,id)
 assert.equal(pendingProcurement(storage,'a',id,undefined,3600011),null)
 pendingProcurement(storage,'a',id,{...p,path:'/evil'},10)
 assert.equal(pendingProcurement(storage,'a',id,undefined,11),null)
 pendingProcurement(storage,'a',id,p,10); pendingProcurement(storage,'a',id,null)
 assert.equal(pendingProcurement(storage,'a',id),null)
})
test('private proof URL and result matching are bound to order and revision',()=>{
 assert.equal(proofUrl({assetId:id,previewUrl:`/api/v1/admin/stores/procurement/proofs/${id}/file`}),`/api/v1/admin/stores/procurement/proofs/${id}/file`)
 assert.equal(proofUrl({assetId:id,previewUrl:'https://evil/a'}),'')
 const p={path:`/stores/procurement/orders/${id}/ship`,body:{expectedRevision:2}}
 assert.equal(resultMatches(p,{id,revision:3,status:'SHIPPED'}),true)
 assert.equal(resultMatches(p,{id,revision:2,status:'PAID'}),false)
})
