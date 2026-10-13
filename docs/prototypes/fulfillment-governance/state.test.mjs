import test from 'node:test';
import assert from 'node:assert/strict';
import { seed, getOrder, statusLabel, scopeLabel, actionsFor, remaining, warehouseFor, visibleOrders, transact, updatePolicy, createVirtualDemo } from './state.mjs';
import * as model from './state.mjs';

const platform = { role: 'platform', permitted: true };
const store = { role: 'store', store: 'sz', permitted: true };
const otherStore = { role: 'store', store: 'ns', permitted: true };
const customer = { role: 'customer', permitted: true };
const physicalRemaining = item => model.physicalRemaining(item);
const run = (state, id, action, actor, payload = {}, key = `${id}-${action}`) => transact(state, { id, action, actor, key, payload });
const order = (state, id) => getOrder(state, id);
const override = (state, id, patch) => ({ ...state, orders: state.orders.map(item => item.id === id ? { ...item, ...patch } : item) });
const expectDenied = (state, id, action, actor, payload = {}) => assert.throws(() => run(state, id, action, actor, payload), Error);

test('种子场景完整，标签可读，仓库与交付方式一致', () => {
  const state = seed();
  for (const id of ['delivery', 'pickup', 'express', 'virtual', 'platform-only', 'store-only', 'expired', 'unpaid', 'mixed']) {
    assert.equal(order(state, id).id, id);
    assert.match(statusLabel(order(state, id)), /[\u4e00-\u9fff]/);
  }
  assert.equal(warehouseFor(order(state, 'delivery')), 'sz');
  assert.equal(warehouseFor(order(state, 'pickup')), 'sz');
  assert.equal(warehouseFor(order(state, 'express')), 'central');
  assert.equal(warehouseFor(order(state, 'virtual')), null);
  assert.throws(() => order(state, 'unknown'), Error);
});

test('列表按履约方与权益授权过滤', () => {
  const state = seed();
  assert.equal(visibleOrders(state, platform).length, state.orders.length);
  assert.equal(visibleOrders(state, customer).length, state.orders.length);
  const ids = visibleOrders(state, store).map(item => item.id);
  assert(ids.includes('delivery'));
  assert(ids.includes('pickup'));
  assert(ids.includes('virtual'));
  assert(ids.includes('store-only'));
  assert(!ids.includes('express'));
  assert(!ids.includes('platform-only'));
  assert(!visibleOrders(state, otherStore).some(item => item.id === 'delivery'));
  assert.deepEqual(visibleOrders(state, { ...store, permitted: false }), []);
});

test('配送只由本仓操作，备货配送不重复扣库，操作保持输入不变', () => {
  const initial = override(seed(), 'delivery', { stage: 'PREPARE', paid: true });
  const snapshot = structuredClone(initial);
  expectDenied(initial, 'delivery', 'PREPARE', platform);
  expectDenied(initial, 'delivery', 'PREPARE', otherStore);
  expectDenied(initial, 'delivery', 'PREPARE', { ...store, permitted: false });
  assert(actionsFor(order(initial, 'delivery'), store).includes('PREPARE'));
  assert(!actionsFor(order(initial, 'delivery'), platform).includes('PREPARE'));
  const ready = run(initial, 'delivery', 'PREPARE', store);
  assert.equal(order(ready, 'delivery').stage, 'READY');
  const dispatched = run(ready, 'delivery', 'DISPATCH', store);
  assert.equal(order(dispatched, 'delivery').stage, 'DISPATCHED');
  const done = run(dispatched, 'delivery', 'COMPLETE', store);
  assert.equal(order(done, 'delivery').stage, 'DONE');
  assert.deepEqual(done.stock, initial.stock);
  assert.deepEqual(initial, snapshot);
  expectDenied(initial, 'delivery', 'COMPLETE', store);
  assert.throws(() => run(done, 'delivery', 'PREPARE', store, {}, 'delivery-PREPARE-after-done'), Error);
  assert(done.events.length >= initial.events.length + 3);
});

