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

function component(item, baseUrl) {
  const props = item.props || {}
  const result = { id: item.componentId, type: item.type, sortOrder: item.sortOrder,
    link: safeLink(props.link) }
  switch (item.type) {
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
    default: return null
  }
}

function homeContent(config, baseUrl) {
  const theme = config && config.theme || {}
  const components = (config && Array.isArray(config.components) ? config.components : [])
    .filter((item) => item && item.visible === true)
    .map((item) => component(item, baseUrl))
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

module.exports = { homeContent, safeLink }
