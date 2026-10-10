import test from 'node:test'
import assert from 'node:assert/strict'
import { shipmentError, redemptionError, remaining, fulfillmentLabel, shipmentEligible } from '../src/views/orders/fulfillment.mjs'

test('shipment requires a configured carrier and a bounded tracking number', () => {
  const carriers = [{ code: 'SIGNED', name: '已签约快递' }]
  assert.equal(shipmentError('', 'SF123', carriers), '请选择可用快递公司。')
  assert.equal(shipmentError('OTHER', 'SF123', carriers), '请选择可用快递公司。')
  assert.equal(shipmentError('SIGNED', '', carriers), '请输入运单号。')
  assert.equal(shipmentError('SIGNED', ' x ', carriers), '')
  assert.equal(shipmentError('SIGNED', 'x'.repeat(81), carriers), '运单号不能超过 80 字。')
  assert.equal(shipmentError('SIGNED', 'SF123', []), '暂无可用快递公司，暂不能发货。')
})

test('redemption quantity is a whole number within the server remaining count', () => {
  assert.equal(redemptionError('0', 4), '本次核销数量必须是正整数。')
  assert.equal(redemptionError('1.5', 4), '本次核销数量必须是正整数。')
  assert.equal(redemptionError('5', 4), '本次核销数量超过可核销数量。')
  assert.equal(redemptionError('1', 0), '当前凭证没有可核销数量。')
  assert.equal(redemptionError('4', 4), '')
  assert.equal(remaining({ quantity: 6, fulfillment: { redeemedQuantity: 2, voidedQuantity: 1 } }), 3)
})

test('fulfillment labels and shipping eligibility reflect independent order state', () => {
  assert.equal(fulfillmentLabel('PARTIAL'), '部分核销')
  assert.equal(fulfillmentLabel('COMPLETED'), '已完成')
  assert.equal(shipmentEligible({ status: 'PAID', fulfillment: { shipStatus: 'PENDING' } }), true)
  assert.equal(shipmentEligible({ status: 'PAID', fulfillment: { shipStatus: 'WAITING_SHIPMENT' } }), true)
  assert.equal(shipmentEligible({ status: 'PAID', shipEligible: true }), true)
  assert.equal(shipmentEligible({ status: 'PAID', shipEligible: false }), false)
  assert.equal(shipmentEligible({ status: 'PAID', fulfillmentStatus: 'IN_PROGRESS', items: [{ fulfillmentKind: 'SHIP', fulfillment: { status: 'WAITING_SHIPMENT' } }, { fulfillmentKind: 'REDEEM', fulfillment: { status: 'PARTIAL' } }] }), true)
  assert.equal(shipmentEligible({ status: 'PAID', fulfillment: { shipStatus: 'SHIPPED' } }), false)
  assert.equal(shipmentEligible({ status: 'CLOSED', fulfillment: { shipStatus: 'PENDING' } }), false)
})

test('after-sale and refund states are distinct from completed fulfillment', () => {
  assert.equal(fulfillmentLabel('AFTER_SALE'), '售后处理中')
  assert.equal(fulfillmentLabel('REFUNDED'), '已退款')
  assert.equal(shipmentEligible({ status: 'PAID', fulfillmentStatus: 'AFTER_SALE',
    items: [{ fulfillmentKind: 'SHIP', fulfillment: { status: 'WAITING_SHIPMENT' } }] }), false)
  assert.equal(shipmentEligible({ status: 'PAID', fulfillmentStatus: 'REFUNDED',
    items: [{ fulfillmentKind: 'SHIP', fulfillment: { status: 'WAITING_SHIPMENT' } }] }), false)
})
test('shipment quantities preserve purchased units and subtract only confirmed refunded units',async()=>{const {shippingQuantity,shipmentQuantityError}=await import('../src/views/orders/fulfillment.mjs');assert.deepEqual(shippingQuantity({quantity:2,fulfillment:{refundedQuantity:1}}),{purchased:2,refunded:1,remaining:1});assert.deepEqual(shippingQuantity({quantity:2,fulfillment:{refundedQuantity:0,heldQuantity:1}}),{purchased:2,refunded:0,remaining:2});assert.equal(shipmentQuantityError([{fulfillmentKind:'SHIP',quantity:2,fulfillment:{refundedQuantity:1}}]),'');assert.match(shipmentQuantityError([{fulfillmentKind:'SHIP',quantity:2,fulfillment:{refundedQuantity:2}}]),/没有待发/);assert.equal(shipmentQuantityError([{fulfillmentKind:'SHIP',quantity:2,fulfillment:{refundedQuantity:0}},{fulfillmentKind:'REDEEM',quantity:4}]),'')})
test('missing malformed or out of range refund quantities block shipment instead of becoming zero',async()=>{const {shippingQuantity,shipmentQuantityError}=await import('../src/views/orders/fulfillment.mjs');for(const refundedQuantity of [undefined,null,-1,3,0.5,'1',NaN]){const line={quantity:2,fulfillmentKind:'SHIP',fulfillment:{refundedQuantity}};assert.equal(shippingQuantity(line),null);assert.match(shipmentQuantityError([line]),/待核查/)}assert.equal(shippingQuantity({quantity:0,fulfillment:{refundedQuantity:0}}),null);assert.match(shipmentQuantityError([]),/没有待发/);assert.match(shipmentQuantityError(undefined),/待核查/)})

test('store physical fulfillment labels and shipping guards remain separate from redemption',()=>{
 assert.equal(fulfillmentLabel('WAITING_PREPARATION'),'待备货')
 assert.equal(fulfillmentLabel('WAITING_PICKUP'),'待自提')
 assert.equal(fulfillmentLabel('WAITING_DELIVERY'),'待配送')
 assert.equal(fulfillmentLabel('DELIVERING'),'配送中')
 assert.equal(shipmentEligible({status:'PAID',deliveryMode:'PICKUP',shipEligible:true}),false)
 assert.equal(shipmentEligible({status:'PAID',deliveryMode:'DELIVERY',shipEligible:true}),false)
})
