const requirePage = (state, id) => {
  const page = state.pages.find(item => item.id === id);
  if (!page) throw new Error('微页面不存在');
  return page;
};

function demoOrder(mode) {
  return {
    id: mode === 'dining' ? 'DEMO-DINING-202610120001' : 'DEMO-202610120001', mode,
    status: 'PENDING_PAYMENT', delivery: 'store', payment: 'offline',
    createdAt: '2026-10-12 09:30', paymentDeadline: '2026-10-12 21:30',
    paymentInstructions: '示例：按本订单收款说明线下付款，保留凭证并报告付款信息。',
    recipient: { name: '李明', phone: '138****8000', address: '深圳市南山区海德三道18号 302室' },
    store: { id: 'nanshan', name: '南山前置仓', address: '深圳市南山区海德三道 128 号' },
    dining: mode === 'dining' ? { name: '春和小馆', table: '08', address: '深圳市南山区海德三道春和小馆' } : null,
    lines: [{ productId: 'beer', name: '麦野精酿啤酒', spec: '500mL × 6罐', image: 'assets/beer.png', quantity: 1, amount: 68 }],
    amounts: { goods: 68, shipping: 0, coupon: 0, points: 0 }, amount: 68,
  };
}

export function initialState() {
  const navigation = [{ id: 'nav-home', label: '首页', type: 'page', target: '@home', icon: 'home', activeIcon: 'home-filled' }, { id: 'nav-category', label: '分类', type: 'category', icon: 'category', activeIcon: 'category-filled' }, { id: 'nav-cart', label: '购物车', type: 'cart', icon: 'cart', activeIcon: 'cart-filled' }, { id: 'nav-me', label: '我的', type: 'me', icon: 'me', activeIcon: 'me-filled' }];
  return {
    homeId: 'home', mode: 'store', storeId: 'nanshan', role: 'customer',
    dining: { name: '春和小馆', table: '08', storeId: 'nanshan', address: '深圳市南山区海德三道春和小馆 · 08桌', restaurantAllowedIds: ['beer'], openingHours: '10:00—22:00（示例）' },
    stores: [{ id: 'nanshan', name: '南山前置仓', address: '深圳市南山区海德三道 128 号', openingHours: '09:00—22:00（示例）' }, { id: 'futian', name: '福田前置仓', address: '深圳市福田区彩田路 66 号', openingHours: '09:00—22:00（示例）' }],
    pages: [
      { id: 'home', name: '商城首页（原首页）', draft: { title: '酒娃子精选', subtitle: '品质好酒，送到身边', revision: 3 }, published: { title: '酒娃子精选', subtitle: '品质好酒，送到身边', revision: 2 }, history: [{ title: '酒娃子商城', subtitle: '附近好酒', revision: 1 }] },
      { id: 'theme', name: '精选酒水', draft: { title: '微醺时刻', subtitle: '相聚的好酒，在这里', revision: 2 }, published: { title: '微醺时刻', subtitle: '相聚的好酒，在这里', revision: 2 }, history: [{ title: '精选酒水', subtitle: '饭桌上的好滋味', revision: 1 }] },
      { id: 'new', name: '秋日上新', draft: { title: '秋日上新', subtitle: '更多新品，敬请期待', revision: 1 }, published: null, history: [] },
    ],
    navigation, navigationDraft: navigation.map(item => ({ ...item })),
    products: [
      { id: 'beer', name: '麦野精酿啤酒', spec: '500mL × 6罐', price: 68, category: 'beer', image: 'assets/beer.png', stores: ['nanshan', 'futian'] },
      { id: 'wine', name: '云酿浓香白酒', spec: '52° · 500mL', price: 128, category: 'baijiu', image: 'assets/wine.png', stores: ['nanshan'] },
      { id: 'gift', name: '云酿双瓶礼盒', spec: '500mL × 2瓶', price: 268, category: 'baijiu', image: 'assets/gift.png', stores: ['futian'] },
    ],
    categories: [
      { id: 'liquor', name: '酒水', image: 'assets/wine.png', children: [{ id: 'beer', name: '啤酒', image: 'assets/beer-banner.png' }, { id: 'baijiu', name: '白酒', image: 'assets/baijiu-banner.png' }] },
      { id: 'electronics', name: '数码', image: 'assets/electronics-square.png', children: [{ id: 'computer', name: '电脑', image: 'assets/computer-banner.png' }, { id: 'phone', name: '手机', image: '' }] },
      { id: 'daily', name: '日用品', image: '', children: [{ id: 'daily', name: '居家日用', image: '' }] },
      { id: 'beverages', name: '饮料', image: '', children: [{ id: 'soft-drinks', name: '饮品', image: '' }] },
      { id: 'food', name: '食品', image: '', children: [{ id: 'snacks', name: '休闲食品', image: '' }] },
      { id: 'care', name: '个护', image: '', children: [{ id: 'personal-care', name: '个人护理', image: '' }] },
    ],
    cart: [{ productId: 'beer', quantity: 1, selected: true }],
    cartBuckets: {},
    checkout: { delivery: 'store', payment: 'wechat', address: '李明 138****8000 · 深圳市南山区海德三道18号 302室', goods: 68, shipping: 0, coupon: 0, points: 0 },
    orders: [demoOrder('store'), demoOrder('dining')],
    previewNotice: '',
  };
}

