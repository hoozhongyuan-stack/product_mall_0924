import test from 'node:test';
import assert from 'node:assert/strict';
import { seed, createVirtualDemo } from './state.mjs';
import { adminView } from './admin.mjs';
import { mobileView } from './mobile.mjs';
import { esc } from './views.mjs';

const ui = { role: 'platform', page: 'list', selected: 'delivery', store: 'sz', viewState: 'normal', task: 'ALL', storeTask: 'ALL', mode: 'ALL', owner: 'ALL', search: '', pagination: 1 };
test('平台按前置仓筛选包含该仓核销范围的虚拟与混合订单', () => {
  const data = seed();
  const state = { ...data, orders: data.orders.filter(order => ['virtual', 'store-only', 'mixed', 'platform-only'].includes(order.id)) };
  const html = adminView(state, { ...ui, owner: 'sz' }, { role: 'platform', permitted: true });
  assert.match(html, /洗车服务次卡/);
  assert.match(html, /咖啡兑换券/);
  assert.match(html, /礼盒/);
  assert.doesNotMatch(html, /线上咨询服务/);
});
test('无权限时不在辅助区透露订单与库存', () => {
  const html = mobileView(seed(), { ...ui, role: 'store', viewState: 'denied' }, { role: 'store', store: 'sz', permitted: false });
  assert.match(html, /暂无订单操作权限/);
  assert.doesNotMatch(html, /精酿啤酒组合|洗车服务次卡|销售账面余量|当前可售|scenario-select/);
});
test('平台详情不向订单管理员展示顾客核销码和自提码', () => {
  const state = seed();
  for (const id of ['virtual', 'pickup']) {
    const html = adminView(state, { ...ui, page: 'detail', selected: id }, { role: 'platform', permitted: true });
    const order = state.orders.find(item => item.id === id);
    assert.doesNotMatch(html, new RegExp(order.voucher));
    assert.doesNotMatch(html, new RegExp(order.pickupCode));
  }
});
test('已到期凭证不展示可使用的演示码，剩余权益仍可查看', () => {
  const state = seed();
  const order = state.orders.find(item => item.id === 'expired');
  const html = mobileView(state, { ...ui, role: 'customer', page: 'detail', selected: 'expired' }, { role: 'customer', permitted: true });
  assert.match(html, /已到期/);
  assert.doesNotMatch(html, new RegExp(order.voucher));
  assert.match(html, /申请售后/);
});
test('订单文本中的HTML作为普通文字显示', () => {
  const data = seed();
  const payload = '<img src=x onerror="alert(1)">';
  const state = { ...data, orders: data.orders.map(order => order.id === 'delivery' ? { ...order, name: payload, address: payload, failureReason: payload, stage: 'FAILED' } : order) };
  const html = mobileView(state, { ...ui, role: 'customer', page: 'detail' }, { role: 'customer', permitted: true });
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img/);
  assert.equal(esc('<&"\'>'), '&lt;&amp;&quot;&#39;&gt;');
});
test('前置仓工作台仅显示本仓实物及授权核销任务', () => {
  const html = mobileView(seed(), { ...ui, role: 'store' }, { role: 'store', store: 'sz', permitted: true });
  const phone = html.split('<section class="phone"')[1].split('<aside class="annotation"')[0];
  assert.match(phone, /洗车服务次卡|咖啡兑换券/);
  assert.doesNotMatch(phone, /红葡萄酒双瓶装|线上咨询服务|data-action="SHIP"/);
  const empty = mobileView(seed(), { ...ui, role: 'store', store: 'ns' }, { role: 'store', store: 'ns', permitted: true });
  assert.match(empty, /暂无符合条件的订单/);
});
test('顾客自提码只在尚有可提商品时显示', () => {
  const state = seed();
  const pickup = state.orders.find(order => order.id === 'pickup');
  const html = mobileView(state, { ...ui, role: 'customer', page: 'detail', selected: 'pickup' }, { role: 'customer', permitted: true });
  assert.match(html, new RegExp(pickup.pickupCode));
  const finished = { ...state, orders: state.orders.map(order => order.id === 'pickup' ? { ...order, stage: 'DONE', handedQty: 1 } : order) };
  const closed = mobileView(finished, { ...ui, role: 'customer', page: 'detail', selected: 'pickup' }, { role: 'customer', permitted: true });
  assert.doesNotMatch(closed, new RegExp(pickup.pickupCode));
});
test('配置界面同时呈现已保存范围、原订单范围和演示新单', () => {
  const html = adminView(createVirtualDemo(seed()), { ...ui, page: 'policy' }, { role: 'platform', permitted: true });
  assert.match(html, /成交快照对照|原有洗车次卡订单|新生成订单/);
  const platformOnly = adminView(seed(), { ...ui, page: 'policy', policyDraft: { scope: 'PLATFORM', stores: [] } }, { role: 'platform', permitted: true });
  assert.match(platformOnly, /仅平台后台可核销/);
});
test('三端能够呈现加载、错误、空态及无权限恢复入口', () => {
  for (const viewState of ['loading', 'empty', 'error', 'denied']) {
    assert.match(adminView(seed(), { ...ui, viewState }, { role: 'platform', permitted: viewState !== 'denied' }), /data-view-state="normal"/);
    assert.match(mobileView(seed(), { ...ui, role: 'customer', viewState }, { role: 'customer', permitted: viewState !== 'denied' }), /data-view-state="normal"/);
  }
});