test('配送异常记录与重试不提前完成订单', () => {
  const state = override(seed(), 'delivery', { stage: 'DISPATCHED', paid: true });
  const failed = run(state, 'delivery', 'FAIL_DELIVERY', store, { reason: '顾客暂时无法接收' });
  assert.notEqual(order(failed, 'delivery').stage, 'DONE');
  const retried = run(failed, 'delivery', 'RETRY_DELIVERY', store);
  assert.equal(order(retried, 'delivery').stage, 'DISPATCHED');
  assert.equal(order(run(retried, 'delivery', 'COMPLETE', store), 'delivery').stage, 'DONE');
  expectDenied(state, 'delivery', 'FAIL_DELIVERY', platform);
});

test('自提要求本仓、待自提状态和正确凭证', () => {
  const state = override(seed(), 'pickup', { stage: 'PREPARE', paid: true });
  expectDenied(state, 'pickup', 'PICKUP', store, { code: order(state, 'pickup').pickupCode });
  const ready = run(state, 'pickup', 'PREPARE', store);
  expectDenied(ready, 'pickup', 'PICKUP', store, { code: '错误凭证' });
  expectDenied(ready, 'pickup', 'PICKUP', platform, { code: order(ready, 'pickup').pickupCode });
  const done = run(ready, 'pickup', 'PICKUP', store, { code: order(ready, 'pickup').pickupCode });
  assert.equal(order(done, 'pickup').stage, 'DONE');
  assert.deepEqual(done.stock, state.stock);
});

test('快递由平台发货，验证运单，顾客收货，前置仓无法发货', () => {
  const state = override(seed(), 'express', { stage: 'PREPARE', paid: true });
  expectDenied(state, 'express', 'SHIP', store, { carrier: '顺丰', tracking: 'SF123456789' });
  for (const payload of [{}, { carrier: '顺丰' }, { carrier: '', tracking: '123' }, { carrier: '顺丰', tracking: '   ' }]) {
    expectDenied(state, 'express', 'SHIP', platform, payload);
  }
  const shipped = run(state, 'express', 'SHIP', platform, { carrier: '顺丰', tracking: 'SF123456789' });
  assert.equal(order(shipped, 'express').stage, 'SHIPPED');
  expectDenied(shipped, 'express', 'RECEIVE', store);
  const done = run(shipped, 'express', 'RECEIVE', customer);
  assert.equal(order(done, 'express').stage, 'DONE');
  assert.deepEqual(done.stock, state.stock);
});

test('付款正式扣指定仓一次，取消未付款订单不增加可用余额', () => {
  const state = override(seed(), 'unpaid', { stage: 'UNPAID', paid: false, store: 'sz', inventoryQty: 2 });
  expectDenied(state, 'unpaid', 'PREPARE', store);
  const paid = run(state, 'unpaid', 'PAY', customer);
  assert.equal(order(paid, 'unpaid').paid, true);
  assert.equal(paid.stock.sz, state.stock.sz - 2);
  assert.equal(paid.stock.central, state.stock.central);
  assert.equal(paid.reserved.sz, 0);
  assert.equal(paid.reserved.central, state.reserved.central);
  assert.equal(run(paid, 'unpaid', 'PAY', customer, {}, 'unpaid-PAY'), paid);
  assert.throws(() => run(paid, 'unpaid', 'PAY', customer, {}, 'unpaid-PAY-again'), Error);
  const canceled = run(state, 'unpaid', 'CANCEL', customer);
  assert.deepEqual(canceled.stock, state.stock);
  assert.equal(canceled.reserved.sz, 0);
  assert.equal(canceled.reserved.ns, state.reserved.ns);
  assert.notEqual(order(canceled, 'unpaid').stage, 'UNPAID');
  expectDenied(canceled, 'unpaid', 'PAY', customer);
});

test('付款不足不回退到其他仓，已付款订单不能取消', () => {
  const state = { ...seed(), stock: { central: 100, sz: 0, ns: 100 } };
  expectDenied(state, 'unpaid', 'PAY', customer);
  expectDenied(state, 'delivery', 'CANCEL', customer);
});

