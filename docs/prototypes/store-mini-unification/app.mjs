import { initialState, publishPage, setHome, editPage, saveNavigation, saveNavigationDraft, moveNavigation, switchStore, switchMode, visibleProducts, chooseDelivery, orderActions, amountLines, previewSubmit, orderSnapshot, removeCartItem, categoryView, navigationIcons, categoryScrollPosition } from './state.mjs';

let state = initialState();
let ui = { admin: 'pages', screen: 'home', pageId: null, draft: false, category: 'liquor', subcategory: 'beer', categoryQuery: '', missingImages: false, managing: false, details: false, query: '', status: '', orderStatus: 'PENDING_PAYMENT', navDraft: null, navPreview: 'draft' };
let toastTimer;
let editPreview = null;
const $ = selector => document.querySelector(selector);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const money = value => `¥${Number(value).toFixed(2)}`;
const icons = {
  home: '<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
  page: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  category: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  cart: '<path d="M2 4h3l3 12h11l3-9H6M9 20h.1M18 20h.1"/>',
  me: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>', back: '<path d="m15 5-7 7 7 7"/>', down: '<path d="m5 9 7 7 7-7"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>', plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
  pin: '<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
  order: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  wallet: '<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 8h18M16 12h5v5h-5zM6 5V3h12"/>',
  box: '<path d="m3 7 9-4 9 4v13H3zM3 7l9 4 9-4M12 11v9"/>',
  settings: '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  'store-mark': '<path d="M3 8h18l-2-5H5zM5 8v12h14V8M9 20v-7h6v7M3 8v3h4V8M7 8v3h5V8M12 8v3h5V8M17 8v3h4V8"/>',
  'dining-mark': '<path d="M4 3v7a3 3 0 0 0 6 0V3M7 3v18M18 3v18M18 3c-4 2-4 8 0 9"/>',
  'home-filled': '<path fill="currentColor" stroke="none" fill-rule="evenodd" d="m12 2 10 8v11h-8v-7h-4v7H2V10z"/>',
  'page-filled': '<path fill="currentColor" stroke="none" d="M6 2h9l5 5v14H4V4a2 2 0 0 1 2-2Z"/><path stroke="white" d="M8 9h8M8 13h8M8 17h5"/>',
  'category-filled': '<path fill="currentColor" stroke="none" d="M2 2h9v9H2zM13 2h9v9h-9zM2 13h9v9H2zM13 13h9v9h-9z"/>',
  'cart-filled': '<path fill="currentColor" stroke="none" d="M5 5h17l-3 11H8z"/><path d="M2 3h2l4 14h11"/><circle cx="9" cy="21" r="2" fill="currentColor" stroke="none"/><circle cx="18" cy="21" r="2" fill="currentColor" stroke="none"/>',
  'me-filled': '<circle cx="12" cy="7" r="5" fill="currentColor" stroke="none"/><path fill="currentColor" stroke="none" d="M2 22v-2a10 10 0 0 1 20 0v2Z"/>',
  'box-filled': '<path fill="currentColor" stroke="none" d="m12 2 10 5v15H2V7Z"/><path stroke="white" d="m3 7 9 4 9-4M12 11v10"/>',
  'wallet-filled': '<path fill="currentColor" stroke="none" d="M4 4h15v4h3v14H2V6a2 2 0 0 1 2-2Z"/><path stroke="white" d="M3 8h17M18 14h2"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.page}</svg>`;
const btn = (label, action, extra = '', className = '') => `<button data-action="${action}" ${extra} class="${className}">${label}</button>`;
const currentStore = () => state.stores.find(store => store.id === (state.mode === 'dining' ? state.dining.storeId : state.storeId));
const availableIds = () => new Set(visibleProducts(state).map(product => product.id));
const cartRows = () => state.cart.map(line => ({ ...line, product: state.products.find(product => product.id === line.productId), available: availableIds().has(line.productId) }));
const selectedTotal = () => cartRows().filter(line => line.selected && line.available).reduce((sum, line) => sum + line.product.price * line.quantity, 0);
const navigationDraft = () => ui.navDraft || state.navigationDraft;
const previewNavigation = () => ui.admin === 'navigation' && ui.navPreview === 'draft' ? navigationDraft() : state.navigation;

function notify(message) {
  $('#toast').textContent = message;
  $('#toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 3400);
}

function openModal(title, content, footer = '') {
  $('#modal-content').innerHTML = `<div class="modal-heading"><h2 id="modal-title">${escape(title)}</h2>${btn(icon('close'), 'close', 'aria-label="关闭弹窗"')}</div>${content}${footer ? `<div class="modal-footer">${footer}</div>` : ''}`;
  if (!$('#modal').open) $('#modal').showModal();
}

function closeModal() { editPreview = null; $('#modal').close(); renderPhone(); }

function pageStatus(page) {
  if (!page.published) return '<span class="tag">草稿 · 未发布</span>';
  return `<span class="tag good">已发布</span>${page.draft.revision !== page.published.revision ? ' <span class="tag">有新草稿</span>' : ''}`;
}

function renderPages() {
  const home = state.pages.find(page => page.id === state.homeId);
  const rows = state.pages.filter(page => (!ui.query || page.name.includes(ui.query)) && (!ui.status || (ui.status === 'published' ? !!page.published : !page.published)));
  return `<div class="panel-heading"><div><h2>微页面管理</h2><p>同一编辑器管理首页和专题页，发布后才可被访问。</p></div>${btn('新建微页面', 'new-page', '', 'primary')}</div>
    <div class="current-home"><div><strong>当前首页：${escape(home.name)}</strong><p>线上版本 v${home.published.revision} · 默认首页导航始终指向当前首页</p></div>${btn('预览线上首页', 'home-preview')}</div>
    <div class="filters"><label>页面名称<input id="page-query" placeholder="搜索微页面" value="${escape(ui.query)}"></label><label>发布状态<select id="page-status"><option value="">全部状态</option><option value="published" ${ui.status === 'published' ? 'selected' : ''}>已发布</option><option value="draft" ${ui.status === 'draft' ? 'selected' : ''}>未发布</option></select></label>${btn('查询', 'query')}${btn('重置', 'query-reset')}</div>
    <div class="table-wrap"><table><thead><tr><th>页面名称</th><th>发布状态</th><th>版本</th><th>操作</th></tr></thead><tbody>${rows.map(page => `<tr><td><strong>${escape(page.name)}</strong>${page.id === state.homeId ? '<span class="tag brand">当前首页</span>' : '<small>普通微页面</small>'}</td><td>${pageStatus(page)}</td><td><small>草稿 v${page.draft.revision}<br>线上 ${page.published ? `v${page.published.revision}` : '—'}</small></td><td><div class="actions">${btn('编辑', 'edit-page', `data-id="${page.id}"`, 'link')}${btn('预览', 'preview-page', `data-id="${page.id}"`, 'link')}${btn('发布', 'publish-confirm', `data-id="${page.id}"`, 'link')}${btn('指定首页', 'home-confirm', `data-id="${page.id}" ${page.id === state.homeId ? 'disabled' : ''}`, 'link')}${btn('历史', 'history', `data-id="${page.id}"`, 'link')}</div></td></tr>`).join('')}</tbody></table>${!rows.length ? '<div class="empty-admin"><h3>没有匹配的微页面</h3><p>调整名称或发布状态后重新查询。</p></div>' : ''}</div>
    <div class="pager"><span>共 ${rows.length} 个微页面</span><span>20条 / 页 &nbsp; <button disabled>上一页</button> <button disabled>1</button> <button disabled>下一页</button></span></div>
    <div class="rule-list"><h3>首页与版本关系</h3><p>指定首页使用此页面当前已发布版本。编辑并保存草稿不会改变线上首页；再次发布此页面后，线上首页随新版本更新。</p><p>原首页保留为普通可管理微页面，发布历史可查看、可回退。当前首页下线或删除前，应先指定另一已发布页面。</p>${btn('查看首页下线限制', 'home-block', '', 'link')}</div>`;
}

function navTargetValue(item) { return item.type === 'page' ? `page:${item.target}` : item.type; }
function navOptions(item) {
  const choices = [{ value: 'page:@home', label: '当前首页（动态入口）' }, ...state.pages.map(page => ({ value: `page:${page.id}`, label: `${page.name}${page.published ? '' : ' · 未发布'}` })), { value: 'category', label: '分类' }, { value: 'cart', label: '购物车' }, { value: 'me', label: '我的' }];
  return choices.map(choice => `<option value="${choice.value}" ${choice.value === navTargetValue(item) ? 'selected' : ''}>${escape(choice.label)}</option>`).join('');
}

function renderNavigation() {
  const nav = navigationDraft();
  const glyphs = [['home', '首页'], ['page', '微页面'], ['category', '分类'], ['cart', '购物车'], ['me', '用户'], ['box', '商品'], ['wallet', '账户']].flatMap(([name, label]) => [[name, `${label} · 空心`], [`${name}-filled`, `${label} · 实心`]]);
  return `<div class="panel-heading"><div><h2>底部导航配置</h2><p>1—5 个入口，可配置多个微页面，排序即时预览。</p></div><span class="tag">${nav.length} / 5 项</span></div>
    <div class="nav-field-headings"><span>排序</span><span>显示名称</span><span>两种状态图标</span><span>操作</span></div>
    ${nav.map((item, index) => { const pair = navigationIcons(item); return `<div class="nav-row"><span class="index">${index + 1}</span><input class="nav-name" aria-label="第${index + 1}项导航名称" data-nav-label="${item.id}" maxlength="8" value="${escape(item.label)}"><div class="nav-icon-pair">${['icon', 'activeIcon'].map(field => `<label class="nav-icon-field ${field === 'activeIcon' ? 'is-selected' : ''}"><span>${icon(pair[field])}${field === 'icon' ? '未选中图标' : '选中图标'}</span><select aria-label="第${index + 1}项${field === 'icon' ? '未选中' : '选中'}图标" ${field === 'icon' ? 'data-nav-icon' : 'data-nav-active-icon'}="${item.id}">${glyphs.map(([value, name]) => `<option value="${value}" ${value === pair[field] ? 'selected' : ''}>${name}</option>`).join('')}</select></label>`).join('')}</div><label class="nav-link-label"><span>链接目标</span><select class="nav-target" aria-label="第${index + 1}项链接目标" data-nav-target="${item.id}">${navOptions(item)}</select></label><div class="actions">${btn('上移', 'nav-move', `data-index="${index}" data-offset="-1" ${index === 0 ? 'disabled' : ''}`)}${btn('下移', 'nav-move', `data-index="${index}" data-offset="1" ${index === nav.length - 1 ? 'disabled' : ''}`)}${btn('删除', 'nav-delete', `data-id="${item.id}"`, 'link danger')}</div></div>`; }).join('')}
    <div id="nav-error" class="form-error" role="alert"></div>
    <div class="toolbar">${btn('添加导航', 'nav-add', nav.length >= 5 ? 'disabled' : '')}<div class="actions">${btn('恢复草稿', 'nav-reset')}${btn('保存草稿', 'nav-save-draft')}${btn('发布生效', 'nav-save', '', 'primary')}</div></div>
    <div class="navigation-preview-switch"><span>手机导航预览</span>${btn('导航草稿', 'nav-preview', 'data-value="draft"', ui.navPreview === 'draft' ? 'active' : '')}${btn('线上配置', 'nav-preview', 'data-value="published"', ui.navPreview === 'published' ? 'active' : '')}</div>
    <div class="rule-list"><h3>链接与发布校验</h3><p>导航允许多个微页面；分类、购物车、我的也都可添加、删除和排序。建议保留购物车和我的，方便用户完成购买与查看订单。</p><p>未发布页面可以保存到导航草稿。发布生效前必须核对目标，未发布、下线或失效时明确阻止，不会静默跳回首页。</p><p>未选中与选中图标分别配置，可选择空心或实心形状。“导航草稿”即时预览编辑结果；“线上配置”保留最近发布版本。</p></div>`;
}

function renderShared() {
  const guides = [
    ['店铺与商品范围', '前置仓可切换；餐饮店铺和桌号由桌码固定。商品按当前销售范围展示。', 'home'],
    ['分类与图片回退', '顶部搜索和一级图标横排，左侧二级分类，右侧横幅与商品列表。缺图不留空白。', 'category'],
    ['购物车与我的', '仅一个居中顶标题，管理入口保留；订单与权益入口保持熟悉位置。', 'cart'],
    ['确认订单与订单详情', '紧凑配送方式、相关地址、短付款按钮、零优惠隐藏、信息可展开。', 'checkout'],
    ['用户与角色中心', '同样的标题、列表、间距与状态语义，各角色只显示权限内入口。', 'center'],
  ];
  return `<div class="panel-heading"><div><h2>共用页面与组件</h2><p>界面复用，店铺归属、桌号和业务权限保持清晰。</p></div></div><div class="shared-guide">${guides.map(([title, description, screen]) => `<div class="guide-row"><div><strong>${title}</strong><p>${description}</p></div>${btn('查看示例', 'screen', `data-screen="${screen}"`)}</div>`).join('')}</div>
    <div class="sim-form"><h3>边界状态演示</h3><label><input id="missing-images" type="checkbox" ${ui.missingImages ? 'checked' : ''}> 分类图片缺失（收起图片区域）</label><label>订单示例状态<select id="order-status"><option value="PENDING_PAYMENT" ${orderSnapshot(state).status === 'PENDING_PAYMENT' ? 'selected' : ''}>待付款</option><option value="PAID" ${orderSnapshot(state).status === 'PAID' ? 'selected' : ''}>已付款 · 待履约</option><option value="DELIVERED" ${orderSnapshot(state).status === 'DELIVERED' ? 'selected' : ''}>已交付</option><option value="CLOSED" ${orderSnapshot(state).status === 'CLOSED' ? 'selected' : ''}>已关闭</option></select></label><label>示例优惠券抵扣<input id="coupon-demo" type="number" min="0" max="20" value="${state.checkout.coupon}"> 元</label><p>以上控件仅切换原型样例，不代表库存、配送能力、到账、佣金或订单的真实业务事实。</p></div>`;
}

function contextBlock() {
  const dining = state.mode === 'dining';
  const shop = dining ? state.dining : currentStore();
  return `<div class="store-context"><div class="store-head-row"><div class="store-logo" aria-label="${dining ? '餐厅' : '前置仓'}示例店标">${icon(dining ? 'dining-mark' : 'store-mark')}</div><strong>${escape(shop.name)}${dining ? ` · ${escape(shop.table)}桌` : ''}</strong>${dining ? '<span class="fixed-table">固定餐桌</span>' : btn('切换', 'switch-store')}</div><div class="store-hours">营业时间 ${escape(shop.openingHours)}</div></div>`;
}

function productCard(product) {
  return `<article class="product-card"><img src="${product.image}" alt="${escape(product.name)}" loading="lazy"><div class="product-body"><strong>${escape(product.name)}</strong><small>${escape(product.spec)}</small><div class="product-price"><span class="price">${money(product.price)}</span>${btn(icon('plus'), 'add-cart', `data-id="${product.id}" aria-label="加入购物车：${escape(product.name)}"`)}</div></div></article>`;
}

function renderHome() {
  const page = state.pages.find(page => page.id === (ui.pageId || state.homeId));
  const content = editPreview?.id === page.id ? editPreview : ui.draft ? page.draft : page.published;
  if (!content) return `<div class="empty">${icon('page')}<strong>此微页面尚未发布</strong><p>可在后台编辑草稿并预览；发布后才能从导航访问。</p>${btn('查看草稿', 'preview-draft', `data-id="${page.id}"`)}</div>`;
  return `${contextBlock()}<div class="page-copy"><h3>${escape(content.title)}</h3><p>${escape(content.subtitle)}</p></div><div class="hero"><img src="assets/home-banner.png" alt="酒水精选活动示例"></div><h3 class="section-heading" style="padding:0 12px">本店精选<span>${state.mode === 'dining' ? '餐厅销售商品' : '当前前置仓在售商品'}</span></h3><div class="product-grid">${visibleProducts(state).map(productCard).join('')}</div>`;
}

function selectedCategory() { return categoryView(state, { primaryId: ui.category, secondaryId: ui.subcategory, query: ui.categoryQuery }); }

function categoryProducts(view) {
  const banner = !ui.missingImages && view.secondary.image ? `<img class="category-banner" src="${view.secondary.image}" alt="${view.secondary.name}分类横幅">` : '';
  return `${banner}<h3 class="category-result-title">${escape(view.secondary.name)}<small>${view.products.length}件商品</small></h3>${view.products.length ? `<div class="category-product-list">${view.products.map(product => `<article class="category-product-row"><img src="${product.image}" alt="${escape(product.name)}" loading="lazy"><div class="category-product-info"><strong>${escape(product.name)}</strong><small>${escape(product.spec)}</small><div class="product-price"><span class="price">${money(product.price)}</span>${btn(icon('plus'), 'add-cart', `data-id="${product.id}" aria-label="加入购物车：${escape(product.name)}"`)}</div></div></article>`).join('')}</div>` : `<div class="category-empty"><strong>${ui.categoryQuery ? '没有匹配商品' : '本店暂无在售商品'}</strong><p>${ui.categoryQuery ? '试试其他商品名称。' : '请选择其他分类。'}</p>${ui.categoryQuery ? btn('清空搜索', 'category-search-reset') : ''}</div>`}`;
}

function renderCategory() {
  const view = selectedCategory();
  return `<div class="category-layout"><div class="category-search"><label>${icon('search')}<input id="category-query" type="search" aria-label="搜索当前分类商品" placeholder="搜索当前分类商品" value="${escape(ui.categoryQuery)}"></label></div><nav class="primary-category-strip" aria-label="一级分类" tabindex="0">${state.categories.map(category => `<button data-action="category-open" data-id="${category.id}" class="${view.primary.id === category.id ? 'active' : ''}" aria-pressed="${view.primary.id === category.id}">${!ui.missingImages && category.image ? `<img src="${category.image}" alt="">` : `<span class="category-icon-fallback">${icon('category')}</span>`}<span>${escape(category.name)}</span></button>`).join('')}</nav><div class="category-columns"><nav class="secondary-category-list" aria-label="二级分类">${view.primary.children.map(category => `<button data-action="subcategory" data-id="${category.id}" class="${view.secondary.id === category.id ? 'active' : ''}" aria-pressed="${view.secondary.id === category.id}">${escape(category.name)}</button>`).join('')}</nav><section id="category-results" class="category-results" aria-label="分类商品">${categoryProducts(view)}</section></div></div>`;
}

function renderCart() {
  const rows = cartRows();
  if (!rows.length) return `<div class="empty">${icon('cart')}<strong>购物车还是空的</strong><p>去当前店铺挑选商品，加入购物车后继续。</p>${btn('去逛逛', 'screen', 'data-screen="home"', 'primary')}</div>`;
  return `${contextBlock()}${rows.map(line => `<div class="mobile-panel"><div class="product-line"><input type="checkbox" data-cart-select="${line.productId}" ${line.selected ? 'checked' : ''} aria-label="选择${escape(line.product.name)}"><img src="${line.product.image}" alt="${escape(line.product.name)}"><div class="grow"><strong>${escape(line.product.name)}</strong><small>${escape(line.product.spec)}</small><div class="line-bottom"><span class="price">${money(line.product.price)}</span><div class="stepper">${btn(icon('minus'), 'quantity', `data-id="${line.productId}" data-step="-1" aria-label="减少数量" ${line.quantity <= 1 ? 'disabled' : ''}`)}<span>${line.quantity}</span>${btn(icon('plus'), 'quantity', `data-id="${line.productId}" data-step="1" aria-label="增加数量"`)}</div></div></div></div>${!line.available ? '<p class="inline-error">当前商品暂不可购买，不计入本次结算。</p>' : ''}${ui.managing ? `<div class="actions" style="justify-content:end;margin-top:10px">${btn('移除', 'cart-remove', `data-id="${line.productId}"`, 'link danger')}</div>` : ''}</div>`).join('')}<p style="padding:0 4px">购物车仅属于当前店铺与访问场景，结算前核对价格与库存。</p>`;
}

function addressBlock() {
  if (state.mode === 'dining') return `<strong>${escape(state.dining.name)} · ${escape(state.dining.table)}桌</strong>${escape(state.dining.address.replace(/ · \d+桌$/, ''))}`;
  if (state.checkout.delivery === 'pickup') return `<strong>${escape(currentStore().name)} · 到店自提</strong>${escape(currentStore().address)}<br><small>营业时间 09:00—22:00（示例）</small>`;
  return `<strong>李明 · 138****8000</strong>${escape(state.checkout.address.split(' · ')[1])}${btn('选择地址', 'address', '', 'link')}`;
}

function amountBlock(amounts = state.checkout) {
  return amountLines(amounts).map(line => `<div class="row ${line.key === 'total' ? 'total' : ''}"><span>${line.label}</span><span class="${line.key === 'total' ? 'price' : ''}">${line.key === 'shipping' && line.amount === 0 ? '免费' : money(line.amount)}</span></div>`).join('');
}

function renderCheckout() {
  const lines = cartRows().filter(line => line.selected && line.available);
  return `<div class="mobile-panel">${state.mode === 'dining' ? '' : `<h3>配送方式</h3><div class="delivery-choice">${[{ id: 'store', label: '前置仓配送' }, { id: 'express', label: '快递配送' }, { id: 'pickup', label: '到店自提' }].map(item => `<button data-action="delivery" data-id="${item.id}" class="${state.checkout.delivery === item.id ? 'active' : ''}">${item.label}</button>`).join('')}</div>`}<div class="address">${addressBlock()}</div></div>
    <div class="mobile-panel"><h3>商品明细</h3>${lines.length ? lines.map(line => `<div class="product-line"><img src="${line.product.image}" alt="${escape(line.product.name)}"><div class="grow"><strong>${escape(line.product.name)}</strong><small>${escape(line.product.spec)} × ${line.quantity}</small><div class="line-bottom"><span class="price">${money(line.product.price * line.quantity)}</span></div></div></div>`).join('<hr class="divider">') : '<p>当前无可结算商品，请回购物车选择本店商品。</p>'}</div>
    <div class="mobile-panel">${amountBlock()}</div><div class="mobile-panel"><h3>付款方式</h3><div class="payment-choices"><label><input type="radio" name="payment" value="wechat" ${state.checkout.payment === 'wechat' ? 'checked' : ''}>微信支付</label><label><input type="radio" name="payment" value="offline" ${state.checkout.payment === 'offline' ? 'checked' : ''}>线下支付</label></div>${state.checkout.payment === 'offline' ? '<div class="payment-instructions"><strong>线下付款说明（示例）</strong><p>按订单收款说明付款，保留凭证并报告付款信息。</p><p>提交后待付款，平台核实到账后更新。</p></div>' : ''}</div>`;
}

function renderOrder() {
  const order = orderSnapshot(state);
  const status = { PENDING_PAYMENT: ['待付款', `付款截止 ${order.paymentDeadline}（示例）`], PAID: ['已付款 · 待履约', '当前订单等待前置仓处理。'], DELIVERED: ['已交付', '订单状态以最新履约结果为准。'], CLOSED: ['已关闭', '此订单不可继续付款。'] }[order.status];
  const address = order.dining ? `<strong>${escape(order.dining.name)} · ${escape(order.dining.table)}桌</strong>${escape(order.dining.address)}` : `<strong>${escape(order.recipient.name)} · ${escape(order.recipient.phone)}</strong>${escape(order.recipient.address)}`;
  return `<div class="status-band"><h3>${status[0]}</h3><p>${escape(status[1])}</p></div><div class="mobile-panel"><h3>${order.dining ? '餐桌配送' : { store: '前置仓配送', express: '快递配送', pickup: '到店自提' }[order.delivery]}</h3><div class="address">${address}</div></div>
    <div class="mobile-panel">${order.lines.map(line => `<div class="product-line"><img src="${line.image}" alt="${escape(line.name)}"><div class="grow"><strong>${escape(line.name)}</strong><small>${escape(line.spec)} · ${line.quantity}份</small><div class="line-bottom"><span class="price">${money(line.amount)}</span></div></div></div>`).join('<hr class="divider">')}</div>
    <div class="mobile-panel">${amountBlock(order.amounts)}</div>${order.payment === 'offline' && order.status === 'PENDING_PAYMENT' ? `<div class="mobile-panel"><h3>本订单付款说明</h3><p>${escape(order.paymentInstructions)}</p><p>报告付款不代表到账；平台核实后更新订单状态。</p></div>` : ''}<div class="mobile-panel">${btn(`订单信息 ${icon(ui.details ? 'down' : 'chevron')}`, 'details', `aria-expanded="${ui.details}"`, 'details-toggle')}${ui.details ? `<div class="details-body"><div class="row"><span>订单号</span><span>${escape(order.id)}</span></div><div class="row"><span>下单时间</span><span>${escape(order.createdAt)}</span></div><div class="row"><span>付款截止</span><span>${escape(order.paymentDeadline)}</span></div><div class="row"><span>付款方式</span><span>${order.payment === 'wechat' ? '微信支付' : '线下支付'}</span></div><div class="row"><span>履约前置仓</span><span>${escape(order.store.name)}</span></div>${order.dining ? `<div class="row"><span>餐厅 / 桌号</span><span>${escape(order.dining.name)} / ${escape(order.dining.table)}</span></div>` : ''}</div>` : ''}</div>`;
}

function renderMe() {
  return `<div class="mobile-panel"><div class="profile-row"><div class="avatar">${icon('me')}</div><div><strong>李明</strong><p>普通会员 · 138****8000</p></div></div><div class="center-summary"><div><strong>—</strong><small>积分</small></div><div><strong>—</strong><small>优惠券</small></div><div><strong>—</strong><small>有效消费</small></div></div></div><div class="mobile-panel"><h3>我的订单</h3><div class="order-entries">${['待付款', '待发货', '待收货', '售后'].map(label => btn(`${icon('order')}<span>${label}</span>`, 'screen', 'data-screen="order"')).join('')}</div></div><div class="mobile-panel center-entries">${entry('收货地址', 'pin', 'address')}${entry('我的权益', 'wallet', 'generic')}${entry('用户中心', 'me', 'center', 'data-role="customer"')}${entry('前置仓管理', 'box', 'center', 'data-role="store"')}${entry('联盟店管理', 'category', 'center', 'data-role="dining"')}${entry('联系客服', 'me', 'generic')}</div>`;
}

function entry(label, glyph, action, extra = '') { return btn(`${icon(glyph)}<span>${label}</span>${icon('chevron')}`, action, `${extra} data-label="${label}"`); }

function renderCenter() {
  const role = state.role;
  const title = role === 'customer' ? '李明' : role === 'store' ? currentStore().name : state.dining.name;
  const entries = role === 'customer' ? [['我的订单', 'order', 'order'], ['积分与优惠券', 'wallet', 'generic'], ['收货地址', 'pin', 'address']] : role === 'store' ? [['待处理订单', 'order', 'order'], ['配送与核销', 'box', 'generic'], ['商品与库存', 'box', 'generic'], ['进货订单', 'cart', 'generic'], ['前置仓账户', 'wallet', 'generic'], ['前置仓设置', 'settings', 'generic']] : [['餐饮订单', 'order', 'order'], ['桌码管理', 'category', 'generic'], ['销售商品', 'box', 'generic'], ['佣金与提现', 'wallet', 'generic'], ['店铺资料', 'settings', 'generic']];
  return `<div class="mobile-panel"><div class="profile-row"><div class="avatar">${icon(role === 'customer' ? 'me' : 'box')}</div><div><strong>${escape(title)}</strong><p>${role === 'customer' ? '会员账户' : role === 'store' ? '当前前置仓的管理范围' : '当前联盟店的管理范围'}</p></div></div><div class="center-summary"><div><strong>—</strong><small>${role === 'customer' ? '积分' : '待处理'}</small></div><div><strong>—</strong><small>${role === 'customer' ? '优惠券' : '账户数据'}</small></div></div></div><div class="mobile-panel center-entries">${entries.map(([label, glyph, action]) => entry(label, glyph, action === 'order' ? 'screen' : action, action === 'order' ? 'data-screen="order"' : '')).join('')}</div>`;
}

function mobileTitle() {
  if (ui.screen === 'category') return '分类';
  return { home: '商城', cart: '购物车', me: '我的', checkout: '确认订单', order: '订单详情', center: { customer: '用户中心', store: '前置仓中心', dining: '联盟中心' }[state.role] }[ui.screen];
}

function mobileFooter() {
  if (ui.screen === 'cart' && state.cart.length) return `<div class="bottom-action"><label class="checkbox-line"><input type="checkbox" id="cart-all" ${state.cart.every(line => line.selected) ? 'checked' : ''}>全选</label><div class="grow">合计 <span class="price">${money(selectedTotal())}</span></div>${btn('去结算', 'checkout', selectedTotal() <= 0 ? 'disabled' : '', 'primary')}</div>`;
  if (ui.screen === 'checkout') return `<div class="bottom-action"><div class="grow">应付 <span class="price">${money(amountLines(state.checkout).at(-1).amount)}</span></div>${btn(state.checkout.payment === 'wechat' ? '付款' : '提交订单', 'submit-preview', selectedTotal() <= 0 ? 'disabled' : '', 'primary')}</div>`;
  if (ui.screen === 'order') return `<div class="bottom-action" style="justify-content:end">${orderActions(state.role, orderSnapshot(state).status).map((label, index) => btn(label, label === '付款' ? 'submit-preview' : 'order-action', `data-label="${label}"`, index === 0 && label === '付款' ? 'primary' : '')).join('') || '<small>当前无可执行操作</small>'}</div>`;
  const nav = previewNavigation();
  return `<nav class="mobile-nav" aria-label="商城底部导航">${nav.map(item => {
    const active = item.type === ui.screen || (ui.screen === 'home' && item.type === 'page' && (item.target === '@home' ? (!ui.pageId || ui.pageId === state.homeId) : item.target === ui.pageId));
    const selectedIcon = navigationIcons(item)[active ? 'activeIcon' : 'icon'];
    return `<button data-action="mobile-nav" data-id="${item.id}" data-icon-state="${active ? 'selected' : 'unselected'}" data-icon-name="${selectedIcon}" class="${active ? 'active' : ''}">${icon(selectedIcon)}<span>${escape(item.label)}</span></button>`;
  }).join('')}</nav>`;
}

function renderPhone() {
  const categoryScroll = $('.primary-category-strip')?.scrollLeft || 0;
  const back = ['checkout', 'order', 'center'].includes(ui.screen);
  const content = { home: renderHome, category: renderCategory, cart: renderCart, me: renderMe, checkout: renderCheckout, order: renderOrder, center: renderCenter }[ui.screen]();
  $('#phone-screen').innerHTML = `<div class="phone-content">${ui.screen === 'home' ? '' : `<div class="mobile-heading"><div>${back ? btn(icon('back'), 'mobile-back', 'aria-label="返回"') : ''}</div><h2>${mobileTitle()}</h2><div>${ui.screen === 'cart' && state.cart.length ? btn(ui.managing ? '完成' : '管理', 'cart-manage') : ''}</div></div>`}<div class="mobile-scroll ${['home', 'category'].includes(ui.screen) ? 'flush' : ''}">${content}</div>${mobileFooter()}</div>`;
  const strip = $('.primary-category-strip');
  const activeCategory = strip?.querySelector('button.active');
  if (activeCategory) strip.scrollLeft = categoryScrollPosition(categoryScroll, activeCategory.offsetLeft, activeCategory.offsetWidth, strip.clientWidth, strip.scrollWidth);
  const page = state.pages.find(page => page.id === (ui.pageId || state.homeId));
  $('#preview-source').textContent = ui.admin === 'navigation' ? ui.navPreview === 'draft' ? '导航草稿 · 即时预览' : '线上配置 · 最近发布' : ui.screen === 'home' ? `${ui.draft ? '草稿预览' : '已发布版本'} · v${(ui.draft ? page.draft : page.published)?.revision || '—'}` : '共用页面 · 本地样例';
  document.querySelectorAll('[data-screen]').forEach(button => button.classList.toggle('active', button.dataset.screen === ui.screen));
}

function render() {
  document.querySelectorAll('[data-admin]').forEach(button => button.classList.toggle('active', button.dataset.admin === ui.admin));
  $('#admin-content').innerHTML = ui.admin === 'pages' ? renderPages() : ui.admin === 'navigation' ? renderNavigation() : renderShared();
  $('#mode').value = state.mode;
  $('#role').value = state.role;
  renderPhone();
}

function showEditor(id) {
  const page = state.pages.find(page => page.id === id);
  editPreview = { ...page.draft, id };
  ui = { ...ui, screen: 'home', pageId: id, draft: true };
  renderPhone();
  openModal(`编辑微页面 · ${page.name}`, `<p>首页与专题页共用此编辑器。右侧预览草稿，保存草稿不会改变线上版本。</p><div class="modal-fields"><label>页面标题<input id="edit-title" value="${escape(page.draft.title)}" maxlength="24"></label><label>页面副标题<input id="edit-subtitle" value="${escape(page.draft.subtitle)}" maxlength="40"></label><div><h3>共用组件</h3><span class="tag">店铺上下文</span> <span class="tag">运营横幅</span> <span class="tag">本店商品列表</span><p>图示组件复用，同一销售范围规则作用于首页和专题页。</p></div><div id="edit-error" class="form-error" role="alert"></div></div>`, `${btn('取消', 'close')}${btn('保存草稿', 'save-page', `data-id="${id}"`, 'primary')}`);
}

function handleAction(button) {
  const { action, id, screen, label } = button.dataset;
  if (action === 'close') return closeModal();
  if (action === 'screen') { ui = { ...ui, screen, pageId: screen === 'home' ? null : ui.pageId, draft: false }; if (screen === 'checkout') syncCheckout(); }
  if (action === 'query') ui = { ...ui, query: $('#page-query').value.trim(), status: $('#page-status').value };
  if (action === 'query-reset') ui = { ...ui, query: '', status: '' };
  if (action === 'edit-page') return showEditor(id);
  if (action === 'new-page') {
    const newId = `page-${Date.now()}`;
    state = { ...state, pages: [...state.pages, { id: newId, name: '新建微页面', draft: { title: '新建微页面', subtitle: '填写页面介绍', revision: 1 }, published: null, history: [] }] };
    render(); return showEditor(newId);
  }
  if (action === 'save-page') {
    try { state = editPage(state, id, { title: $('#edit-title').value.trim(), subtitle: $('#edit-subtitle').value.trim() }); closeModal(); notify('草稿已保存到本次演示；线上版本未改变'); }
    catch (error) { $('#edit-error').textContent = error.message; return; }
  }
  if (['preview-page', 'preview-draft'].includes(action)) { ui = { ...ui, screen: 'home', pageId: id, draft: action === 'preview-draft' || !state.pages.find(page => page.id === id).published }; }
  if (action === 'home-preview') ui = { ...ui, screen: 'home', pageId: null, draft: false };
  if (action === 'publish-confirm') {
    const page = state.pages.find(page => page.id === id);
    return openModal('发布微页面', `<p>将“${escape(page.name)}”草稿 v${page.draft.revision} 发布为线上版本。${state.homeId === id ? '此页面是当前首页，首页同步使用新发布版本。' : '发布后可被导航引用或指定为首页。'}</p><p>这里只模拟发布，不写入真实后台。</p>`, `${btn('取消', 'close')}${btn('确认发布', 'publish', `data-id="${id}"`, 'primary')}`);
  }
  if (action === 'publish') { state = publishPage(state, id); ui = { ...ui, draft: false }; closeModal(); notify('已模拟发布，历史版本已保留'); }
  if (action === 'home-confirm') {
    const page = state.pages.find(page => page.id === id);
    return openModal('指定唯一首页', page.published ? `<p>使用“${escape(page.name)}”已发布 v${page.published.revision} 作为唯一首页。原首页仍保留在微页面列表中。</p>` : '<div class="inline-error">此页面尚未发布。请先发布草稿，再指定为首页。</div>', `${btn('关闭', 'close')}${page.published ? btn('确认指定', 'set-home', `data-id="${id}"`, 'primary') : ''}`);
  }
  if (action === 'set-home') { state = setHome(state, id); ui = { ...ui, screen: 'home', pageId: null, draft: false }; closeModal(); notify('唯一首页已切换；默认首页导航同步指向新首页'); }
  if (action === 'home-block') return openModal('当前首页下线限制', '<p>当前首页不可直接下线或删除。请先指定另一已发布微页面为首页，再处理原页面。</p>', btn('知道了', 'close', '', 'primary'));
  if (action === 'history') {
    const page = state.pages.find(page => page.id === id);
    return openModal(`发布历史 · ${page.name}`, `${page.published ? `<div class="history-row"><div><strong>v${page.published.revision} · ${escape(page.published.title)}</strong><p>当前线上版本</p></div><span class="tag good">已发布</span></div>` : ''}${[...page.history].reverse().map((version, index) => `<div class="history-row"><div><strong>v${version.revision} · ${escape(version.title)}</strong><p>历史发布快照</p></div>${btn('回退至此版本', 'rollback-confirm', `data-id="${id}" data-index="${page.history.length - 1 - index}"`)}</div>`).join('') || '<p>暂无历史发布记录。首次发布后将保留版本快照。</p>'}`, btn('关闭', 'close'));
  }
  if (action === 'rollback-confirm') return openModal('确认回退发布版本', '<p>恢复所选历史发布快照，保留当前草稿与现有历史。若此页面是首页，线上首页同步恢复。</p>', `${btn('取消', 'close')}${btn('确认回退', 'rollback', `data-id="${id}" data-index="${button.dataset.index}"`, 'primary')}`);
  if (action === 'rollback') {
    state = { ...state, pages: state.pages.map(page => page.id === id ? { ...page, published: { ...page.history[Number(button.dataset.index)] }, history: [...page.history, { ...page.published }] } : page) };
    ui = { ...ui, draft: false }; closeModal(); notify('已模拟回退到历史发布版本，草稿保持独立');
  }
  if (action === 'nav-add') { const nav = navigationDraft(); if (nav.length >= 5) return notify('底部导航最多5项'); ui = { ...ui, navDraft: [...nav, { id: `nav-${Date.now()}`, label: '精选', type: 'page', target: 'theme', icon: 'page', activeIcon: 'page-filled' }], navPreview: 'draft' }; }
  if (action === 'nav-delete') ui = { ...ui, navDraft: navigationDraft().filter(item => item.id !== id) };
  if (action === 'nav-move') ui = { ...ui, navDraft: moveNavigation(navigationDraft(), Number(button.dataset.index), Number(button.dataset.offset)) };
  if (action === 'nav-reset') { ui = { ...ui, navDraft: null }; notify('已恢复本次演示的已保存草稿'); }
  if (action === 'nav-preview') ui = { ...ui, navPreview: button.dataset.value };
  if (action === 'nav-save-draft') {
    try { state = saveNavigationDraft(state, navigationDraft()); ui = { ...ui, navDraft: null }; notify('导航草稿已保存；线上配置未改变'); }
    catch (error) { $('#nav-error').textContent = error.message; return; }
  }
  if (action === 'nav-save') {
    try { state = saveNavigation(state, navigationDraft()); ui = { ...ui, navDraft: null, navPreview: 'published' }; notify('导航已模拟发布生效'); }
    catch (error) { $('#nav-error').textContent = error.message; return; }
  }
  if (action === 'mobile-nav') {
    const nav = previewNavigation().find(item => item.id === id);
    if (nav.type === 'page') {
      const target = nav.target === '@home' ? state.homeId : nav.target;
      if (!state.pages.find(page => page.id === target)?.published) return notify('此链接未发布或不可用，请在后台更换链接');
      ui = { ...ui, screen: 'home', pageId: target, draft: false };
    } else ui = { ...ui, screen: nav.type, draft: false, category: null };
  }
  if (action === 'category-open') ui = { ...ui, category: id, subcategory: id === 'liquor' ? 'beer' : id === 'electronics' ? 'computer' : 'daily' };
  if (action === 'category-search-reset') ui = { ...ui, categoryQuery: '' };
  if (action === 'category-back') ui = { ...ui, category: null };
  if (action === 'subcategory') ui = { ...ui, subcategory: id };
  if (action === 'mobile-back') ui = { ...ui, screen: ui.screen === 'category' ? 'category' : 'home', category: null, pageId: null, draft: false };
  if (action === 'switch-store') return openModal('切换前置仓', `<p>切换后显示对应前置仓的购物车；返回原店保留原商品与数量。普通商城与餐饮桌码商城的购物车互相独立。</p>${state.stores.map(store => `<button class="store-option" data-action="set-store" data-id="${store.id}"><div><strong>${store.name}</strong><small>${store.address}</small></div>${store.id === state.storeId ? '<span class="tag brand">当前</span>' : icon('chevron')}</button>`).join('')}`, btn('取消', 'close'));
  if (action === 'set-store') { state = switchStore(state, id); ui = { ...ui, managing: false }; closeModal(); notify('已切换前置仓与对应购物车'); }
  if (action === 'add-cart') {
    state = { ...state, cart: state.cart.some(line => line.productId === id) ? state.cart.map(line => line.productId === id ? { ...line, quantity: line.quantity + 1 } : line) : [...state.cart, { productId: id, quantity: 1, selected: true }] };
    notify('已加入本次演示的购物车');
  }
  if (action === 'cart-manage') ui = { ...ui, managing: !ui.managing };
  if (action === 'cart-remove') { const result = removeCartItem(state, id, ui.managing); state = result.state; ui = { ...ui, managing: result.managing }; }
  if (action === 'quantity') state = { ...state, cart: state.cart.map(line => line.productId === id ? { ...line, quantity: Math.max(1, Math.min(99, line.quantity + Number(button.dataset.step))) } : line) };
  if (action === 'checkout') { syncCheckout(); ui = { ...ui, screen: 'checkout' }; }
  if (action === 'delivery') state = chooseDelivery(state, id);
  if (action === 'details') ui = { ...ui, details: !ui.details };
  if (action === 'submit-preview') return openModal('确认交互演示', '<p>此按钮仅验证确认流程。继续后不创建订单、不扣库存、不发起微信支付，也不确认任何真实收款。</p>', `${btn('返回', 'close')}${btn('继续演示', 'confirm-preview', '', 'primary')}`);
  if (action === 'confirm-preview') { state = previewSubmit(state); closeModal(); notify(state.previewNotice); }
  if (action === 'center') { state = { ...state, role: button.dataset.role || state.role }; ui = { ...ui, screen: 'center' }; }
  if (action === 'address') {
    if (state.mode === 'dining') return notify('餐饮订单的配送店铺与桌号固定，不允许更换');
    return openModal('选择收货地址', `<button class="store-option" data-action="address-select"><div><strong>李明 · 138****8000</strong><small>${escape(state.checkout.address.split(' · ')[1])}</small></div><span class="tag brand">当前</span></button><p>选址交互示例；不新增或修改真实地址。</p>`, btn('取消', 'close'));
  }
  if (action === 'address-select') { closeModal(); notify('已选中示例地址'); }
  if (action === 'generic' || action === 'order-action') return openModal(label || '管理入口', '<p>已进入当前角色对应的共用样式示例。本原型不执行真实资金、库存、核销、取消或售后操作。</p>', btn('返回', 'close', '', 'primary'));
  render();
}

function syncCheckout() { state = { ...state, checkout: { ...state.checkout, goods: selectedTotal(), delivery: state.mode === 'dining' ? 'store' : state.checkout.delivery } }; }

document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.admin) { ui = { ...ui, admin: button.dataset.admin }; render(); return; }
  if (button.dataset.screen && !button.dataset.action) { ui = { ...ui, screen: button.dataset.screen, pageId: null, draft: false }; if (ui.screen === 'checkout') syncCheckout(); render(); return; }
  if (button.id === 'reset') { state = initialState(); ui = { ...ui, navDraft: null, screen: 'home', pageId: null, draft: false, category: 'liquor', subcategory: 'beer', categoryQuery: '', query: '', status: '' }; render(); notify('演示已重置'); return; }
  if (button.dataset.action) { try { handleAction(button); } catch (error) { notify(error.message); } }
});

