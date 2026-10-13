// 本地评审模型；所有数据只在内存中变化，不连接商城服务。
export const stores = { sz: '深圳1号前置仓', ns: '南山前置仓', central: '平台总仓' };
export const actionLabels = {
  PAY: '模拟付款成功', CANCEL: '取消订单', PREPARE: '确认备货完成', DISPATCH: '开始配送',
  COMPLETE: '确认送达', PICKUP: '确认提货', SHIP: '快递发货', RECEIVE: '确认收货',
  REDEEM: '核销权益', APPLY_AFTER: '申请售后', REJECT_AFTER: '驳回售后',
  REFUND: '确认退款成功', FAIL_DELIVERY: '记录配送异常', RETRY_DELIVERY: '继续配送',
};
const isVirtual = order => order.kind !== 'PHYSICAL';
const isPhysical = order => order.kind !== 'VIRTUAL';
const unfinished = order => !['DONE', 'CANCELED', 'REFUNDED'].includes(order.stage);
const makeOrder = (id, index, patch) => ({
  id, no: `XS20261012100000${String(index).padStart(6, '0')}`, source: '普通销售',
  name: '精酿啤酒组合', spec: '330ml × 6瓶', qty: 1, amount: 9800, kind: 'PHYSICAL',
  mode: 'DELIVERY', store: 'sz', paid: true, stage: 'PREPARE', inventoryQty: 1,
  redeemed: 0, voided: 0, frozen: 0, expired: false, scope: 'BOTH', scopeStores: ['sz'],
  voucher: String(826410 + index), pickupCode: String(612800 + index), revision: 1,
  date: '2026-12-31', createdAt: '2026-10-12 10:00', physicalDone: false, handedQty: 0,
  address: '深圳市福田区深南大道 · 演示地址', recipient: '陈女士 · 138****6821',
  ...patch,
});

export function seed() {
  const orders = [
    makeOrder('delivery', 1, { qty: 2, inventoryQty: 2, amount: 19600 }),
    makeOrder('pickup', 2, { mode: 'PICKUP', stage: 'READY', address: '深圳1号前置仓 · 福田区演示自提点' }),
    makeOrder('express', 3, { mode: 'EXPRESS', store: null, name: '红葡萄酒双瓶装', spec: '750ml × 2瓶', amount: 21800 }),
    makeOrder('virtual', 4, { kind: 'VIRTUAL', mode: 'REDEEM', store: null, name: '洗车服务次卡', spec: '标准洗车 · 5次', qty: 5, redeemed: 1, inventoryQty: 0, stage: 'REDEEM', amount: 15000 }),
    makeOrder('platform-only', 5, { kind: 'VIRTUAL', mode: 'REDEEM', store: null, name: '线上咨询服务', spec: '30分钟 · 2次', qty: 2, scope: 'PLATFORM', scopeStores: [], inventoryQty: 0, stage: 'REDEEM', amount: 8000 }),
    makeOrder('store-only', 6, { kind: 'VIRTUAL', mode: 'REDEEM', store: null, name: '咖啡兑换券', spec: '美式咖啡 · 3杯', qty: 3, scope: 'STORE', inventoryQty: 0, stage: 'REDEEM', amount: 4500 }),
    makeOrder('expired', 7, { kind: 'VIRTUAL', mode: 'REDEEM', store: null, name: '已到期体验券', spec: '体验服务 · 2次', qty: 2, inventoryQty: 0, stage: 'REDEEM', expired: true, date: '2026-10-11', amount: 6000 }),
    makeOrder('unpaid', 8, { paid: false, stage: 'UNPAID', qty: 2, inventoryQty: 2, amount: 19600 }),
    makeOrder('mixed', 9, { kind: 'MIXED', mode: 'EXPRESS', store: null, name: '礼盒＋品鉴服务', spec: '礼盒1份＋品鉴3次', qty: 1, redeemQty: 3, inventoryQty: 1, amount: 26800 }),
    makeOrder('dining', 10, { source: '餐饮联盟', no: 'CY20261012100000000001', name: '餐桌啤酒组合', stage: 'READY', address: '联盟演示餐厅 · 08号桌', recipient: '餐厅接收 · 139****2508', amount: 9800 }),
    makeOrder('points', 11, { source: '积分兑换', no: 'JF20261012100000000001', mode: 'PICKUP', name: '积分兑换礼品', amount: 0, points: 1200, spec: '标准装 · 1份' }),
    makeOrder('frozen', 12, { kind: 'VIRTUAL', mode: 'REDEEM', store: null, name: '售后中的洗车次卡', spec: '标准洗车 · 5次', qty: 5, redeemed: 1, frozen: 2, inventoryQty: 0, stage: 'REDEEM', amount: 15000 }),
  ];
  return {
    orders, stock: { central: 40, sz: 20, ns: 18 }, reserved: { central: 0, sz: 2, ns: 0 },
    policies: { virtual: { scope: 'BOTH', stores: ['sz'] } }, events: [], receipts: {},
  };
}

