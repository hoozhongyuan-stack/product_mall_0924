const assetIdPattern = /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/
const themeDefaults = {
  pageBackgroundColor: '#FFF5E8',
  headerBackgroundColor: '#B63F32',
  brandTextColor: '#FFF8EF',
}

function color(value, fallback) {
  return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value : fallback
}

function safeLink(link) {
  if (!link || typeof link !== 'object') return null
  if ((link.type === 'PRODUCT' || link.type === 'CATEGORY' || link.type === 'PAGE') &&
      typeof link.targetId === 'string' && assetIdPattern.test(link.targetId)) {
    return { type: link.type, targetId: link.targetId }
  }
  if (link.type === 'FUNCTION' && ['SEARCH', 'CATALOG'].includes(link.targetId)) {
    return { type: link.type, targetId: link.targetId }
  }
  return null
}

function imageUrl(baseUrl, assetId) {
  if (!assetIdPattern.test(String(assetId || ''))) return ''
  return `${baseUrl.replace(/\/$/, '')}/api/v1/app/assets/${assetId}/file`
}

function hotArea(area) {
  const { x, y, width, height } = area || {}
  if (![x, y, width, height].every((value) => typeof value === 'number' && Number.isFinite(value) &&
      value >= 0 && value <= 1) || !width || !height || x + width > 1 || y + height > 1) return null
  return { style: `left:${x * 100}%;top:${y * 100}%;width:${width * 100}%;height:${height * 100}%;`,
    link: safeLink(area.link) }
}

function appearanceStyle(input) {
  const value = input && typeof input === 'object' ? input : {}
  const spacing = (name) => Number.isInteger(value[name]) && value[name] >= 0 && value[name] <= 64
    ? `${name === 'radius' ? 'border-radius' : name}:${value[name] * 2}rpx;` : ''
  return (color(value.backgroundColor, '') ? `background-color:${value.backgroundColor};` : '') +
    ['padding', 'margin', 'radius'].map(spacing).join('')
}

