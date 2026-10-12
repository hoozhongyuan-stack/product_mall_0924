import test from 'node:test';
import assert from 'node:assert/strict';
import { money, businessNumber, quantity, quoteFreight, pointInPolygon, inCircle, zoneQuote } from './state.mjs';

const fixed = { mode: 'fixed', firstAmount: 800, firstUnits: 1, extraAmount: 200, extraUnits: 1, freeThreshold: 19900, blocked: false };
const basket = { amount: 10000, pieces: 3, grams: 2400, campaign: false };
const zone = { mode: 'fixed', fixedAmount: 600, firstDistance: 3, firstAmount: 600, extraDistance: 1, extraAmount: 200, freeThreshold: 9900, minimumAmount: 3900, active: true };
const delivery = { inside: true, excluded: false, amount: 5000, distance: 2, campaign: false };

test('money formats integer fen and rejects non-currency inputs', () => {
  assert.equal(money(0), '¥0.00');
  assert.equal(money(12345), '¥123.45');
  for (const value of [-1, 1.5, NaN, Infinity, '100', null]) assert.throws(() => money(value));
});

test('business numbers use Shanghai creation time and six digit sequences', () => {
  assert.equal(businessNumber('JH', '2026-10-12T06:35:26Z', 1), 'JH20261012143526000001');
  assert.equal(businessNumber('XS', '2026-12-31T23:59:59Z', 999999), 'XS20270101075959999999');
  for (const type of ['x', 'xs', '123', '', null]) assert.throws(() => businessNumber(type, '2026-10-12T00:00:00Z', 1));
  for (const date of ['bad', '', null, '2026-10-12']) assert.throws(() => businessNumber('XS', date, 1));
  for (const sequence of [0, -1, 1000000, 1.5, NaN, '1']) assert.throws(() => businessNumber('XS', '2026-10-12T00:00:00Z', sequence));
});

test('quantity permits zero through 999 and only digit strings', () => {
  for (const [value, expected] of [[0, 0], [999, 999], ['12', 12], ['000', 0]]) assert.equal(quantity(value), expected);
  for (const value of ['', '-1', '1.1', '1e2', ' 2 ', 1000, -1, 1.1, Infinity, NaN, null, true]) assert.throws(() => quantity(value));
});

test('freight fixed, piece and weight modes round additional units upward', () => {
  assert.equal(quoteFreight(fixed, basket).feeFen, 800);
  assert.equal(quoteFreight({ ...fixed, mode: 'piece' }, basket).feeFen, 1200);
  assert.equal(quoteFreight({ ...fixed, mode: 'weight', firstUnits: 1000, extraUnits: 1000 }, basket).feeFen, 1200);
  assert.equal(quoteFreight({ ...fixed, mode: 'weight', firstUnits: 3000, extraUnits: 1000 }, basket).feeFen, 800);
  assert.deepEqual(quoteFreight(fixed, basket), { feeFen: 800, costFen: 800, storeBearsFen: 0, platformBearsFen: 0, reason: '顾客支付运费' });
});

test('example cost split keeps delivery cost when customer freight becomes free', () => {
  assert.deepEqual(quoteFreight(fixed, { ...basket, amount: 19900 }), { feeFen: 0, costFen: 800, storeBearsFen: 800, platformBearsFen: 0, reason: '经营包邮，前置仓承担' });
  assert.deepEqual(quoteFreight(fixed, { ...basket, amount: 25000, campaign: true }), { feeFen: 0, costFen: 800, storeBearsFen: 0, platformBearsFen: 800, reason: '平台活动包邮，平台承担' });
  assert.equal(quoteFreight({ ...fixed, freeThreshold: 0 }, { ...basket, amount: 900000 }).feeFen, 800);
  assert.deepEqual(quoteFreight({ ...fixed, blocked: true }, { ...basket, campaign: true }), { feeFen: 0, costFen: 0, storeBearsFen: 0, platformBearsFen: 0, reason: '该地区不支持快递配送' });
  assert.equal(quoteFreight({ ...fixed, firstAmount: 0 }, basket).costFen, 0);
});

test('freight validates all configurations and basket inputs, even blocked regions', () => {
  const patches = [{ mode: 'unknown' }, { firstAmount: -1 }, { firstAmount: 1.5 }, { extraAmount: Infinity }, { extraUnits: 0 }, { firstUnits: 0 }, { freeThreshold: -1 }, { blocked: 'yes' }];
  for (const patch of patches) assert.throws(() => quoteFreight({ ...fixed, ...patch }, basket));
  for (const patch of [{ amount: -1 }, { pieces: 1.5 }, { pieces: 0 }, { grams: 0 }, { grams: NaN }, { campaign: 'yes' }]) assert.throws(() => quoteFreight(fixed, { ...basket, ...patch }));
  assert.throws(() => quoteFreight({ ...fixed, blocked: true, extraUnits: 0 }, basket));
  assert.throws(() => quoteFreight({ ...fixed, firstAmount: Number.MAX_SAFE_INTEGER, mode: 'piece' }, basket));
});