export function getOrder(state, id) {
  const order = state.orders.find(item => item.id === id);
  if (!order) throw new Error('订单不存在，请返回列表重新选择。');
  return order;
}

export function remaining(order) {
  return Math.max(0, (order.redeemQty ?? order.qty) - order.redeemed - order.voided - order.frozen);
}

export function physicalRemaining(order) {
  return Math.max(0, order.qty - (order.handedQty || 0) - (order.kind === 'PHYSICAL' ? order.voided + order.frozen : 0));
}

export function warehouseFor(order) {
  if (!isPhysical(order)) return null;
  return order.mode === 'EXPRESS' ? 'central' : order.store;
}

export function scopeLabel(order) {
  const names = order.scopeStores.map(id => stores[id]).join('、');
  return order.scope === 'PLATFORM' ? '仅平台' : `${order.scope === 'BOTH' ? '平台＋' : ''}${names}`;
}

export function statusLabel(order) {
  if (order.stage === 'DONE') return '已完成';
  if (order.stage === 'CANCELED') return '已取消';
  if (order.stage === 'REFUNDED') return '已退款';
  if (!order.paid) return '待付款';
  if (order.kind === 'VIRTUAL') {
    if (order.expired) return '已到期 · 待处理';
    if (remaining(order) === 0 && order.frozen > 0) return '售后处理中';
    return order.redeemed > 0 ? '部分核销' : '待核销';
  }
  if (order.kind === 'MIXED' && order.physicalDone) return '待核销';
  if (order.stage === 'AFTER_DELIVERY') return '已交付 · 售后处理中';
  const labels = { PREPARE: order.mode === 'EXPRESS' ? '待发货' : '待备货', READY: order.mode === 'PICKUP' ? '待自提' : '待配送', DISPATCHED: '配送中', FAILED: '配送异常', SHIPPED: '已发货' };
  return labels[order.stage] || '待处理';
}

function canRedeem(order, actor) {
  if (!isVirtual(order) || !order.paid || !unfinished(order) || order.expired || remaining(order) <= 0) return false;
  if (actor.role === 'platform') return order.scope !== 'STORE';
  return actor.role === 'store' && order.scope !== 'PLATFORM' && order.scopeStores.includes(actor.store);
}

export function visibleOrders(state, actor) {
  if (!actor?.permitted) return [];
  if (['platform', 'customer'].includes(actor.role)) return state.orders;
  if (actor.role !== 'store') return [];
  return state.orders.filter(order =>
    (order.kind === 'PHYSICAL' && order.mode !== 'EXPRESS' && order.store === actor.store) ||
    (isVirtual(order) && order.scope !== 'PLATFORM' && order.scopeStores.includes(actor.store)));
}

