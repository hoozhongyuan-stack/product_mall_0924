import { seed, getOrder, transact, actionsFor, updatePolicy, createVirtualDemo, actionLabels, remaining, physicalRemaining, stores, scopeLabel, warehouseFor } from './state.mjs';
import { esc, icon } from './views.mjs';
import { adminView } from './admin.mjs';
import { mobileView } from './mobile.mjs';

let state = seed();
let ui = { role: 'platform', page: 'list', selected: 'delivery', store: 'sz', viewState: 'normal', task: 'ALL', storeTask: 'ALL', mode: 'ALL', owner: 'ALL', search: '', pagination: 1, policyDraft: null };
let pending = null;
let toastTimer;
const app = document.querySelector('#app');
const dialog = document.querySelector('#action-dialog');
const actor = () => ({ role: ui.role, store: ui.role === 'store' ? ui.store : undefined, permitted: ui.viewState !== 'denied' });

function toast(message) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove('visible'), 3200);
}

function render() {
  document.querySelector('#role-tabs').innerHTML = [['platform', '平台后台'], ['store', '前置仓中心'], ['customer', '顾客订单详情']].map(([key, name]) => `<button class="${ui.role === key ? 'active' : ''}" aria-pressed="${ui.role === key}" data-role="${key}">${name}</button>`).join('');
  app.innerHTML = ui.role === 'platform' ? adminView(state, ui, actor()) : mobileView(state, ui, actor());
}

function setUi(patch) { ui = { ...ui, ...patch }; render(); }

function switchRole(role, id) {
  const order = getOrder(state, id || ui.selected);
  const store = role === 'store' ? order.store || order.scopeStores[0] || ui.store : ui.store;
  setUi({ role, selected: order.id, store, page: id ? 'detail' : role === 'customer' ? 'detail' : 'list', viewState: 'normal' });
  window.scrollTo({ top: 0 });
}