test('核销范围三种授权，平台前置仓共享同一剩余权益', () => {
  const initial = override(seed(), 'virtual', { stage: 'REDEEM', paid: true, qty: 5, redeemed: 0, voided: 0, frozen: 0, expired: false, scope: 'BOTH', scopeStores: ['sz'] });
  const code = order(initial, 'virtual').voucher;
  const two = run(initial, 'virtual', 'REDEEM', platform, { code, qty: 2 }, 'virtual-1');
  assert.equal(remaining(order(two, 'virtual')), 3);
  const done = run(two, 'virtual', 'REDEEM', store, { code, qty: 3 }, 'virtual-2');
  assert.equal(remaining(order(done, 'virtual')), 0);
  assert.equal(order(done, 'virtual').stage, 'DONE');
  assert.deepEqual(done.stock, initial.stock);
  expectDenied(initial, 'virtual', 'REDEEM', otherStore, { code, qty: 1 });
  expectDenied(seed(), 'platform-only', 'REDEEM', store, { code: order(seed(), 'platform-only').voucher, qty: 1 });
  expectDenied(seed(), 'store-only', 'REDEEM', platform, { code: order(seed(), 'store-only').voucher, qty: 1 });
  assert(actionsFor(order(seed(), 'platform-only'), platform).includes('REDEEM'));
  assert(actionsFor(order(seed(), 'store-only'), store).includes('REDEEM'));
});

test('核销校验凭证、正整数、剩余数量、有效期和付款状态', () => {
  const state = seed();
  const code = order(state, 'virtual').voucher;
  for (const qty of [0, -1, 1.5, '1', null, Infinity, NaN, 1000]) {
    expectDenied(state, 'virtual', 'REDEEM', platform, { code, qty });
  }
  expectDenied(state, 'virtual', 'REDEEM', platform, { code: '错误凭证', qty: 1 });
  expectDenied(state, 'expired', 'REDEEM', platform, { code: order(state, 'expired').voucher, qty: 1 });
  const unpaid = override(state, 'virtual', { paid: false });
  expectDenied(unpaid, 'virtual', 'REDEEM', platform, { code, qty: 1 });
  const exhausted = override(state, 'virtual', { redeemed: order(state, 'virtual').qty });
  assert.equal(remaining(order(exhausted, 'virtual')), 0);
  expectDenied(exhausted, 'virtual', 'REDEEM', platform, { code, qty: 1 });
});

test('虚拟售后按数量冻结，拒绝释放，退款作废，已核销不能自动恢复', () => {
  const state = override(seed(), 'virtual', { qty: 5, redeemed: 1, frozen: 0, voided: 0, stage: 'REDEEM', paid: true });
  const frozen = run(state, 'virtual', 'APPLY_AFTER', customer, { qty: 2 });
  assert.equal(order(frozen, 'virtual').frozen, 2);
  assert.equal(remaining(order(frozen, 'virtual')), 2);
  expectDenied(frozen, 'virtual', 'REDEEM', platform, { code: order(frozen, 'virtual').voucher, qty: 3 });
  expectDenied(frozen, 'virtual', 'REFUND', store);
  const refunded = run(frozen, 'virtual', 'REFUND', platform);
  assert.equal(order(refunded, 'virtual').voided, 2);
  assert.equal(order(refunded, 'virtual').redeemed, 1);
  assert.equal(order(refunded, 'virtual').frozen, 0);
  assert.deepEqual(refunded.stock, state.stock);
  const released = run(frozen, 'virtual', 'REJECT_AFTER', platform);
  assert.equal(order(released, 'virtual').frozen, 0);
  assert.equal(remaining(order(released, 'virtual')), 4);
  expectDenied(state, 'virtual', 'REFUND', platform);
  expectDenied(state, 'virtual', 'APPLY_AFTER', customer, { qty: 5 });
});

