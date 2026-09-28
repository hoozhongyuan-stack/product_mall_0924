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

export function validateNavigation(config) {
  if (!config || !Array.isArray(config.items) || config.items.length !== NAV_ITEMS.length) return '底部导航必须保留四个固定入口。'
  for (let index = 0; index < NAV_ITEMS.length; index += 1) {
    const item = config.items[index]
    if (item?.key !== NAV_ITEMS[index].key) return '底部导航入口顺序不可更改。'
    if (item.label !== NAV_ITEMS[index].label) return '底部导航名称固定，不可更改。'
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