test('polygon includes edges and vertices, supports concavity and reversed winding', () => {
  const square = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }];
  for (const point of [{ x: 5, y: 5 }, { x: 0, y: 0 }, { x: 10, y: 5 }, { x: 5, y: 10 }]) assert.equal(pointInPolygon(point, square), true);
  assert.equal(pointInPolygon({ x: 11, y: 5 }, square), false);
  assert.equal(pointInPolygon({ x: 5, y: 5 }, [...square].reverse()), true);
  const concave = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 5, y: 5 }, { x: 0, y: 10 }];
  assert.equal(pointInPolygon({ x: 5, y: 8 }, concave), false);
  assert.equal(pointInPolygon({ x: 2, y: 3 }, concave), true);
  assert.throws(() => pointInPolygon({ x: NaN, y: 0 }, square));
  assert.throws(() => pointInPolygon({ x: 0, y: 0 }, []));
  assert.throws(() => pointInPolygon({ x: 0, y: 0 }, [{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 }]));
  assert.throws(() => pointInPolygon({ x: 0, y: 0 }, [{ x: 0, y: 0 }, { x: Infinity, y: 0 }, { x: 0, y: 1 }]));
});

test('circle uses planar preview coordinates and includes boundary', () => {
  assert.equal(inCircle({ x: 3, y: 4 }, { x: 0, y: 0, radius: 5 }), true);
  assert.equal(inCircle({ x: 0, y: 0 }, { x: 0, y: 0, radius: 5 }), true);
  assert.equal(inCircle({ x: 6, y: 0 }, { x: 0, y: 0, radius: 5 }), false);
  assert.throws(() => inCircle({ x: 0, y: 0 }, { x: 0, y: 0, radius: 0 }));
  assert.throws(() => inCircle({ x: 0, y: 0 }, { x: NaN, y: 0, radius: 5 }));
});

test('zone eligibility covers inactive, excluded, outside, minimum and free thresholds', () => {
  assert.equal(zoneQuote(zone, delivery).eligibility, 'ELIGIBLE');
  assert.equal(zoneQuote({ ...zone, active: false }, delivery).eligibility, 'UNAVAILABLE');
  assert.equal(zoneQuote(zone, { ...delivery, excluded: true, inside: false }).eligibility, 'EXCLUDED');
  assert.equal(zoneQuote(zone, { ...delivery, inside: false }).eligibility, 'OUT_OF_RANGE');
  assert.equal(zoneQuote(zone, { ...delivery, amount: 3899 }).eligibility, 'BELOW_MINIMUM');
  assert.equal(zoneQuote(zone, { ...delivery, amount: 3900 }).eligible, true);
  const free = zoneQuote(zone, { ...delivery, amount: 9900 });
  assert.equal(free.storeBearsFen, 600);
  assert.equal(free.feeFen, 0);
  const activity = zoneQuote(zone, { ...delivery, campaign: true });
  assert.equal(activity.platformBearsFen, 600);
  assert.equal(zoneQuote({ ...zone, freeThreshold: 0 }, { ...delivery, amount: 20000 }).feeFen, 600);
  assert.equal(zoneQuote({ ...zone, mode: 'distance' }, { ...delivery, distance: 4.1 }).feeFen, 1000);
  assert.equal(zoneQuote({ ...zone, mode: 'distance' }, { ...delivery, distance: 2 }).feeFen, 600);
});

test('zone rejects invalid config, distances and eligibility flags', () => {
  for (const patch of [{ mode: 'piece' }, { fixedAmount: -1 }, { extraDistance: 0 }, { firstDistance: -1 }, { minimumAmount: -1 }, { active: 'yes' }]) assert.throws(() => zoneQuote({ ...zone, ...patch }, delivery));
  for (const patch of [{ inside: 1 }, { excluded: null }, { amount: 0.5 }, { distance: -1 }, { distance: Infinity }, { campaign: 1 }]) assert.throws(() => zoneQuote(zone, { ...delivery, ...patch }));
  assert.throws(() => zoneQuote({ ...zone, mode: 'distance', firstAmount: Number.MAX_SAFE_INTEGER }, { ...delivery, distance: 4 }));
});
