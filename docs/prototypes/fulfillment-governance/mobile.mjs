import { actionsFor, visibleOrders, stores, scopeLabel, remaining, physicalRemaining, statusLabel } from './state.mjs';
import { esc, tag, icon, actionButton, statusTag, orderAmount, ownerLabel, product, explanation, steps, timeline, inventory, virtualNumbers, stateSelector, stateScreen, modeNames } from './views.mjs';

function mobileTitle(title, back = true) {
  return `<div class="mobile-title">${back ? `<button aria-label="返回订单列表" data-back>${icon('back')}</button>` : '<span></span>'}<strong>${title}</strong><span class="capsule" aria-label="小程序菜单示意"><svg viewBox="0 0 52 20" width="52" height="20" aria-hidden="true"><g fill="currentColor"><circle cx="8" cy="10" r="1.5"/><circle cx="14" cy="10" r="1.5"/><circle cx="20" cy="10" r="1.5"/></g><path d="M29 3v14" fill="none" stroke="#d9dfe3"/><circle cx="41" cy="10" r="6" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="41" cy="10" r="2.1" fill="currentColor"/></svg></span></div>`;
}

function voucher(order, customer) {
  const isPickup = order.mode === 'PICKUP' && order.kind === 'PHYSICAL';
  if (isPickup) {
    const active = order.paid && !['DONE', 'REFUNDED', 'CANCELED'].includes(order.stage) && physicalRemaining(order) > 0;
    return `<section class="mobile-section"><h3>自提凭证</h3>${customer && active ? `<div class="voucher-code">${order.pickupCode}</div><div class="code-caption">演示码 · 不可用于真实提货</div>` : `<p class="muted">${customer ? '本订单已交付、关闭或售后处理中。' : '顾客出示自提码后，核验并一次交付剩余商品。'}</p>`}<div class="notice">自提凭证用于确认实物交付，与虚拟权益核销凭证分开。</div></section>`;
  }
  if (order.kind === 'PHYSICAL') return '';
  const active = order.paid && !order.expired && remaining(order) > 0;
  return `<section class="mobile-section"><h3>核销权益</h3>${customer && active ? `<div class="voucher-code">${order.voucher}</div><div class="code-caption">演示码 · 不可用于真实核销</div>` : `<p class="muted">${customer ? order.expired ? '已到期，凭证不可继续使用。' : '当前没有可使用的权益。' : '输入顾客出示的凭证，核验剩余次数后核销。'}</p>`}${virtualNumbers(order)}<p class="muted" style="margin-top:12px">核销范围：${esc(scopeLabel(order))}<br>有效至：${order.date}（当天有效）</p>${order.frozen ? `<div class="notice warn">${order.frozen}次售后冻结，暂不可核销。其余有效次数可继续使用。</div>` : ''}</section>`;
}

function orderDetail(state, order, actor) {
  const customer = actor.role === 'customer';
  const actions = actionsFor(order, actor);
  const primary = actions.find(action => !['APPLY_AFTER', 'CANCEL', 'FAIL_DELIVERY'].includes(action));
  const other = actions.filter(action => action !== primary);
  return `${mobileTitle(customer ? '订单详情' : order.kind === 'VIRTUAL' ? '核销订单详情' : '订单详情')}
    <div class="phone-body"><section class="mobile-status"><h2>${statusLabel(order)}</h2><p>${esc(explanation(order))}</p></section>
    ${order.kind !== 'VIRTUAL' ? `<section class="mobile-section"><div class="row"><strong>${modeNames[order.mode]}</strong>${tag(order.source)}</div>${steps(order)}<dl class="facts"><div><dt>${order.mode === 'EXPRESS' ? '发货方' : '履约前置仓'}</dt><dd>${esc(ownerLabel(order))}</dd></div><div><dt>${order.mode === 'PICKUP' ? '自提地点' : '送达地址'}</dt><dd>${esc(order.address)}</dd></div>${order.mode === 'PICKUP' ? '<div><dt>自提时间</dt><dd>营业时段内 · 演示 09:00–21:00</dd></div>' : `<div><dt>接收人</dt><dd>${esc(order.recipient)}</dd></div>`}</dl>${order.tracking ? `<div class="notice">${esc(order.carrier)}<br>运单号：${esc(order.tracking)}<br><small>物流轨迹为评审示例，暂无外部查询。</small></div>` : ''}</section>` : ''}
    ${voucher(order, customer)}<section class="mobile-section"><h3>商品信息</h3>${product(order)}<div class="row"><span class="muted">${order.points ? '兑换积分' : '订单实付'}</span><span class="price">${orderAmount(order)}</span></div></section>
    <section class="mobile-section"><h3>订单信息</h3><dl class="facts"><div><dt>订单编号</dt><dd>${esc(order.no)}</dd></div><div><dt>订单来源</dt><dd>${order.source}</dd></div><div><dt>创建时间</dt><dd>${order.createdAt}</dd></div></dl></section>
    <section class="mobile-section"><h3>${customer ? '订单进度' : '履约记录'}</h3>${timeline(state, order, customer)}</section>
    ${!customer && order.kind === 'MIXED' ? '<div class="notice">本中心只处理授权服务权益。礼盒的快递发货和物流由平台负责。</div>' : ''}
    ${customer && order.kind === 'PHYSICAL' && ['DONE', 'DISPATCHED', 'SHIPPED', 'FAILED'].includes(order.stage) ? '<div class="notice">已交付或在途商品需要退货验收。此稿演示未交付退款，退货验收沿用既有售后流程。</div>' : ''}
    </div><div class="mobile-footer">${other.map(action => actionButton(action, order)).join('')}${primary ? actionButton(primary, order, true) : `<span class="muted">${customer ? '请留意订单最新进度' : '当前无可执行的履约动作'}</span>`}</div>`;
}

