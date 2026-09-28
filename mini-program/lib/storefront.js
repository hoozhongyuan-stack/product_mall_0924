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
  const item = NAVIGATION.find((entry) => entry.key === key)
  return `/assets/nav/${item.icon}${selected ? '-selected' : ''}.svg`
}

function navigationItems(source, base) {
  const values = source && Array.isArray(source.items) ? source.items : []
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
  return { navigation: navigationItems(data.navigation, base),
    service: customerService(data.customerService, base) }
}

module.exports = { NAVIGATION, presentStorefront }
