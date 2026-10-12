import test from 'node:test';
import assert from 'node:assert/strict';
import { initialState, publishPage, setHome, editPage, saveNavigation, moveNavigation, switchStore, visibleProducts, chooseDelivery, orderActions, amountLines, previewSubmit } from './state.mjs';
import * as model from './state.mjs';

function freeze(value) {
  Object.freeze(value);
  Object.values(value).forEach(item => { if (item && typeof item === 'object' && !Object.isFrozen(item)) freeze(item); });
  return value;
}

test('only a published page can be the unique homepage, with frozen published content', () => {
  const state = freeze(initialState());
  assert.throws(() => setHome(state, 'new'), /发布/);
  const next = setHome(state, 'theme');
  assert.equal(next.homeId, 'theme');
  assert.equal(state.homeId, 'home');
  assert.equal(next.pages.filter(page => page.id === next.homeId).length, 1);
  const edited = editPage(state, 'home', { title: '新的首页标题' });
  assert.equal(edited.pages[0].published.title, '酒娃子精选');
  assert.equal(edited.pages[0].draft.title, '新的首页标题');
  const published = publishPage(edited, 'home');
  assert.equal(published.pages[0].published.title, '新的首页标题');
  assert.equal(published.pages[0].published.revision, published.pages[0].draft.revision);
  assert.throws(() => publishPage(state, 'missing'), /不存在/);
  assert.throws(() => editPage(state, 'missing', {}), /不存在/);
  assert.throws(() => editPage(state, 'new', { title: ' ' }), /名称/);
});

test('navigation supports multiple pages, order changes, max five, and rejects drafts', () => {
  const state = freeze(initialState());
  const nav = [{ id: 'a', label: '首页', type: 'page', target: 'home' }, { id: 'b', label: '精选', type: 'page', target: 'theme' }];
  const saved = saveNavigation(state, nav);
  assert.equal(saved.navigation.length, 2);
  assert.notEqual(saved.navigation, nav);
  assert.deepEqual(moveNavigation(nav, 1, -1).map(item => item.id), ['b', 'a']);
  assert.deepEqual(moveNavigation(nav, 0, -1), nav);
  assert.throws(() => saveNavigation(state, [...nav, ...nav, ...nav]), /最多/);
  assert.throws(() => saveNavigation(state, []), /至少/);
  assert.throws(() => saveNavigation(state, [{ id: 'd', label: '草稿', type: 'page', target: 'new' }]), /未发布/);
  assert.throws(() => saveNavigation(state, [{ id: 'd', label: '', type: 'cart' }]), /名称/);
  assert.throws(() => saveNavigation(state, [{ id: 'd', label: '外链', type: 'external' }]), /类型/);
  assert.throws(() => saveNavigation(state, [nav[0], nav[0]]), /重复/);
  assert.throws(() => saveNavigation(state, 'bad'), /数组/);
  assert.equal(saveNavigation(state, [{ id: 'c', label: '分类', type: 'category' }, { id: 'cart', label: '购物车', type: 'cart' }, { id: 'me', label: '我的', type: 'me' }]).navigation.length, 3);
});

test('restaurant store and table are fixed; products follow the current store', () => {
  const state = freeze(initialState());
  const store = switchStore(state, 'futian');
  assert.equal(store.storeId, 'futian');
  assert.equal(store.cart.length, 0);
  assert.ok(visibleProducts(store).every(product => product.stores.includes('futian')));
  const dining = { ...state, mode: 'dining', storeId: state.dining.storeId };
  assert.throws(() => switchStore(dining, 'futian'), /固定/);
  assert.equal(dining.dining.table, '08');
  assert.ok(visibleProducts(dining).every(product => dining.dining.restaurantAllowedIds.includes(product.id)));
  assert.throws(() => switchStore(state, 'missing'), /不存在/);
});

test('checkout delivery addresses follow fulfillment and restaurant context', () => {
  const state = freeze(initialState());
  assert.equal(chooseDelivery(state, 'pickup').checkout.delivery, 'pickup');
  assert.equal(chooseDelivery(state, 'express').checkout.delivery, 'express');
  assert.equal(chooseDelivery({ ...state, mode: 'dining' }, 'store').checkout.delivery, 'store');
  assert.throws(() => chooseDelivery({ ...state, mode: 'dining' }, 'express'), /餐饮/);
  assert.throws(() => chooseDelivery(state, 'unknown'), /配送/);
});