test('实物未交付退款回补原仓，售后冻结禁止交付', () => {
  const state = override(seed(), 'delivery', { qty: 2, inventoryQty: 2, frozen: 0, stage: 'PREPARE', paid: true });
  const frozen = run(state, 'delivery', 'APPLY_AFTER', customer, { qty: 2 });
  expectDenied(frozen, 'delivery', 'PREPARE', store);
  const refunded = run(frozen, 'delivery', 'REFUND', platform);
  assert.equal(refunded.stock.sz, state.stock.sz + 2);
  assert.equal(refunded.stock.central, state.stock.central);
  const express = override(seed(), 'express', { qty: 1, inventoryQty: 1, stage: 'PREPARE', frozen: 0, paid: true });
  const expressFrozen = run(express, 'express', 'APPLY_AFTER', customer, { qty: 1 });
  const expressRefund = run(expressFrozen, 'express', 'REFUND', platform);
  assert.equal(expressRefund.stock.central, express.stock.central + 1);
  assert.equal(expressRefund.stock.sz, express.stock.sz);
  const delivered = override(state, 'delivery', { stage: 'DONE' });
  expectDenied(delivered, 'delivery', 'REFUND', platform);
});

test('售后数量严格校验，冻结不会将待处理权益误判完成', () => {
  const state = seed();
  for (const qty of [0, -1, 1.1, '1', null, Infinity, 10000]) {
    expectDenied(state, 'virtual', 'APPLY_AFTER', customer, { qty });
  }
  const virtual = override(state, 'virtual', { qty: 2, redeemed: 0, voided: 0, frozen: 0 });
  const frozen = run(virtual, 'virtual', 'APPLY_AFTER', customer, { qty: 2 });
  assert.equal(remaining(order(frozen, 'virtual')), 0);
  assert.notEqual(order(frozen, 'virtual').stage, 'DONE');
  expectDenied(virtual, 'virtual', 'REJECT_AFTER', platform);
});

test('混合订单须快递收货及虚拟义务完成两项都满足', () => {
  const initial = override(seed(), 'mixed', { stage: 'PREPARE', paid: true, qty: 1, redeemQty: 3, redeemed: 0, voided: 0, frozen: 0, physicalDone: false, scope: 'BOTH', scopeStores: ['sz'] });
  const redeemed = run(initial, 'mixed', 'REDEEM', store, { code: order(initial, 'mixed').voucher, qty: 3 });
  assert.notEqual(order(redeemed, 'mixed').stage, 'DONE');
  const shipped = run(redeemed, 'mixed', 'SHIP', platform, { carrier: '顺丰', tracking: 'SF123456789' });
  assert.equal(order(run(shipped, 'mixed', 'RECEIVE', customer), 'mixed').stage, 'DONE');
  const shippedFirst = run(initial, 'mixed', 'SHIP', platform, { carrier: '顺丰', tracking: 'SF987654321' }, 'mixed-second-ship');
  const received = run(shippedFirst, 'mixed', 'RECEIVE', customer, {}, 'mixed-second-receive');
  assert.equal(order(received, 'mixed').physicalDone, true);
  assert.notEqual(order(received, 'mixed').stage, 'DONE');
  assert.equal(order(run(received, 'mixed', 'REDEEM', platform, { code: order(received, 'mixed').voucher, qty: 3 }, 'mixed-second-redeem'), 'mixed').stage, 'DONE');
});

test('配置修改保持旧订单快照，新订单应用新核销范围', () => {
  const initial = seed();
  const snapshot = structuredClone(initial);
  const changed = updatePolicy(initial, { scope: 'STORE', stores: ['ns'] });
  assert.deepEqual(order(changed, 'virtual'), order(initial, 'virtual'));
  assert.deepEqual(changed.policies.virtual, { scope: 'STORE', stores: ['ns'] });
  assert.deepEqual(initial, snapshot);
  const added = createVirtualDemo(changed);
  assert.equal(added.orders.length, changed.orders.length + 1);
  const newOrder = added.orders.at(-1);
  assert.equal(newOrder.scope, 'STORE');
  assert.deepEqual(newOrder.scopeStores, ['ns']);
  assert(!actionsFor(newOrder, platform).includes('REDEEM'));
  assert(actionsFor(newOrder, otherStore).includes('REDEEM'));
  assert.deepEqual(changed.orders, initial.orders);
  for (const config of [{ scope: 'INVALID', stores: [] }, { scope: 'STORE', stores: [] }, { scope: 'BOTH', stores: ['unknown'] }, { scope: 'STORE', stores: 'sz' }]) {
    assert.throws(() => updatePolicy(initial, config), Error);
  }
  assert.equal(updatePolicy(initial, { scope: 'PLATFORM', stores: [] }).policies.virtual.scope, 'PLATFORM');
});

