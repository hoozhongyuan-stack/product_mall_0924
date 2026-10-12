const NAVIGATION = Object.freeze([
  { key: 'HOME', label: '首页', path: '/pages/home/home', icon: 'home' },
  { key: 'CATEGORY', label: '分类', path: '/pages/index/index', icon: 'category' },
  { key: 'CART', label: '购物车', path: '/pages/cart/cart', icon: 'cart' },
  { key: 'ME', label: '我的', path: '/pages/member/index', icon: 'member' },
])

const ASSET_URL = /^\/api\/v1\/app\/assets\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/file$/i
const PHONE = /^1[3-9]\d{9}$/

function publicAssetUrl(value, base) {
  return typeof value === 'string' && ASSET_URL.test(value) ? `${base}${value}` : null
}

function iconUrl(key, selected) {
  const item = NAVIGATION.find((entry) => entry.key === key) || { icon: 'micro' }
  return `/assets/nav/${item.icon}${selected ? '-selected' : ''}.svg`
}

function navigationItems(source, base) {
  const values = source && Array.isArray(source.items) ? source.items : []
  if (source && source.schemaVersion === 2) {
    const valid = values.length > 0 && values.length <= 5 && new Set(values.map(item => item && item.key)).size === values.length &&
      values.every(item => item && typeof item.key === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(item.key) &&
        typeof item.label === 'string' && item.label.trim().length > 0 && item.label.trim().length <= 10 &&
        navigationPath(item.target))
    if (valid) return values.map(item => ({ key: item.key, label: item.label.trim(), target: { ...item.target },
      path: navigationPath(item.target), iconUrl: publicAssetUrl(item.iconUrl, base) || iconUrl(item.target.type, false),
      selectedIconUrl: publicAssetUrl(item.selectedIconUrl, base) || iconUrl(item.target.type, true) }))
  }
  const configured = values.length === NAVIGATION.length && values.every((item, index) =>
    item && item.key === NAVIGATION[index].key)
  return NAVIGATION.map(({ key, label, path }, index) => ({
    key, label, path,
    iconUrl: configured ? publicAssetUrl(values[index].iconUrl, base) ||
      iconUrl(key, false) : iconUrl(key, false),
    selectedIconUrl: configured ?
      publicAssetUrl(values[index].selectedIconUrl, base) ||
      iconUrl(key, true) : iconUrl(key, true),
  }))
}

function navigationPath(target) {
  if (!target || typeof target !== 'object') return ''
  if (target.type === 'MICRO') return typeof target.pageId === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(target.pageId) ?
    `/pages/micro/detail?pageId=${encodeURIComponent(target.pageId)}` : ''
  const item = NAVIGATION.find(entry => entry.key === target.type)
  return item ? item.path : ''
}

function activeNavigation(items, page) {
  if (!page) return ''
  const route = page.route === 'pages/dining/shop' ? '/pages/home/home' : `/${page.route}`
  const item = items.find(entry => entry.path.split('?')[0] === route &&
    (entry.target?.type !== 'MICRO' || entry.target.pageId === (page.options || {}).pageId))
  return item ? item.key : ''
}

function customerService(source, base) {
  if (!source || source.enabled !== true) return null
  const icon = publicAssetUrl(source.iconUrl, base) || '/assets/nav/service.svg'
  const prompt = typeof source.prompt === 'string' && source.prompt.trim().length <= 20 &&
    source.prompt.trim() ? source.prompt.trim() : '联系客服'
  if (source.mode === 'PHONE' && typeof source.phone === 'string' && PHONE.test(source.phone)) {
    return { mode: 'PHONE', phone: source.phone, qrUrl: null, prompt, iconUrl: icon }
  }
  if (source.mode === 'QR') {
    const qrUrl = publicAssetUrl(source.qrUrl, base)
    return qrUrl ? { mode: 'QR', phone: null, qrUrl, prompt, iconUrl: icon } : null
  }
  return null
}

function presentStorefront(payload, base) {
  const data = payload && typeof payload === 'object' ? payload : {}
  return { recordNo: data.filing && typeof data.filing.recordNo === 'string' ? data.filing.recordNo.trim().slice(0, 100) : '',
    navigation: navigationItems(data.navigation, base),
    service: customerService(data.customerService, base) }
}

module.exports = { NAVIGATION, presentStorefront, activeNavigation }