function storeList(state, ui, actor) {
  const all = visibleOrders(state, actor);
  const groups = [
    ['ALL', '全部', () => true], ['PREPARE', '待备货', order => actionsFor(order, actor).includes('PREPARE')],
    ['DISPATCH', '待配送', order => actionsFor(order, actor).includes('DISPATCH')],
    ['COMPLETE', '配送中', order => ['DISPATCHED', 'FAILED'].includes(order.stage)],
    ['PICKUP', '待自提', order => actionsFor(order, actor).includes('PICKUP')],
    ['REDEEM', '待核销', order => actionsFor(order, actor).includes('REDEEM')],
  ];
  const filter = groups.find(([key]) => key === ui.storeTask)?.[2] || groups[0][2];
  const shown = all.filter(filter);
  return `${mobileTitle('前置仓中心', false)}<div class="store-heading"><div><strong>${stores[ui.store]}</strong><small>履约与核销工作台</small></div>${tag('营业中', 'good')}</div><div class="phone-body"><div class="task-tabs">${groups.map(([key, label, condition]) => `<button class="${ui.storeTask === key ? 'active' : ''}" data-store-task="${key}">${label}<b>${all.filter(condition).length}</b></button>`).join('')}</div>
    ${shown.length ? `<section class="mobile-section">${shown.map(order => `<article class="mobile-order"><div class="row">${tag(modeNames[order.mode])}${statusTag(order)}</div><h3>${esc(order.name)}</h3><p class="break">${esc(order.no)}</p><p>${order.kind === 'PHYSICAL' ? order.mode === 'PICKUP' ? '顾客到仓提货' : esc(order.address) : `可核销${order.expired ? 0 : remaining(order)}次 · ${esc(scopeLabel(order))}`}</p>${order.frozen ? `<p>售后冻结${order.frozen}${order.kind === 'PHYSICAL' ? '件' : '次'}</p>` : ''}<div class="order-actions"><button data-open="${order.id}">查看详情</button>${actionsFor(order, actor)[0] ? actionButton(actionsFor(order, actor)[0], order, true) : ''}</div></article>`).join('')}</section>` : stateScreen('empty', true)}<div class="notice">本仓负责配送、自提和授权虚拟核销。平台快递不进入本仓履约待办。</div></div>`;
}

function customerList(state) {
  return `${mobileTitle('我的订单', false)}<div class="phone-body"><section class="mobile-section">${state.orders.map(order => `<article class="mobile-order"><div class="row">${tag(order.source)}${statusTag(order)}</div><h3>${esc(order.name)}</h3><p>${esc(modeNames[order.mode])} · ${orderAmount(order)}</p><div class="order-actions"><button data-open="${order.id}">查看订单</button></div></article>`).join('')}</section></div>`;
}

