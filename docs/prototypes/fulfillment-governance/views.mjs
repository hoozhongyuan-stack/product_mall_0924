import { stores, actionLabels, warehouseFor, statusLabel, remaining, scopeLabel } from './state.mjs';

export const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
export const money = cents => `¥${(cents / 100).toFixed(2)}`;
export const modeNames = { DELIVERY: '配送到家', PICKUP: '到仓自提', EXPRESS: '快递发货', REDEEM: '权益核销' };
export const scopeNames = { PLATFORM: '仅平台', STORE: '指定前置仓', BOTH: '平台＋指定前置仓' };
const paths = {
  orders: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 2h6v4H9zM9 11h6M9 15h6"/>',
  store: '<path d="M3 10h18l-2-6H5zM5 10v10h14V10M9 20v-6h6v6"/>',
  back: '<path d="m15 5-7 7 7 7"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  box: '<path d="m3 7 9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10M7 5l10 4"/>',
  ticket: '<path d="M3 7h18v4a2 2 0 0 0 0 4v4H3v-4a2 2 0 0 0 0-4zM15 7v3m0 3v2m0 2v2"/>',
  empty: '<path d="M4 8h16v12H4zM7 8V4h10v4M8 13h8M10 17h4"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 14v3"/>',
};
export const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.box}</svg>`;
export const tag = (text, tone = '') => `<span class="tag ${tone}">${esc(text)}</span>`;
export const actionButton = (action, order, primary = false) => `<button class="${primary ? 'primary' : 'secondary'}" data-action="${action}" data-id="${esc(order.id)}">${actionLabels[action]}</button>`;
export const orderAmount = order => order.points ? `${order.points} 积分` : money(order.amount);
export const ownerLabel = order => order.kind === 'VIRTUAL' ? scopeLabel(order) : order.mode === 'EXPRESS' ? '平台履约' : stores[order.store];
export const statusTag = order => tag(statusLabel(order), order.stage === 'DONE' ? 'good' : order.expired || order.frozen || order.stage === 'FAILED' ? 'warn' : 'brand');

export function product(order) {
  return `<div class="product"><div class="product-mark">${icon(order.kind === 'VIRTUAL' ? 'ticket' : 'box')}</div><div class="product-info"><strong>${esc(order.name)}</strong><small>${esc(order.spec)}</small><small>${order.kind === 'VIRTUAL' ? '购买数量' : '商品数量'}：${order.qty}${order.kind === 'MIXED' ? ` · 服务${order.redeemQty}次` : ''}</small>${order.kind === 'PHYSICAL' && (order.handedQty || order.frozen || order.voided) ? `<small>已交付${order.handedQty || 0}件 · 售后冻结${order.frozen}件 · 已退款${order.voided}件</small>` : ''}</div><div>${orderAmount(order)}</div></div>`;
}

export function explanation(order) {
  if (!order.paid && order.stage === 'UNPAID') return '付款成功后开始备货。未付款库存仅占用，取消后释放。';
  if (order.stage === 'CANCELED') return '订单已取消，原库存占用已释放。';
  if (order.stage === 'REFUNDED') return '退款处理已完成，对应商品或权益已关闭。';
  if (order.stage === 'DONE') return '本订单的有效履约义务已完成。';
  if (order.expired) return '权益已到期，无法继续核销。剩余权益可申请售后，不自动视为消费完成。';
  if (order.stage === 'FAILED') return `配送暂未完成：${order.failureReason || '请联系履约前置仓确认后续配送安排。'}`;
  if (order.kind === 'VIRTUAL') return '出示本订单的核销凭证；每次核销后，平台和前置仓同步剩余次数。';
  if (order.kind === 'MIXED') return '礼盒由平台快递发货；品鉴服务按授权范围核销，两项均结束后整单完成。';
  const messages = {
    PREPARE: order.mode === 'EXPRESS' ? '平台正在安排发货，发货后可查看运单。' : '前置仓正在备货，请留意履约进度。',
    READY: order.mode === 'PICKUP' ? '商品已备好，请在自提点出示自提凭证。' : '商品已备好，前置仓即将安排配送。',
    DISPATCHED: '前置仓正在配送，请保持接收电话畅通。', SHIPPED: '商品已通过快递寄出，收到商品后请确认收货。',
  };
  return messages[order.stage] || '请查看订单最新进度。';
}

export function steps(order) {
  if (order.kind === 'VIRTUAL') return '';
  const route = order.mode === 'EXPRESS' ? [['UNPAID', '待付款'], ['PREPARE', '待发货'], ['SHIPPED', '已发货'], ['DONE', '已收货']] :
    order.mode === 'PICKUP' ? [['UNPAID', '待付款'], ['PREPARE', '待备货'], ['READY', '待自提'], ['DONE', '已提货']] :
      [['UNPAID', '待付款'], ['PREPARE', '待备货'], ['READY', '待配送'], ['DISPATCHED', '配送中'], ['DONE', '已送达']];
  const stage = order.physicalDone ? 'DONE' : order.stage === 'FAILED' ? 'DISPATCHED' : order.stage;
  const position = route.findIndex(([key]) => key === stage);
  return `<ol class="steps" aria-label="履约进度">${route.map(([key, text], index) => `<li class="${index < position ? 'done' : index === position ? 'current' : ''}">${text}</li>`).join('')}</ol>`;
}

export function timeline(state, order, customer = false) {
  const records = state.events.filter(event => event.orderId === order.id).toReversed();
  return `<ul class="timeline">${records.map(event => `<li>${esc(event.label)}${event.qty ? ` · ${event.qty}${order.kind === 'PHYSICAL' ? '件' : '次'}` : ''}<small>${esc(event.time)} · ${customer && event.actor === '顾客' ? '本人' : esc(event.actor)}${!customer && event.delta ? ` · ${stores[event.warehouse]}库存${event.delta > 0 ? '+' : ''}${event.delta}` : ''}</small></li>`).join('')}<li>${order.paid ? order.points ? '积分兑换成功' : '付款已确认' : '订单已创建'}<small>${esc(order.createdAt)}${customer ? '' : order.paid && order.kind !== 'VIRTUAL' ? ` · ${stores[warehouseFor(order)]}已扣销售库存` : ''}</small></li></ul>`;
}

export function inventory(state, order) {
  const warehouse = warehouseFor(order);
  if (!warehouse) return `<div class="notice">该订单为虚拟权益。核销消耗剩余次数，不扣前置仓实物库存。</div>`;
  return `<div class="ledger-row row"><span>扣库仓</span><b>${stores[warehouse]}</b></div><div class="ledger-row row"><span>销售账面余量</span><b>${state.stock[warehouse]}</b></div><div class="ledger-row row"><span>待付款占用</span><b>${state.reserved[warehouse]}</b></div><div class="ledger-row row"><span>当前可售</span><b>${state.stock[warehouse] - state.reserved[warehouse]}</b></div><p class="muted">数量为演示库存池的基础单位。付款时正式扣减，交付不再扣库。</p>`;
}

export function stateScreen(kind, phone = false) {
  if (kind === 'loading') return `<section class="${phone ? 'mobile-section' : 'panel'}" aria-busy="true" aria-label="订单加载中"><div class="skeleton large"></div><div class="skeleton"></div><div class="skeleton"></div><p class="muted">正在读取订单，请稍候…</p><button data-view-state="normal">演示加载完成</button></section>`;
  const variants = {
    empty: ['empty', '暂无符合条件的订单', '试试其他筛选条件，或返回全部订单。', '返回全部订单'],
    error: ['empty', '订单暂时读取失败', '请检查网络后重试，已完成的操作不会因读取失败被撤销。', '重新加载'],
    denied: ['lock', '暂无订单操作权限', '请联系管理员授权；当前不展示私人订单与凭证。', '恢复授权演示'],
  };
  const [symbol, title, description, action] = variants[kind];
  return `<section class="${phone ? 'mobile-section' : 'panel'} empty-state">${icon(symbol)}<h3>${title}</h3><p>${description}</p><button data-view-state="normal">${action}</button></section>`;
}

export function virtualNumbers(order) {
  return `<div class="voucher-stats"><div><b>${order.redeemQty ?? order.qty}</b>购买</div><div><b>${order.redeemed}</b>已核销</div><div><b>${order.voided}</b>已作废</div><div><b>${order.frozen}</b>售后冻结</div></div><div class="available"><span>当前可核销${order.expired ? '（已到期）' : ''}</span><b>${order.expired ? 0 : remaining(order)} 次</b></div>`;
}

export function stateSelector(value) {
  return `<label class="field">界面状态<select id="view-state" aria-label="界面状态">${[['normal', '正常'], ['loading', '加载中'], ['empty', '空列表'], ['error', '网络错误'], ['denied', '无权限']].map(([key, text]) => `<option value="${key}" ${key === value ? 'selected' : ''}>${text}</option>`).join('')}</select></label>`;
}