export function editPage(state, id, patch) {
  const page = requirePage(state, id);
  if (patch.title !== undefined && !String(patch.title).trim()) throw new Error('页面名称不能为空');
  return { ...state, pages: state.pages.map(item => item.id === id ? {
    ...page, draft: { ...page.draft, ...patch, revision: page.draft.revision + 1 },
  } : item) };
}

export function publishPage(state, id) {
  const page = requirePage(state, id);
  return { ...state, pages: state.pages.map(item => item.id === id ? {
    ...page, published: { ...page.draft }, history: page.published ? [...page.history, { ...page.published }] : [...page.history],
  } : item) };
}

export function setHome(state, id) {
  const page = requirePage(state, id);
  if (!page.published) throw new Error('请先发布此微页面，再指定为首页');
  return { ...state, homeId: id };
}

export function saveNavigationDraft(state, navigation) {
  if (!Array.isArray(navigation)) throw new Error('导航必须为数组');
  if (navigation.length > 5) throw new Error('底部导航最多5项');
  if (!navigation.length) throw new Error('至少保留1个导航入口');
  if (new Set(navigation.map(item => item.id)).size !== navigation.length) throw new Error('导航编号重复');
  navigation.forEach(item => {
    if (!item.label.trim()) throw new Error('导航名称不能为空');
    if (!['page', 'category', 'cart', 'me'].includes(item.type)) throw new Error('导航链接类型不支持');
    const allowed = ['home', 'page', 'category', 'cart', 'me', 'box', 'wallet'].flatMap(name => [name, `${name}-filled`]);
    if ((item.icon && !allowed.includes(item.icon)) || (item.activeIcon && !allowed.includes(item.activeIcon))) throw new Error('请选择受控SVG图标');
  });
  return { ...state, navigationDraft: navigation.map(item => ({ ...item })) };
}

export function saveNavigation(state, navigation) {
  const draft = saveNavigationDraft(state, navigation);
  navigation.forEach(item => {
    if (item.type === 'page') {
      const page = state.pages.find(page => page.id === (item.target === '@home' ? state.homeId : item.target));
      if (!page?.published) throw new Error('未发布微页面不能作为导航链接，请先发布或更换链接');
    }
  });
  return { ...draft, navigation: navigation.map(item => ({ ...item })) };
}

export function orderSnapshot(state) {
  return state.orders.find(order => order.mode === state.mode);
}

export function removeCartItem(state, id, managing) {
  const cart = state.cart.filter(line => line.productId !== id);
  return { state: { ...state, cart }, managing: cart.length > 0 && managing };
}

export function moveNavigation(navigation, index, offset) {
  const target = index + offset;
  if (target < 0 || target >= navigation.length) return [...navigation];
  return navigation.map((item, current) => current === index ? navigation[target] : current === target ? navigation[index] : item);
}