test('幂等按请求内容识别，相同请求返回原状态，键冲突拒绝', () => {
  const state = override(seed(), 'virtual', { qty: 5, redeemed: 0, voided: 0, frozen: 0 });
  const payload = { code: order(state, 'virtual').voucher, qty: 1 };
  const next = run(state, 'virtual', 'REDEEM', platform, payload, 'same-key');
  assert.equal(run(next, 'virtual', 'REDEEM', platform, payload, 'same-key'), next);
  assert.equal(order(next, 'virtual').redeemed, 1);
  assert.throws(() => run(next, 'virtual', 'REDEEM', platform, { ...payload, qty: 2 }, 'same-key'), Error);
  assert.throws(() => run(next, 'virtual', 'REDEEM', store, payload, 'same-key'), Error);
  assert.throws(() => run(next, 'pickup', 'PICKUP', store, { code: order(next, 'pickup').pickupCode }, 'same-key'), Error);
});

test('输入守卫拒绝未知订单、动作、角色和缺失幂等键', () => {
  const state = seed();
  assert.throws(() => transact(state, { id: 'missing', action: 'PAY', actor: customer, key: 'x' }), Error);
  assert.throws(() => transact(state, { id: 'delivery', action: 'INVENTED', actor: store, key: 'x' }), Error);
  assert.throws(() => transact(state, { id: 'delivery', action: 'PREPARE', actor: { role: 'intruder', permitted: true }, key: 'x' }), Error);
  assert.throws(() => transact(state, { id: 'delivery', action: 'PREPARE', actor: store }), Error);
  assert.throws(() => transact(state, { id: 'delivery', action: 'PREPARE', actor: null, key: 'x' }), Error);
  assert.deepEqual(actionsFor(order(state, 'delivery'), { ...store, permitted: false }), []);
});

test('状态标签覆盖终态、异常、到期、冻结和待处理', () => {
  const base = order(seed(), 'delivery');
  assert.equal(statusLabel({ ...base, stage: 'DONE' }), '已完成');
  assert.equal(statusLabel({ ...base, stage: 'CANCELED' }), '已取消');
  assert.equal(statusLabel({ ...base, stage: 'REFUNDED' }), '已退款');
  assert.equal(statusLabel({ ...base, stage: 'FAILED' }), '配送异常');
  assert.equal(statusLabel({ ...base, stage: 'UNKNOWN' }), '待处理');
  assert.equal(statusLabel({ ...base, paid: false }), '待付款');
  assert.equal(statusLabel({ ...base, kind: 'VIRTUAL', stage: 'REDEEM', qty: 2, redeemed: 0, frozen: 2 }), '售后处理中');
  assert.equal(statusLabel(order(seed(), 'expired')), '已到期 · 待处理');
  assert.equal(statusLabel({ ...order(seed(), 'mixed'), physicalDone: true, stage: 'SHIPPED' }), '待核销');
});

test('核销范围标签展示平台及指定前置仓，不借用订单来源', () => {
  const base = order(seed(), 'virtual');
  assert.equal(scopeLabel({ ...base, scope: 'PLATFORM', scopeStores: [] }), '仅平台');
  assert.equal(scopeLabel({ ...base, scope: 'STORE', scopeStores: ['ns'] }), '南山前置仓');
  assert.equal(scopeLabel({ ...base, scope: 'BOTH', scopeStores: ['sz', 'ns'] }), '平台＋深圳1号前置仓、南山前置仓');
});