function productImageUrl(value, baseUrl) {
  if (typeof value !== 'string') return ''
  if (/^https?:\/\//.test(value)) return value
  const match = /^\/api\/v1\/app\/assets\/([^/]+)\/file$/.exec(value)
  return match ? imageUrl(baseUrl, match[1]) : ''
}

function productCards(data, limit, baseUrl) {
  return (Array.isArray(data) ? data : []).filter((item) => item &&
    assetIdPattern.test(String(item.productId || '')) && typeof item.name === 'string' &&
    Number.isSafeInteger(item.priceFen) && item.priceFen >= 0).slice(0, limit).map((item) => ({
    productId: item.productId, name: item.name, price: (item.priceFen / 100).toFixed(2),
    imageUrl: productImageUrl(item.imageUrl, baseUrl),
    purchasable: item.purchasable === true, link: { type: 'PRODUCT', targetId: item.productId },
  }))
}

function component(item, baseUrl, data) {
  const props = item.props || {}
  const result = { id: item.componentId, type: item.type, sortOrder: item.sortOrder,
    link: safeLink(props.link), appearanceStyle: appearanceStyle(item.appearance) }
  switch (item.type) {
    case 'COUPON_LIST': return { ...result, layout: props.layout === 'SCROLL' ? 'SCROLL' : 'LIST', limit: Number.isInteger(props.limit) && props.limit >= 1 && props.limit <= 10 ? props.limit : 10 }
    case 'MOSAIC': {
      const count = { TWO: 2, THREE: 3, FOUR: 4, FEATURED: 3 }[props.template]
      if (!count || !Array.isArray(props.items) || props.items.length !== count) return { ...result, type: 'UNSUPPORTED' }
      return { ...result, template: props.template,
        gapStyle: `gap:${[0, 4, 8, 12, 16].includes(props.gap) ? props.gap * 2 : 0}rpx;`,
        slotStyle: `width:calc((100% - ${[0, 4, 8, 12, 16].includes(props.gap) ? props.gap * 2 * (props.template === 'THREE' ? 2 : 1) : 0}rpx) / ${props.template === 'THREE' ? 3 : 2});`,
        items: props.items.map(value => { const slot = value && typeof value === 'object' ? value : {}; return {
          imageUrl: imageUrl(baseUrl, slot.assetId), title: typeof slot.title === 'string' ? slot.title.slice(0, 40) : '', link: safeLink(slot.link),
        } }) }
    }
    case 'SPACER': return { ...result, heightStyle: `height:${[4, 8, 12, 16, 24, 32, 48, 64, 96].includes(props.height) ? props.height * 2 : 16}rpx;` }
    case 'TITLE': return { ...result, text: typeof props.text === 'string' ? props.text : '',
      subtitle: typeof props.subtitle === 'string' ? props.subtitle : '',
      titleStyle: `text-align:${props.align === 'CENTER' ? 'center' : 'left'};font-size:${[16, 20, 24].includes(props.size) ? props.size * 2 : 40}rpx;` }
    case 'IMAGE': return { ...result, imageUrl: imageUrl(baseUrl, props.assetId),
      ratio: ['1:1', '16:9'].includes(props.ratio) ? props.ratio : 'AUTO' }
    case 'NAVIGATION': return { ...result, columns: [2, 3, 4].includes(props.columns) ? props.columns : 4,
      items: (Array.isArray(props.items) ? props.items : []).filter((entry) => entry && typeof entry.title === 'string').map((entry) => ({
        title: entry.title, imageUrl: imageUrl(baseUrl, entry.assetId), link: safeLink(entry.link),
      })) }
    case 'PRODUCT_LIST': return { ...result, layout: ['GRID', 'LIST', 'SCROLL'].includes(props.layout) ? props.layout : 'GRID',
      products: productCards(data, Number.isInteger(props.limit) && props.limit >= 1 && props.limit <= 20 ? props.limit : 20, baseUrl) }
    case 'SEARCH': return { ...result, placeholder: props.placeholder || '搜索商品' }
    case 'NOTICE': return { ...result, text: props.text || '' }
    case 'CAROUSEL': return { ...result, slides: (props.slides || []).map((slide) => ({
      imageUrl: imageUrl(baseUrl, slide.assetId), link: safeLink(slide.link),
    })).filter((slide) => slide.imageUrl) }
    case 'IMAGE_HOTZONE': return { ...result, imageUrl: imageUrl(baseUrl, props.assetId),
      areas: (props.areas || []).map(hotArea).filter(Boolean) }
    case 'DIVIDER': return { ...result, dividerStyle:
      ['SOLID', 'DASHED', 'SPACE'].includes(props.style) ? props.style : 'SOLID' }
    case 'FILING': return { ...result, recordNo: props.recordNo || '' }
    default: return { ...result, type: 'UNSUPPORTED' }
  }
}

function homeContent(config, baseUrl, componentData = {}) {
  if (config && config.schemaVersion !== undefined && ![1, 2, 3, 4].includes(config.schemaVersion)) {
    throw new Error('当前页面需要更新小程序版本，请更新后重新打开。')
  }
  const theme = config && config.theme || {}
  const components = (config && Array.isArray(config.components) ? config.components : [])
    .filter((item) => item && item.visible === true)
    .map((item) => component(item, baseUrl, componentData && componentData[item.componentId]))
    .filter(Boolean)
    .sort((left, right) => left.sortOrder - right.sortOrder)
  return {
    theme: {
      pageBackgroundColor: color(theme.pageBackgroundColor, themeDefaults.pageBackgroundColor),
      headerBackgroundColor: color(theme.headerBackgroundColor, themeDefaults.headerBackgroundColor),
      brandTextColor: color(theme.brandTextColor, themeDefaults.brandTextColor),
    },
    components,
  }
}

function contentError(error) {
  return error && ['PAGE_SCHEMA_UNSUPPORTED', 'HOME_SCHEMA_UNSUPPORTED', 'SCHEMA_VERSION_UNSUPPORTED'].includes(error.code)
    ? '当前页面需要更新小程序版本，请更新后重新打开；也可以稍后重试。'
    : error.message
}

module.exports = { homeContent, safeLink, contentError }