export function actionsFor(order, actor) {
  if (!actor?.permitted || !['platform', 'store', 'customer'].includes(actor.role)) return [];
  if (actor.role === 'customer') {
    if (order.stage === 'UNPAID') return ['PAY', 'CANCEL'];
    const receive = order.stage === 'SHIPPED' && !order.physicalDone ? ['RECEIVE'] : [];
    const canAfter = order.paid && unfinished(order) && order.frozen === 0 &&
      (isVirtual(order) ? remaining(order) > 0 : ['PREPARE', 'READY'].includes(order.stage) && physicalRemaining(order) > 0);
    return [...receive, ...(canAfter ? ['APPLY_AFTER'] : [])];
  }
  const after = actor.role === 'platform' && order.frozen > 0 ? ['REFUND', 'REJECT_AFTER'] : [];
  if (!order.paid || !unfinished(order)) return after;
  const redeem = canRedeem(order, actor) ? ['REDEEM'] : [];
  if (order.kind === 'VIRTUAL') return [...redeem, ...after];
  if (physicalRemaining(order) <= 0) return [...redeem, ...after];
  if (order.mode === 'EXPRESS') return [...(actor.role === 'platform' && order.stage === 'PREPARE' ? ['SHIP'] : []), ...redeem, ...after];
  if (actor.role !== 'store' || actor.store !== order.store) return after;
  const step = {
    PREPARE: ['PREPARE'], READY: [order.mode === 'PICKUP' ? 'PICKUP' : 'DISPATCH'],
    DISPATCHED: ['COMPLETE', 'FAIL_DELIVERY'], FAILED: ['RETRY_DELIVERY'],
  };
  return step[order.stage] || [];
}

function requireQuantity(qty, max) {
  if (!Number.isSafeInteger(qty) || qty <= 0 || qty > max) throw new Error(`请输入1至${max}之间的整数数量。`);
}

function physicalPatch(order) {
  const handedQty = (order.handedQty || 0) + physicalRemaining(order);
  const physicalDone = handedQty + (order.kind === 'PHYSICAL' ? order.voided : 0) === order.qty;
  const virtualDone = order.redeemed + order.voided === (order.redeemQty ?? order.qty) && order.frozen === 0;
  const stage = !physicalDone ? 'AFTER_DELIVERY' : order.kind === 'MIXED' && !virtualDone ? 'SHIPPED' : 'DONE';
  return { handedQty, physicalDone, stage };
}

function actionPatch(order, action, payload) {
  const nextStage = { PREPARE: 'READY', DISPATCH: 'DISPATCHED', COMPLETE: 'DONE', PICKUP: 'DONE', RETRY_DELIVERY: 'DISPATCHED', CANCEL: 'CANCELED' };
  if (action === 'PAY') return { paid: true, stage: isPhysical(order) ? 'PREPARE' : 'REDEEM' };
  if (action === 'PICKUP' && payload.code !== order.pickupCode) throw new Error('自提凭证不匹配，请核对顾客订单中的自提码。');
  if (action === 'SHIP') {
    if (!['顺丰速运', '中通快递', '圆通速递', '顺丰'].includes(payload.carrier) ||
      !/^[A-Za-z0-9-]{6,30}$/.test(payload.tracking || '')) throw new Error('请选择快递公司，并填写6至30位字母或数字运单号。');
    return { stage: 'SHIPPED', carrier: payload.carrier, tracking: payload.tracking };
  }
  if (['RECEIVE', 'COMPLETE', 'PICKUP'].includes(action)) return physicalPatch(order);
  if (action === 'FAIL_DELIVERY') {
    if (typeof payload.reason !== 'string' || payload.reason.trim().length < 3 || payload.reason.length > 200) throw new Error('请填写3至200字的配送异常原因。');
    return { stage: 'FAILED', failureReason: payload.reason.trim() };
  }
  if (action === 'REDEEM') {
    if (payload.code !== order.voucher) throw new Error('核销凭证不匹配，请重新核对顾客出示的凭证。');
    requireQuantity(payload.qty, remaining(order));
    const redeemed = order.redeemed + payload.qty;
    const complete = redeemed + order.voided === (order.redeemQty ?? order.qty) && order.frozen === 0;
    return { redeemed, stage: complete && (order.kind !== 'MIXED' || order.physicalDone) ? 'DONE' : order.stage };
  }
  if (action === 'APPLY_AFTER') {
    requireQuantity(payload.qty, isVirtual(order) ? remaining(order) : physicalRemaining(order));
    return { frozen: payload.qty };
  }
  if (action === 'REJECT_AFTER') return { frozen: 0, ...(order.stage === 'AFTER_DELIVERY' ? { stage: 'PREPARE' } : {}) };
  if (action === 'REFUND') {
    const voided = order.voided + order.frozen;
    const exhausted = voided + (order.kind === 'PHYSICAL' ? order.handedQty || 0 : order.redeemed) === (order.redeemQty ?? order.qty);
    return { frozen: 0, voided, stage: exhausted && (order.kind !== 'MIXED' || order.physicalDone) ? (order.redeemed || order.handedQty ? 'DONE' : 'REFUNDED') : order.stage };
  }
  return { stage: nextStage[action] };
}