test('配送异常原因要求有效文字且不超过200字', () => {
  const state = override(seed(), 'delivery', { stage: 'DISPATCHED' });
  for (const reason of [undefined, null, 123, '', '   ', '短', '原'.repeat(201)]) {
    expectDenied(state, 'delivery', 'FAIL_DELIVERY', store, { reason });
  }
  const next = run(state, 'delivery', 'FAIL_DELIVERY', store, { reason: '  地址暂时无法进入  ' });
  assert.equal(order(next, 'delivery').failureReason, '地址暂时无法进入');
  assert.equal(statusLabel(order(next, 'delivery')), '配送异常');
});

test('运单拒绝非法格式与未知承运商，允许合法边界长度', () => {
  const state = seed();
  for (const tracking of ['abc12', 'A'.repeat(31), 'SF 123456', '<script>', '中文123456', '123456\n']) {
    expectDenied(state, 'express', 'SHIP', platform, { carrier: '顺丰', tracking });
  }
  expectDenied(state, 'express', 'SHIP', platform, { carrier: '未知快递', tracking: 'ABC123456' });
  assert.equal(order(run(state, 'express', 'SHIP', platform, { carrier: '中通快递', tracking: 'ABC123' }), 'express').tracking, 'ABC123');
  assert.equal(order(run(state, 'express', 'SHIP', platform, { carrier: '圆通速递', tracking: 'A'.repeat(30) }), 'express').tracking.length, 30);
});

test('混合单冻结的权益不算完成，收货后需退款关闭或继续核销', () => {
  const initial = override(seed(), 'mixed', { qty: 1, redeemQty: 3, redeemed: 0, voided: 0, frozen: 0, stage: 'PREPARE', physicalDone: false });
  const shipped = run(initial, 'mixed', 'SHIP', platform, { carrier: '顺丰', tracking: 'SF123456789' });
  const frozen = run(shipped, 'mixed', 'APPLY_AFTER', customer, { qty: 1 });
  const redeemed = run(frozen, 'mixed', 'REDEEM', store, { code: order(frozen, 'mixed').voucher, qty: 2 });
  assert.equal(remaining(order(redeemed, 'mixed')), 0);
  const received = run(redeemed, 'mixed', 'RECEIVE', customer);
  assert.equal(order(received, 'mixed').physicalDone, true);
  assert.equal(order(received, 'mixed').frozen, 1);
  assert.notEqual(order(received, 'mixed').stage, 'DONE');
  const refunded = run(received, 'mixed', 'REFUND', platform);
  assert.equal(order(refunded, 'mixed').stage, 'DONE');
  assert.deepEqual(refunded.stock, initial.stock);
  const released = run(received, 'mixed', 'REJECT_AFTER', platform);
  assert.equal(order(released, 'mixed').frozen, 0);
  assert.notEqual(order(released, 'mixed').stage, 'DONE');
  const done = run(released, 'mixed', 'REDEEM', platform, { code: order(released, 'mixed').voucher, qty: 1 }, 'mixed-last-redeem');
  assert.equal(order(done, 'mixed').stage, 'DONE');
});

test('幂等忽略载荷字段顺序，授权仍在幂等返回前校验', () => {
  const initial = seed();
  const code = order(initial, 'virtual').voucher;
  const next = run(initial, 'virtual', 'REDEEM', platform, { code, qty: 1 }, 'ordered-payload');
  assert.equal(run(next, 'virtual', 'REDEEM', platform, { qty: 1, code }, 'ordered-payload'), next);
  assert.throws(() => run(next, 'virtual', 'REDEEM', { ...platform, permitted: false }, { code, qty: 1 }, 'ordered-payload'), Error);
  assert.throws(() => transact(initial, { id: 'delivery', action: 'PREPARE', actor: store, key: '  ' }), Error);
  assert.deepEqual(visibleOrders(initial, { role: 'unknown', permitted: true }), []);
  assert.deepEqual(actionsFor(order(initial, 'delivery'), { role: 'unknown', permitted: true }), []);
});