export function switchStore(state, id) {
  if (state.mode === 'dining') throw new Error('餐饮模式的店铺与桌号由桌码固定，不能切换');
  if (!state.stores.some(item => item.id === id)) throw new Error('前置仓不存在');
  return switchCartContext(state, { storeId: id });
}

function cartContextKey(state) {
  return state.mode === 'dining'
    ? `dining:${state.dining.storeId}:${state.dining.name}:${state.dining.table}`
    : `store:${state.storeId}`;
}

function switchCartContext(state, context) {
  const next = { ...state, ...context };
  const buckets = { ...state.cartBuckets, [cartContextKey(state)]: state.cart.map(line => ({ ...line })) };
  const cart = (buckets[cartContextKey(next)] || []).map(line => ({ ...line }));
  return { ...next, cartBuckets: buckets, cart };
}

export function switchMode(state, mode) {
  if (!['store', 'dining'].includes(mode)) throw new Error('访问场景不支持');
  return switchCartContext(state, { mode, checkout: { ...state.checkout, delivery: 'store' } });
}

export function visibleProducts(state) {
  return state.products.filter(product => product.stores.includes(state.mode === 'dining' ? state.dining.storeId : state.storeId)
    && (state.mode !== 'dining' || state.dining.restaurantAllowedIds.includes(product.id)));
}

export function categoryView(state, { primaryId, secondaryId, query = '' } = {}) {
  const primary = state.categories.find(item => item.id === primaryId) || state.categories[0];
  const secondary = primary.children.find(item => item.id === secondaryId) || primary.children[0];
  const keyword = String(query).trim().toLocaleLowerCase();
  const products = visibleProducts(state).filter(product => product.category === secondary.id
    && product.name.toLocaleLowerCase().includes(keyword));
  return { primary, secondary, products };
}

export function navigationIcons(item) {
  const fallback = item.type === 'page' ? item.target === '@home' ? 'home' : 'page' : item.type;
  const icon = item.icon || fallback;
  return { icon, activeIcon: item.activeIcon || `${icon.replace(/-filled$/, '')}-filled` };
}

export function chooseDelivery(state, delivery) {
  if (!['store', 'express', 'pickup'].includes(delivery)) throw new Error('配送方式不支持');
  if (state.mode === 'dining' && delivery !== 'store') throw new Error('餐饮订单配送至当前店铺桌号');
  return { ...state, checkout: { ...state.checkout, delivery } };
}

export function orderActions(role, status) {
  if (role === 'customer') {
    if (status === 'PENDING_PAYMENT') return ['付款', '取消订单'];
    if (status === 'PAID') return ['申请售后'];
    if (status === 'DELIVERED') return ['确认收货', '申请售后'];
    return [];
  }
  if (role === 'store') return status === 'PAID' ? ['备货', '配送交付'] : ['查看订单'];
  if (role === 'dining') return status === 'PAID' ? ['查看订单', '查看佣金'] : ['查看订单'];
  return [];
}

export function amountLines({ goods, shipping, coupon, points }) {
  return [
    { key: 'goods', label: '商品金额', amount: goods }, { key: 'shipping', label: '配送费', amount: shipping },
    ...(coupon ? [{ key: 'coupon', label: '优惠券', amount: -coupon }] : []),
    ...(points ? [{ key: 'points', label: '积分抵扣', amount: -points }] : []),
    { key: 'total', label: '应付金额', amount: goods + shipping - coupon - points },
  ];
}

export function previewSubmit(state) {
  return { ...state, previewNotice: '已走通确认交互；未创建订单、未扣库存、未发起支付。' };
}

export function categoryScrollPosition(current, left, itemWidth, viewportWidth, totalWidth) {
  const visible = left < current ? left : left + itemWidth > current + viewportWidth ? left + itemWidth - viewportWidth : current;
  return Math.min(Math.max(0, totalWidth - viewportWidth), Math.max(0, visible));
}
