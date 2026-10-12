export const NAV_ITEMS = Object.freeze([
  Object.freeze({ key: 'HOME', label: '首页' }),
  Object.freeze({ key: 'CATEGORY', label: '分类' }),
  Object.freeze({ key: 'CART', label: '购物车' }),
  Object.freeze({ key: 'ME', label: '我的' }),
])

export function copyConfig(config) {
  return JSON.parse(JSON.stringify(config))
}

export function reconcileSavedConfig(current, submitted, server) {
  const editedDuringSave = JSON.stringify(current) !== JSON.stringify(submitted)
  return {
    config: editedDuringSave ? current : copyConfig(server),
    savedSnapshot: JSON.stringify(server),
    editedDuringSave,
  }
}

export function updateNavigationItem(config, key, patch) {
  return { ...config, items: config.items.map(item => item.key === key ? { ...item, ...patch } : item) }
}

export function normalizeNavigation(config) {
  if (config.schemaVersion === 2) return copyConfig(config)
  return { schemaVersion: 2, items: config.items.map(item => ({ ...item, target: { type: item.key } })) }
}

export function appendNavigationItem(config, key) {
  if (config.items.length >= 5 || config.items.some(item => item.key === key)) return config
  return { ...normalizeNavigation(config), items: [...normalizeNavigation(config).items, { key, label: '新导航', target: { type: 'MICRO', pageId: '' }, iconAssetId: null, selectedIconAssetId: null }] }
}
export function moveNavigationItem(config, key, direction) {
  const index = config.items.findIndex(item => item.key === key), to = index + direction
  if (index < 0 || to < 0 || to >= config.items.length) return config
  const items = config.items.map((item, position) => position === index ? config.items[to] : position === to ? config.items[index] : item)
  return { ...config, items }
}
export function removeNavigationItem(config, key) {
  if (config.items.length <= 1) return config
  return { ...config, items: config.items.filter(item => item.key !== key) }
}

export function validateNavigation(config) {
  if (!config || !Array.isArray(config.items)) return '底部导航配置无效。'
  if (config.schemaVersion !== 2) {
    if (config.items.length !== NAV_ITEMS.length) return '底部导航必须保留四个固定入口。'
    for (let index = 0; index < NAV_ITEMS.length; index += 1) {
      if (config.items[index]?.key !== NAV_ITEMS[index].key) return '底部导航入口顺序不可更改。'
      if (config.items[index].label !== NAV_ITEMS[index].label) return '底部导航名称固定，不可更改。'
    }
  } else {
    if (config.items.length < 1 || config.items.length > 5) return '底部导航需要 1 至 5 项。'
    const keys = config.items.map(item => item?.key)
    if (keys.some(key => typeof key !== 'string' || !/^[A-Za-z0-9_-]{1,40}$/.test(key)) || new Set(keys).size !== keys.length) return '导航标识须唯一且有效。'
    for (const item of config.items) {
      if (typeof item.label !== 'string' || !item.label.trim() || item.label.trim().length > 10 || /[\u0000-\u001f\u007f]/.test(item.label)) return '导航名称需要 1 至 10 字，不能包含控制字符。'
      if (!['HOME', 'MICRO', 'CATEGORY', 'CART', 'ME'].includes(item.target?.type)) return '请选择有效的关联页面。'
      if (item.target.type === 'MICRO' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.target.pageId || '')) return `${item.label}请选择微页面或输入有效页面 ID。`
      if (item.target.type !== 'MICRO' && item.target.pageId) return '固定页面不能同时关联微页面 ID。'
    }
  }
  for (const item of config.items) {
    if (Boolean(item.iconAssetId) !== Boolean(item.selectedIconAssetId)) return `${item.label}的普通图标与选中图标须成对配置。`
  }
  return ''
}

export function validateCustomerService(config) {
  if (!config || typeof config.enabled !== 'boolean') return '客服配置无效，请重新读取草稿。'
  if (typeof config.prompt !== 'string' || config.prompt.trim().length > 20) return '入口文案不能超过 20 字。'
  if (config.mode === null) return config.enabled || config.phone || config.qrAssetId ? '请选择电话或二维码客服方式。' : ''
  if (config.mode === 'PHONE') {
    if (config.qrAssetId) return '电话客服不能同时配置二维码。'
    if (!config.phone && !config.enabled) return ''
    return /^1[3-9]\d{9}$/.test(config.phone || '') ? '' : '请输入有效的 11 位大陆手机号。'
  }
  if (config.mode === 'QR') {
    if (config.phone) return '二维码客服不能同时配置手机号。'
    return !config.enabled || config.qrAssetId ? '' : '请选择客服二维码图片。'
  }
  return '请选择电话或二维码客服方式。'
}
