import { actionsFor, visibleOrders, stores, scopeLabel, remaining } from './state.mjs';
import { esc, tag, icon, actionButton, orderAmount, ownerLabel, statusTag, product, explanation, steps, timeline, inventory, stateScreen, virtualNumbers, stateSelector, modeNames, scopeNames } from './views.mjs';

function listView(state, ui, actor) {
  const all = visibleOrders(state, actor);
  const categories = [
    ['ALL', '全部订单', () => true],
    ['SHIP', '待平台发货', order => actionsFor(order, actor).includes('SHIP')],
    ['REDEEM', '待平台核销', order => actionsFor(order, actor).includes('REDEEM')],
    ['AFTER', '售后处理中', order => order.frozen > 0],
  ];
  const condition = categories.find(([key]) => key === ui.task)?.[2] || categories[0][2];
  const filtered = all.filter(order => condition(order) &&
    (ui.mode === 'ALL' || order.mode === ui.mode) &&
    (ui.owner === 'ALL' || (ui.owner === 'PLATFORM' ? order.mode === 'EXPRESS' || order.kind === 'VIRTUAL' && order.scope !== 'STORE' : order.store === ui.owner || order.kind !== 'PHYSICAL' && order.scope !== 'PLATFORM' && order.scopeStores.includes(ui.owner))) &&
    (!ui.search || `${order.no} ${order.name} ${order.source}`.includes(ui.search)));
  const pages = Math.max(1, Math.ceil(filtered.length / 6));
  const page = Math.min(ui.pagination, pages);
  const rows = filtered.slice((page - 1) * 6, page * 6);
  return `<div class="heading"><div><h1>订单管理</h1><p>统一查看订单，按履约职责提供操作。</p></div><span>${tag('平台运营', 'blue')}</span></div>
    <div class="notice">配送、自提由对应前置仓中心执行；平台后台负责快递发货、授权虚拟核销和资金售后。</div>
    <section class="panel"><div class="task-tabs">${categories.map(([key, text, filter]) => `<button class="${ui.task === key ? 'active' : ''}" data-task="${key}">${text}<b>${all.filter(filter).length}</b></button>`).join('')}</div>
    <form id="filters" class="filters"><label class="field">订单搜索<input name="search" value="${esc(ui.search)}" placeholder="订单号、商品名称或订单来源"></label>
    <label class="field">交付方式<select name="mode"><option value="ALL">全部方式</option>${Object.entries(modeNames).map(([key, value]) => `<option value="${key}" ${ui.mode === key ? 'selected' : ''}>${value}</option>`).join('')}</select></label>
    <label class="field">履约归属<select name="owner">${[['ALL', '全部履约方'], ['PLATFORM', '平台履约／核销'], ['sz', '深圳1号前置仓']].map(([key, value]) => `<option value="${key}" ${ui.owner === key ? 'selected' : ''}>${value}</option>`).join('')}</select></label><button class="primary">查询</button></form>
    ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>订单 / 商品</th><th>来源</th><th>交付方式</th><th>履约方 / 扣库仓</th><th>状态</th><th>实付</th><th>操作</th></tr></thead><tbody>${rows.map(order => {
      const actions = actionsFor(order, actor);
      const primary = actions.find(action => ['SHIP', 'REDEEM', 'REFUND'].includes(action));
      return `<tr><td><strong>${esc(order.name)}</strong><small>${esc(order.no)}</small></td><td>${tag(order.source)}</td><td>${modeNames[order.mode]}${order.kind === 'MIXED' ? '<small>＋权益核销</small>' : ''}</td><td>${esc(ownerLabel(order))}<small>${order.kind === 'VIRTUAL' ? '共享剩余权益' : order.mode === 'EXPRESS' ? '扣平台总仓' : '扣本前置仓'}</small></td><td>${statusTag(order)}${order.frozen ? `<small>售后冻结${order.frozen}${order.kind === 'PHYSICAL' ? '件' : '次'}</small>` : ''}</td><td>${orderAmount(order)}</td><td><button class="text-button" data-open="${order.id}">详情</button>${primary ? actionButton(primary, order).replace('class="secondary"', 'class="text-button"') : ''}</td></tr>`;
    }).join('')}</tbody></table></div><div class="table-foot"><span>共 ${filtered.length} 笔订单 · 每页6笔</span><div class="row"><button class="small" data-pagination="${page - 1}" ${page === 1 ? 'disabled' : ''}>上一页</button><span>${page} / ${pages}</span><button class="small" data-pagination="${page + 1}" ${page === pages ? 'disabled' : ''}>下一页</button></div></div>` : stateScreen('empty')}
    </section>`;
}

function actionPanel(state, order, actor) {
  const actions = actionsFor(order, actor);
  return `<section class="panel action-box"><h2>当前可操作</h2>${order.kind === 'PHYSICAL' && order.mode !== 'EXPRESS' ? `<div class="notice">由${stores[order.store]}中心完成备货和交付。</div>` : ''}${order.kind !== 'PHYSICAL' && order.scope === 'STORE' ? '<div class="notice">该权益仅允许指定前置仓核销。</div>' : ''}
    ${order.frozen ? `<div class="notice warn">有${order.frozen}${order.kind === 'PHYSICAL' ? '件商品' : '次权益'}正在售后。冻结部分不可继续履约。</div>` : ''}
    ${actions.length ? actions.map((action, index) => actionButton(action, order, index === 0)).join('') : `<p class="muted">${order.expired ? '权益已到期，可通过售后处理剩余数量。' : '当前没有平台履约动作。'}</p>`}
    <button class="secondary" data-to-role="customer" data-id="${order.id}">查看顾客订单详情</button></section>
    <section class="panel"><h2>库存归属</h2>${inventory(state, order)}</section>`;
}

function detailView(state, ui, actor) {
  const order = state.orders.find(item => item.id === ui.selected);
  return `<button class="text-button" data-back>${icon('back')} 返回订单列表</button><div class="heading"><div><h1>${esc(order.name)}</h1><p class="break">${esc(order.no)}</p></div><div class="row wrap">${tag(order.source)}${statusTag(order)}</div></div>
    <div class="details-layout"><section class="panel">
    <div class="section"><h2>履约信息</h2>${steps(order)}<dl class="facts"><div><dt>交付方式</dt><dd>${modeNames[order.mode]}${order.kind === 'MIXED' ? '＋权益核销' : ''}</dd></div><div><dt>履约方</dt><dd>${esc(ownerLabel(order))}</dd></div><div><dt>支付状态</dt><dd>${order.paid ? order.points ? '积分已扣除' : '已确认收款' : '待付款'}</dd></div><div><dt>交付地点</dt><dd>${order.kind === 'VIRTUAL' ? '按核销范围提供服务' : esc(order.address)}</dd></div></dl><div class="notice">${esc(explanation(order))}</div>${order.tracking ? `<p>快递：${esc(order.carrier)} · 运单：${esc(order.tracking)}</p>` : ''}</div>
    <div class="section"><h2>商品与金额</h2>${product(order)}<div class="row"><span>订单实付</span><span class="price">${orderAmount(order)}</span></div><p class="muted">${order.mode === 'EXPRESS' ? '平台快递按平台交易规则结算，不自动产生前置仓履约收益。' : order.kind === 'VIRTUAL' ? '虚拟服务结算独立于实物进货成本分润。' : '前置仓成本、运费与分润沿用成交时保存的规则。'}</p></div>
    ${order.kind !== 'PHYSICAL' ? `<div class="section"><h2>核销权益</h2><p class="muted">核销范围：${esc(scopeLabel(order))} · 有效至 ${order.date}</p>${virtualNumbers(order)}<p class="muted">核销凭证由顾客出示，后台通过输入凭证核验。</p></div>` : ''}
    <div class="section"><h2>操作记录</h2>${timeline(state, order)}</div></section><aside>${actionPanel(state, order, actor)}</aside></div>`;
}

function policyView(state, ui) {
  const draft = ui.policyDraft || state.policies.virtual;
  const old = state.orders.find(item => item.id === 'virtual');
  return `<div class="heading"><div><h1>虚拟商品核销范围</h1><p>商品配置影响新成交订单；已有订单保留原核销范围。</p></div>${tag('洗车服务次卡', 'blue')}</div>
    <div class="policy-layout"><section class="panel"><h2>允许在哪里核销</h2><div class="scope-options">${Object.entries(scopeNames).map(([key, value]) => `<button class="${draft.scope === key ? 'active' : ''}" data-scope="${key}">${value}</button>`).join('')}</div>
    ${draft.scope === 'PLATFORM' ? '<div class="notice">仅平台后台可核销，前置仓中心不显示此商品的核销任务。</div>' : `<h3>指定前置仓</h3>${['sz', 'ns'].map(id => `<label class="check-row"><input type="checkbox" data-policy-store="${id}" ${draft.stores.includes(id) ? 'checked' : ''}>${stores[id]}</label>`).join('')}<p class="muted">至少选择一个前置仓。员工仍须取得本仓核销权限。</p>`}
    <div class="notice">各核销端共用凭证、剩余次数与记录。平台核销2次后，前置仓只能使用剩余次数。</div><div class="policy-actions"><button class="primary" id="save-policy">保存演示配置</button><button id="new-virtual">按已保存配置生成演示新单</button></div><p class="muted" style="margin-top:12px">此处保存仅在当前页面有效，刷新后重置。</p></section>
    <section class="panel"><h2>成交快照对照</h2><div class="definition"><strong>当前已保存配置</strong><p>${esc(scopeLabel({ scope: state.policies.virtual.scope, scopeStores: state.policies.virtual.stores }))}</p></div><div class="definition"><strong>原有洗车次卡订单</strong><p>${esc(old.no)}</p><p>冻结范围：${esc(scopeLabel(old))} · 当前可核销${remaining(old)}次</p></div>${state.orders.filter(order => order.id.startsWith('new-virtual')).map(order => `<div class="definition"><strong>新生成订单</strong><p>${esc(order.no)}</p><p>冻结范围：${esc(scopeLabel(order))}</p><button class="text-button" data-open="${order.id}">查看订单</button></div>`).join('')}
    <div class="notice warn">已有订单不随配置改动。若要改变旧单权益，需要另行授权的售后流程。</div></section></div>`;
}

export function adminView(state, ui, actor) {
  return `<div class="admin-shell"><aside class="sidebar"><div class="brand">商城管理后台</div><button class="${ui.page !== 'policy' ? 'active' : ''}" data-admin-page="list">${icon('orders')}订单</button><button class="${ui.page === 'policy' ? 'active' : ''}" data-admin-page="policy">${icon('ticket')}虚拟商品</button><div class="account">平台运营<br>当前范围：全部订单</div></aside><div class="admin-main"><div class="breadcrumbs">商城管理后台 / ${ui.page === 'policy' ? '商品 / 核销范围' : '订单 / ' + (ui.page === 'detail' ? '订单详情' : '订单管理')}</div>
    <nav class="subnav" aria-label="订单与商品二级菜单"><button class="${ui.page !== 'policy' ? 'active' : ''}" data-admin-page="list">订单管理</button><button class="${ui.page === 'policy' ? 'active' : ''}" data-admin-page="policy">核销范围配置</button></nav>
    ${ui.viewState !== 'normal' ? stateScreen(ui.viewState) : ui.page === 'detail' ? detailView(state, ui, actor) : ui.page === 'policy' ? policyView(state, ui) : listView(state, ui, actor)}
    <details class="state-controls"><summary>评审辅助：查看加载、空列表、错误与无权限状态</summary>${stateSelector(ui.viewState)}</details></div></div>`;
}