function inventoryEffect(state, order, action) {
  const warehouse = warehouseFor(order);
  if (!warehouse) return { stock: state.stock, reserved: state.reserved, delta: 0 };
  const qty = order.inventoryQty || order.qty;
  const delta = action === 'PAY' ? -qty : action === 'REFUND' && order.kind === 'PHYSICAL' ? order.frozen : 0;
  if (delta < 0 && state.stock[warehouse] < qty) throw new Error('订单指定仓库存不足，请重新选购；系统不会改扣其他仓库存。');
  const reservedDelta = ['PAY', 'CANCEL'].includes(action) ? -qty : 0;
  return {
    stock: delta ? { ...state.stock, [warehouse]: state.stock[warehouse] + delta } : state.stock,
    reserved: reservedDelta ? { ...state.reserved, [warehouse]: Math.max(0, state.reserved[warehouse] + reservedDelta) } : state.reserved,
    delta,
  };
}

export function transact(state, { id, action, actor, key, payload = {} }) {
  if (!actor?.permitted || !['platform', 'store', 'customer'].includes(actor.role)) throw new Error('当前账号没有此操作权限。');
  if (typeof key !== 'string' || !key.trim()) throw new Error('操作标识缺失，请重新打开操作窗口。');
  const order = getOrder(state, id);
  const request = JSON.stringify([id, action, actor.role, actor.store || null, Object.entries(payload).sort(([a], [b]) => a.localeCompare(b))]);
  if (state.receipts[key]) {
    if (state.receipts[key] !== request) throw new Error('相同操作标识的内容已改变，请先核查原操作结果。');
    return state;
  }
  if (!actionsFor(order, actor).includes(action)) throw new Error('当前履约方、订单状态或售后冻结不允许此操作，请查看最新订单。');
  const patch = actionPatch(order, action, payload);
  const inventory = inventoryEffect(state, order, action);
  const event = {
    id: `${key}-event`, orderId: id, action, actor: actor.role === 'store' ? stores[actor.store] : actor.role === 'platform' ? '平台运营' : '顾客',
    label: actionLabels[action], qty: payload.qty || (action === 'REFUND' ? order.frozen : ['PICKUP', 'COMPLETE', 'RECEIVE'].includes(action) ? physicalRemaining(order) : 0), delta: inventory.delta,
    warehouse: warehouseFor(order), time: new Date().toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }),
  };
  return {
    ...state, stock: inventory.stock, reserved: inventory.reserved,
    orders: state.orders.map(item => item.id === id ? { ...item, ...patch, revision: item.revision + 1 } : item),
    events: [...state.events, event], receipts: { ...state.receipts, [key]: request },
  };
}

export function updatePolicy(state, { scope, stores: enabledStores }) {
  if (!['PLATFORM', 'STORE', 'BOTH'].includes(scope) || !Array.isArray(enabledStores) ||
    enabledStores.some(id => !['sz', 'ns'].includes(id)) || (scope !== 'PLATFORM' && enabledStores.length === 0)) {
    throw new Error('请选择核销范围；允许前置仓核销时，至少选择一个有效前置仓。');
  }
  return { ...state, policies: { ...state.policies, virtual: { scope, stores: scope === 'PLATFORM' ? [] : [...new Set(enabledStores)] } } };
}

export function createVirtualDemo(state) {
  const index = state.orders.length + 1;
  const policy = state.policies.virtual;
  const order = makeOrder(`new-virtual-${index}`, index, {
    kind: 'VIRTUAL', mode: 'REDEEM', store: null, name: '新单 · 洗车服务次卡', spec: '标准洗车 · 5次',
    qty: 5, inventoryQty: 0, stage: 'REDEEM', amount: 15000, scope: policy.scope, scopeStores: [...policy.stores],
  });
  return { ...state, orders: [...state.orders, order] };
}