test('role-specific order actions never expose payment confirmation to customers', () => {
  assert.deepEqual(orderActions('customer', 'PENDING_PAYMENT'), ['付款', '取消订单']);
  assert.deepEqual(orderActions('customer', 'PAID'), ['申请售后']);
  assert.deepEqual(orderActions('customer', 'DELIVERED'), ['确认收货', '申请售后']);
  assert.deepEqual(orderActions('store', 'PAID'), ['备货', '配送交付']);
  assert.deepEqual(orderActions('dining', 'PENDING_PAYMENT'), ['查看订单']);
  assert.deepEqual(orderActions('dining', 'PAID'), ['查看订单', '查看佣金']);
  assert.deepEqual(orderActions('customer', 'CLOSED'), []);
  assert.deepEqual(orderActions('unknown', 'PAID'), []);
});

test('amount rows omit zero deductions; simulated submit creates no order', () => {
  assert.deepEqual(amountLines({ goods: 128, shipping: 0, coupon: 0, points: 0 }).map(item => item.key), ['goods', 'shipping', 'total']);
  const lines = amountLines({ goods: 128, shipping: 5, coupon: 10, points: 2 });
  assert.equal(lines.at(-1).amount, 121);
  assert.equal(lines.find(item => item.key === 'coupon').amount, -10);
  const state = freeze(initialState());
  const next = previewSubmit(state);
  assert.equal(next.previewNotice, '已走通确认交互；未创建订单、未扣库存、未发起支付。');
  assert.deepEqual(next.orders, state.orders);
  assert.equal(state.previewNotice, '');
});

test('order details use independent snapshots despite checkout, store, or quantity changes', () => {
  const state = freeze(initialState());
  const order = model.orderSnapshot(state);
  assert.equal(order.lines[0].quantity, 1);
  assert.equal(order.amounts.goods, 68);
  assert.equal(order.payment, 'offline');
  assert.match(order.paymentDeadline, /^2026-10-12 \d{2}:\d{2}$/);
  const changed = { ...state, checkout: { ...state.checkout, goods: 999, coupon: 20, payment: 'wechat', address: '不同地址' }, cart: [{ productId: 'beer', quantity: 8 }] };
  assert.deepEqual(model.orderSnapshot(switchStore(changed, 'futian')), order);
  const dining = model.orderSnapshot({ ...changed, mode: 'dining' });
  assert.equal(dining.dining.table, '08');
  assert.equal(dining.store.name, '南山前置仓');
});

test('navigation drafts retain unpublished targets while publishing checks them and SVG icons', () => {
  const state = freeze(initialState());
  const draft = [{ id: 'a', label: '秋日', type: 'page', target: 'new', icon: 'box' }];
  const saved = model.saveNavigationDraft(state, draft);
  assert.deepEqual(saved.navigation, state.navigation);
  assert.deepEqual(saved.navigationDraft, draft);
  assert.notEqual(saved.navigationDraft[0], draft[0]);
  assert.throws(() => saveNavigation(saved, saved.navigationDraft), /未发布/);
  const ready = publishPage(saved, 'new');
  const published = saveNavigation(ready, ready.navigationDraft);
  assert.equal(published.navigation[0].icon, 'box');
  assert.equal(published.navigation[0].target, 'new');
  assert.throws(() => model.saveNavigationDraft(state, [{ ...draft[0], icon: 'uncontrolled' }]), /图标/);
  assert.equal(model.saveNavigationDraft(state, [{ id: 'a', label: '首页', type: 'page', target: '@home', icon: 'home' }]).navigationDraft[0].target, '@home');
});

test('clearing the final cart row exits management, empty carts have no management state', () => {
  const state = freeze(initialState());
  const result = model.removeCartItem(state, 'beer', true);
  assert.equal(result.state.cart.length, 0);
  assert.equal(result.managing, false);
  assert.equal(state.cart.length, 1);
  const two = { ...state, cart: [...state.cart, { productId: 'wine', quantity: 1 }] };
  assert.equal(model.removeCartItem(two, 'wine', true).managing, true);
});