function annotation(state, ui, order) {
  return `<aside class="annotation"><h2>联动评审</h2><section><h3>当前订单归属</h3><p>${order.kind === 'VIRTUAL' ? `核销范围：${esc(scopeLabel(order))}。两端共用剩余次数。` : `${modeNames[order.mode]}，${esc(ownerLabel(order))}。${order.kind === 'MIXED' ? '实物与权益分别完成。' : ''}`}</p><button class="small" data-to-role="${ui.role === 'store' ? 'customer' : 'store'}" data-id="${order.id}">${ui.role === 'store' ? '切到顾客端看结果' : '切到前置仓处理'}</button>${ui.role === 'customer' ? `<button class="small" data-to-role="platform" data-id="${order.id}" style="margin-top:8px">切到平台处理</button>` : ''}</section>
    <section><h3>模拟库存观察</h3>${inventory(state, order)}</section><section><h3>建议体验</h3><p>配送：前置仓备货 → 配送 → 送达。<br>自提：顾客看码 → 前置仓核验提货。<br>虚拟：平台核销 → 前置仓查看剩余。<br>售后：顾客申请 → 平台退款或驳回。</p></section><section>${stateSelector(ui.viewState)}</section></aside>`;
}

export function mobileView(state, ui, actor) {
  if (ui.viewState === 'denied' || !actor.permitted) {
    return `<div class="mobile-stage"><aside class="review-tools"><h1>${ui.role === 'store' ? '前置仓中心' : '顾客订单详情'}</h1><p>当前身份未获授权，不展示订单数据。</p>${stateSelector('denied')}</aside><section class="phone" aria-label="无权限手机预览">${mobileTitle('订单权限', false)}<div class="phone-body">${stateScreen('denied', true)}</div></section></div>`;
  }
  const eligible = visibleOrders(state, { ...actor, permitted: true });
  const selected = state.orders.find(order => order.id === ui.selected) || eligible[0] || state.orders[0];
  const accessible = eligible.some(order => order.id === selected.id);
  const options = ui.role === 'store' ? eligible : state.orders;
  const content = ui.viewState !== 'normal' ? `${mobileTitle(ui.role === 'store' ? '前置仓中心' : '订单详情', false)}<div class="phone-body">${stateScreen(ui.viewState, true)}</div>` :
    ui.page === 'list' ? ui.role === 'store' ? storeList(state, ui, actor) : customerList(state) :
      accessible ? orderDetail(state, selected, actor) : `${mobileTitle('订单详情')}<div class="phone-body"><div class="notice warn">该订单不属于本仓履约或核销范围。请返回本仓列表选择订单。</div><button data-back>查看本仓订单</button></div>`;
  return `<div class="mobile-stage"><aside class="review-tools"><h1>${ui.role === 'store' ? '前置仓中心' : '顾客订单详情'}</h1><p>${ui.role === 'store' ? '只处理本仓履约与获授权权益。' : '查看交付进度、提货码与剩余权益。'}</p>${ui.role === 'store' ? `<label class="field">当前授权前置仓<select id="store-select">${['sz', 'ns'].map(id => `<option value="${id}" ${ui.store === id ? 'selected' : ''}>${stores[id]}</option>`).join('')}</select></label>` : ''}
    <label class="field">选择评审订单<select id="scenario-select">${options.length ? options.map(order => `<option value="${order.id}" ${selected.id === order.id ? 'selected' : ''}>${esc(order.name)} · ${modeNames[order.mode]}</option>`).join('') : '<option value="">本仓暂无订单</option>'}</select></label><div class="scenario-list">${options.slice(0, 8).map(order => `<button class="${selected.id === order.id && ui.page === 'detail' ? 'active' : ''}" data-open="${order.id}">${esc(order.name)}<small>${order.source} · ${statusLabel(order)}</small></button>`).join('')}</div><button class="text-button" data-back style="margin-top:12px">${ui.role === 'store' ? '返回履约工作台' : '查看我的全部订单'}</button></aside>
    <section class="phone" aria-label="${ui.role === 'store' ? '前置仓中心' : '顾客订单'}手机预览"><div class="device-status"><span>9:41</span><span class="signal"><span>5G</span><svg viewBox="0 0 25 12" aria-hidden="true"><rect x="1" y="1" width="20" height="10" rx="2"/><path d="M23 4v4M4 4h14v4H4z"/></svg></span></div>${content}<div class="home-line"></div></section>${annotation(state, ui, selected)}</div>`;
}