function dialogFields(action, order) {
  const note = `<div class="notice">${warehouseFor(order) ? `交付不重复扣库存；本订单扣库仓为${stores[warehouseFor(order)]}。` : '本次核销消耗订单权益，不扣实物库存。'}</div>`;
  if (action === 'SHIP') return `<label class="field">快递公司<select name="carrier" required><option value="">请选择快递公司</option>${['顺丰速运', '中通快递', '圆通速递'].map(value => `<option>${value}</option>`).join('')}</select></label><label class="field">运单号<input name="tracking" required minlength="6" maxlength="30" pattern="[A-Za-z0-9-]{6,30}" placeholder="6至30位字母或数字"></label>${note}`;
  if (['REDEEM', 'PICKUP'].includes(action)) return `<label class="field">${action === 'REDEEM' ? '核销凭证' : '自提凭证'}<input name="code" inputmode="numeric" autocomplete="off" required pattern="[0-9]{6}" maxlength="6" placeholder="输入顾客出示的6位演示码"></label><button type="button" id="verify-code">核验凭证</button><div id="verification" class="verified" role="status"></div>${action === 'REDEEM' ? `<label class="field">本次核销数量<input name="qty" type="number" inputmode="numeric" required min="1" max="${remaining(order)}" step="1" value="1"><small>当前可核销${remaining(order)}次，已扣除售后冻结数量。</small></label>` : `<p class="muted" style="margin-top:12px">本次交付${physicalRemaining(order)}件${order.frozen ? `，另有${order.frozen}件售后冻结，不在本次交付内` : ''}。</p>`}${note}`;
  if (action === 'APPLY_AFTER') return `<label class="field">申请数量<input name="qty" type="number" inputmode="numeric" required min="1" max="${order.kind === 'PHYSICAL' ? physicalRemaining(order) : remaining(order)}" value="1" step="1"></label><label class="field">申请原因<textarea name="reason" required minlength="3" maxlength="200" placeholder="请填写申请原因"></textarea></label><div class="notice warn">提交后冻结对应数量，等待平台审核。${order.kind === 'MIXED' ? '本示例售后针对未核销服务次数。' : order.kind === 'PHYSICAL' ? '本示例仅演示未交付退款。' : '已核销部分须另行人工审核，不自动恢复次数。'}</div>`;
  if (action === 'FAIL_DELIVERY') return '<label class="field">配送异常原因<textarea name="reason" required minlength="3" maxlength="200" placeholder="例如顾客暂时无法接收"></textarea></label><div class="notice warn">记录异常不会标记已送达，后续可以继续配送。</div>';
  if (action === 'REFUND') return `<div class="notice warn">确认${order.frozen}${order.kind === 'PHYSICAL' ? '件商品' : '次未核销权益'}的退款已实际成功。${order.kind === 'PHYSICAL' ? `对应数量回补${stores[warehouseFor(order)]}。` : '对应次数作废，已核销记录保留。'}</div><p class="muted">此按钮仅模拟成功事实，不发起真实退款或资金操作。</p>`;
  if (action === 'REJECT_AFTER') return `<div class="notice">驳回后释放${order.frozen}${order.kind === 'PHYSICAL' ? '件商品' : '次权益'}的售后冻结，可恢复符合条件的履约。</div>`;
  if (action === 'PAY') return `<div class="notice">模拟可信付款成功，正式扣减${stores[warehouseFor(order)]}库存${order.inventoryQty}个基础单位。原库存占用同时释放。</div>`;
  if (action === 'CANCEL') return '<div class="notice">取消未付款订单，只释放原仓库存占用，不增加销售账面库存。</div>';
  return `<p>${{ PREPARE: '请确认本单可履约商品已备齐。', DISPATCH: '请确认可履约商品已交给配送人员。', COMPLETE: '请确认本次配送商品已实际送达顾客。', RECEIVE: '请确认本次快递商品已收到。', RETRY_DELIVERY: '继续配送，本单仍须实际送达后才完成。' }[action]}</p>${order.kind === 'PHYSICAL' ? `<p class="muted">本次可交付${physicalRemaining(order)}件${order.frozen ? `，另有${order.frozen}件售后冻结` : ''}。</p>` : ''}${note}`;
}

function openAction(action, id) {
  const order = getOrder(state, id);
  if (!actionsFor(order, actor()).includes(action)) return toast('当前账号或订单状态不允许此操作。');
  pending = { id, action, actor: { ...actor() }, key: crypto.randomUUID(), revision: order.revision };
  dialog.innerHTML = `<form id="action-form"><div class="dialog-heading"><h2 id="dialog-title">${actionLabels[action]}</h2><button type="button" data-close aria-label="关闭操作窗口">${icon('close')}</button></div><div class="dialog-facts"><strong>${esc(order.name)}</strong><small class="break">${esc(order.no)}</small><small>履约／核销方：${order.kind === 'VIRTUAL' ? esc(scopeLabel(order)) : order.mode === 'EXPRESS' ? '平台' : stores[order.store]}</small></div>${dialogFields(action, order)}<p class="dialog-error" id="dialog-error" role="alert"></p><div class="dialog-actions"><button type="button" data-close>取消</button><button class="primary" id="confirm-action">${actionLabels[action]}</button></div></form>`;
  dialog.showModal();
}

function closeDialog() { dialog.close(); pending = null; }

function verifyCode() {
  const order = getOrder(state, pending.id);
  const code = dialog.querySelector('[name=code]').value;
  const expected = pending.action === 'REDEEM' ? order.voucher : order.pickupCode;
  const error = dialog.querySelector('#dialog-error');
  const preview = dialog.querySelector('#verification');
  if (code !== expected) {
    error.textContent = '凭证不匹配，请核对顾客出示的订单凭证。';
    preview.textContent = '';
    return;
  }
  error.textContent = '';
  preview.textContent = pending.action === 'REDEEM' ? `凭证有效，当前可核销${remaining(order)}次。请确认本次使用数量。` : `凭证有效，可交付${physicalRemaining(order)}件商品。`;
}