test('store and dining carts are isolated, restored, and never mutate earlier contexts', () => {
  const initial = freeze(initialState());
  const nanshan = freeze({ ...initial, cart: [{ productId: 'beer', quantity: 4, selected: true }] });
  const futian = model.switchStore(nanshan, 'futian');
  assert.equal(futian.cart.length, 0);
  const stockedFutian = freeze({ ...futian, cart: [{ productId: 'gift', quantity: 2, selected: true }] });
  const restoredNanshan = model.switchStore(stockedFutian, 'nanshan');
  assert.deepEqual(restoredNanshan.cart, nanshan.cart);
  assert.notEqual(restoredNanshan.cart, nanshan.cart);
  assert.notEqual(restoredNanshan.cart[0], nanshan.cart[0]);
  assert.equal(model.switchStore(restoredNanshan, 'futian').cart[0].quantity, 2);
  const dining = model.switchMode(restoredNanshan, 'dining');
  assert.equal(dining.cart.length, 0);
  const stockedDining = freeze({ ...dining, cart: [{ productId: 'beer', quantity: 3, selected: true }] });
  const ordinary = model.switchMode(stockedDining, 'store');
  assert.equal(ordinary.cart[0].quantity, 4);
  assert.equal(model.switchMode(ordinary, 'dining').cart[0].quantity, 3);
  assert.deepEqual(initial.cart, [{ productId: 'beer', quantity: 1, selected: true }]);
  assert.deepEqual(model.orderSnapshot(ordinary), model.orderSnapshot(initial));
  assert.throws(() => model.switchMode(initial, 'unknown'), /场景/);
});

test('selected and unselected SVG icons save independently with validated defaults', () => {
  const state = freeze(initialState());
  const nav = [{ id: 'custom', label: '首页', type: 'page', target: '@home', icon: 'page', activeIcon: 'wallet-filled' }];
  const draft = model.saveNavigationDraft(state, nav);
  assert.equal(draft.navigationDraft[0].icon, 'page');
  assert.equal(draft.navigationDraft[0].activeIcon, 'wallet-filled');
  assert.deepEqual(draft.navigation, state.navigation);
  const published = saveNavigation(draft, draft.navigationDraft);
  assert.equal(published.navigation[0].icon, 'page');
  assert.equal(published.navigation[0].activeIcon, 'wallet-filled');
  const different = model.saveNavigationDraft(published, [{ ...nav[0], activeIcon: 'home-filled' }]);
  assert.equal(different.navigationDraft[0].icon, 'page');
  assert.equal(published.navigation[0].activeIcon, 'wallet-filled');
  assert.throws(() => model.saveNavigationDraft(state, [{ ...nav[0], activeIcon: 'unsafe' }]), /图标/);
  assert.deepEqual(model.navigationIcons({ type: 'cart' }), { icon: 'cart', activeIcon: 'cart-filled' });
  assert.deepEqual(model.navigationIcons({ type: 'page', target: '@home', icon: 'box' }), { icon: 'box', activeIcon: 'box-filled' });
});

test('category default, primary switching, secondary banner, and actual name search share state', () => {
  const state = freeze(initialState());
  const first = model.categoryView(state);
  assert.equal(first.primary.id, 'liquor');
  assert.equal(first.secondary.id, 'beer');
  assert.equal(first.secondary.image, 'assets/beer-banner.png');
  assert.ok(first.products.length > 0);
  const whiteWine = model.categoryView(state, { primaryId: 'liquor', secondaryId: 'baijiu' });
  assert.equal(whiteWine.products[0].id, 'wine');
  assert.equal(whiteWine.secondary.image, 'assets/baijiu-banner.png');
  const computer = model.categoryView(state, { primaryId: 'electronics', secondaryId: 'beer' });
  assert.equal(computer.secondary.id, 'computer');
  assert.equal(computer.secondary.image, 'assets/computer-banner.png');
  assert.equal(model.categoryView(state, { query: ' 麦野 ' }).products.length, 1);
  assert.equal(model.categoryView(state, { query: '不存在' }).products.length, 0);
  assert.equal(model.categoryView(state, { primaryId: 'missing' }).primary.id, 'liquor');
  assert.equal(model.categoryView(state, { primaryId: 'daily' }).secondary.image, '');
});


test('horizontal category selection keeps visible items in place and reveals offscreen items', () => {
  assert.equal(model.categoryScrollPosition(0, 400, 72, 320, 600), 152);
  assert.equal(model.categoryScrollPosition(100, 0, 72, 320, 600), 0);
  assert.equal(model.categoryScrollPosition(50, 100, 72, 320, 600), 50);
  assert.equal(model.categoryScrollPosition(0, 20, 72, 320, 200), 0);
  assert.equal(model.categoryScrollPosition(300, 550, 72, 320, 600), 280);
});