test('混合单虚拟售后冻结不阻断独立实物发货与收货', () => {
  const initial = override(seed(), 'mixed', { qty: 1, redeemQty: 3, redeemed: 0, voided: 0, frozen: 0, handedQty: 0, stage: 'PREPARE', physicalDone: false });
  const frozen = run(initial, 'mixed', 'APPLY_AFTER', customer, { qty: 1 });
  assert(actionsFor(order(frozen, 'mixed'), platform).includes('SHIP'));
  assert.equal(physicalRemaining(order(frozen, 'mixed')), 1);
  const shipped = run(frozen, 'mixed', 'SHIP', platform, { carrier: '顺丰', tracking: 'SF123456789' });
  const received = run(shipped, 'mixed', 'RECEIVE', customer);
  assert.equal(order(received, 'mixed').physicalDone, true);
  assert.equal(order(received, 'mixed').handedQty, 1);
  assert.equal(order(received, 'mixed').frozen, 1);
  assert.notEqual(order(received, 'mixed').stage, 'DONE');
  assert.deepEqual(received.stock, initial.stock);
});

test('实物部分冻结只交付可履约数量，剩余退款回补原仓后完成', () => {
  const initial = override(seed(), 'delivery', { qty: 2, inventoryQty: 2, frozen: 0, voided: 0, handedQty: 0, stage: 'PREPARE', physicalDone: false });
  const frozen = run(initial, 'delivery', 'APPLY_AFTER', customer, { qty: 1 });
  assert(actionsFor(order(frozen, 'delivery'), store).includes('PREPARE'));
  assert.equal(physicalRemaining(order(frozen, 'delivery')), 1);
  const ready = run(frozen, 'delivery', 'PREPARE', store);
  const dispatched = run(ready, 'delivery', 'DISPATCH', store);
  const delivered = run(dispatched, 'delivery', 'COMPLETE', store);
  assert.equal(order(delivered, 'delivery').handedQty, 1);
  assert.equal(order(delivered, 'delivery').frozen, 1);
  assert.notEqual(order(delivered, 'delivery').stage, 'DONE');
  assert.equal(physicalRemaining(order(delivered, 'delivery')), 0);
  assert(!actionsFor(order(delivered, 'delivery'), store).includes('COMPLETE'));
  assert.deepEqual(delivered.stock, initial.stock);
  const refunded = run(delivered, 'delivery', 'REFUND', platform);
  assert.equal(refunded.stock.sz, initial.stock.sz + 1);
  assert.equal(refunded.stock.central, initial.stock.central);
  assert.equal(order(refunded, 'delivery').handedQty, 1);
  assert.equal(order(refunded, 'delivery').voided, 1);
  assert.equal(order(refunded, 'delivery').stage, 'DONE');
  assert.equal(run(refunded, 'delivery', 'REFUND', platform), refunded);
  assert.throws(() => run(refunded, 'delivery', 'REFUND', platform, {}, 'delivery-refund-again'), Error);
});

test('自提部分售后冻结后仍可凭证提取未冻结商品', () => {
  const initial = override(seed(), 'pickup', { qty: 2, inventoryQty: 2, frozen: 0, voided: 0, handedQty: 0, stage: 'READY', physicalDone: false });
  const frozen = run(initial, 'pickup', 'APPLY_AFTER', customer, { qty: 1 });
  assert(actionsFor(order(frozen, 'pickup'), store).includes('PICKUP'));
  assert.equal(physicalRemaining(order(frozen, 'pickup')), 1);
  const picked = run(frozen, 'pickup', 'PICKUP', store, { code: order(frozen, 'pickup').pickupCode });
  assert.equal(order(picked, 'pickup').handedQty, 1);
  assert.equal(order(picked, 'pickup').frozen, 1);
  assert.notEqual(order(picked, 'pickup').stage, 'DONE');
  assert.deepEqual(picked.stock, initial.stock);
  assert(!actionsFor(order(picked, 'pickup'), store).includes('PICKUP'));
});