function submitAction(event) {
  event.preventDefault();
  const form = event.target;
  if (!form.checkValidity()) return form.reportValidity();
  const values = Object.fromEntries(new FormData(form));
  const payload = { ...values, ...(values.qty !== undefined ? { qty: Number(values.qty) } : {}) };
  try {
    if (getOrder(state, pending.id).revision !== pending.revision) throw new Error('订单状态已变化，请关闭窗口后重新核对。');
    state = transact(state, { ...pending, payload });
    const label = actionLabels[pending.action];
    ui = { ...ui, selected: pending.id };
    closeDialog();
    render();
    toast(`${label}已记录，三端状态已同步。`);
  } catch (error) { dialog.querySelector('#dialog-error').textContent = error.message; }
}

function savePolicy() {
  try {
    state = updatePolicy(state, ui.policyDraft || state.policies.virtual);
    ui = { ...ui, policyDraft: null };
    render();
    toast('演示配置已保存；已有订单的核销范围保持不变。');
  } catch (error) { toast(error.message); }
}

document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  const data = button.dataset;
  if (data.role) return switchRole(data.role);
  if (data.toRole) return switchRole(data.toRole, data.id);
  if (data.open) return setUi({ selected: data.open, page: 'detail', viewState: 'normal' });
  if ('back' in data) return setUi({ page: 'list' });
  if (data.adminPage) return setUi({ page: data.adminPage, viewState: 'normal' });
  if (data.task) return setUi({ task: data.task, pagination: 1 });
  if (data.storeTask) return setUi({ storeTask: data.storeTask, page: 'list' });
  if (data.pagination) return setUi({ pagination: Number(data.pagination) });
  if (data.viewState) return setUi({ viewState: data.viewState, search: '', mode: 'ALL', owner: 'ALL', task: 'ALL', storeTask: 'ALL', pagination: 1 });
  if (data.action) return openAction(data.action, data.id);
  if ('close' in data) return closeDialog();
  if (data.scope) return setUi({ policyDraft: { ...(ui.policyDraft || state.policies.virtual), scope: data.scope } });
  if (button.id === 'verify-code') return verifyCode();
  if (button.id === 'save-policy') return savePolicy();
  if (button.id === 'new-virtual') {
    state = createVirtualDemo(state);
    ui = { ...ui, selected: state.orders.at(-1).id };
    render();
    return toast('演示新单已生成，使用已保存的核销范围。');
  }
  if (button.id === 'reset') {
    state = seed();
    ui = { ...ui, page: ui.role === 'customer' ? 'detail' : 'list', selected: 'delivery', store: 'sz', policyDraft: null, task: 'ALL', storeTask: 'ALL', mode: 'ALL', owner: 'ALL', search: '', pagination: 1, viewState: 'normal' };
    render();
    toast('演示已重置，所有订单和库存恢复初始值。');
  }
});

document.addEventListener('change', event => {
  const element = event.target;
  if (element.id === 'view-state') return setUi({ viewState: element.value });
  if (element.id === 'scenario-select' && element.value) return setUi({ selected: element.value, page: 'detail', viewState: 'normal' });
  if (element.id === 'store-select') return setUi({ store: element.value, page: 'list', storeTask: 'ALL' });
  if (element.dataset.policyStore) {
    const draft = ui.policyDraft || state.policies.virtual;
    const enabledStores = element.checked ? [...draft.stores, element.dataset.policyStore] : draft.stores.filter(id => id !== element.dataset.policyStore);
    return setUi({ policyDraft: { ...draft, stores: enabledStores } });
  }
  if (element.name === 'code') dialog.querySelector('#verification').textContent = '';
});

document.addEventListener('submit', event => {
  if (event.target.id === 'action-form') return submitAction(event);
  if (event.target.id === 'filters') {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    setUi({ search: values.search.trim(), mode: values.mode, owner: values.owner, pagination: 1 });
  }
});
dialog.addEventListener('cancel', () => { pending = null; });
render();