document.addEventListener('change', event => {
  const target = event.target;
  if (target.id === 'mode') { state = switchMode(state, target.value); ui = { ...ui, pageId: null, draft: false, managing: false }; syncCheckout(); }
  if (target.id === 'role') state = { ...state, role: target.value };
  if (target.dataset.navLabel) ui = { ...ui, navDraft: navigationDraft().map(item => item.id === target.dataset.navLabel ? { ...item, label: target.value } : item), navPreview: 'draft' };
  if (target.dataset.navTarget) { const [type, selected] = target.value.split(':'); ui = { ...ui, navDraft: navigationDraft().map(item => item.id === target.dataset.navTarget ? { ...item, type, target: selected } : item), navPreview: 'draft' }; }
  if (target.dataset.navIcon) ui = { ...ui, navDraft: navigationDraft().map(item => item.id === target.dataset.navIcon ? { ...item, icon: target.value } : item), navPreview: 'draft' };
  if (target.dataset.navActiveIcon) ui = { ...ui, navDraft: navigationDraft().map(item => item.id === target.dataset.navActiveIcon ? { ...item, activeIcon: target.value } : item), navPreview: 'draft' };
  if (target.id === 'missing-images') ui = { ...ui, missingImages: target.checked, screen: 'category' };
  if (target.id === 'order-status') { state = { ...state, orders: state.orders.map(order => order.mode === state.mode ? { ...order, status: target.value } : order) }; ui = { ...ui, orderStatus: target.value, screen: 'order' }; }
  if (target.id === 'coupon-demo') state = { ...state, checkout: { ...state.checkout, coupon: Math.max(0, Math.min(20, Number(target.value) || 0)) } };
  if (target.dataset.cartSelect) state = { ...state, cart: state.cart.map(line => line.productId === target.dataset.cartSelect ? { ...line, selected: target.checked } : line) };
  if (target.id === 'cart-all') state = { ...state, cart: state.cart.map(line => ({ ...line, selected: target.checked })) };
  if (target.name === 'payment') state = { ...state, checkout: { ...state.checkout, payment: target.value } };
  if (target.closest('#modal')) return;
  render();
});

document.addEventListener('keydown', event => { if (event.key === 'Enter' && event.target.id === 'page-query') handleAction({ dataset: { action: 'query' } }); });
document.addEventListener('input', event => {
  if (event.target.id === 'category-query') {
    ui = { ...ui, categoryQuery: event.target.value };
    $('#category-results').innerHTML = categoryProducts(selectedCategory());
    return;
  }
  if (!editPreview || !['edit-title', 'edit-subtitle'].includes(event.target.id)) return;
  editPreview = { ...editPreview, title: $('#edit-title').value, subtitle: $('#edit-subtitle').value };
  renderPhone();
});
$('#modal').addEventListener('close', () => { editPreview = null; renderPhone(); });
document.addEventListener('error', event => {
  if (event.target.matches?.('.category-banner')) event.target.remove();
  if (event.target.matches?.('.primary-category-strip img')) {
    const fallback = document.createElement('span');
    fallback.className = 'category-icon-fallback';
    fallback.innerHTML = icon('category');
    event.target.replaceWith(fallback);
  }
}, true);
render();
