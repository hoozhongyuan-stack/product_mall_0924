const { safeLink } = require('./home')
const assetPath = /^\/api\/v1\/app\/assets\/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\/file$/
const fallback = { title: '商城首页', path: '/pages/home/home' }

function publicCover(value, baseUrl) {
  if (typeof value !== 'string') return ''
  const base = baseUrl.replace(/\/$/, '')
  const path = value.startsWith(`${base}/`) ? value.slice(base.length) : value
  return assetPath.test(path) ? `${base}${path}` : ''
}
function contentShare(value, title, baseUrl) {
  const data = value && typeof value === 'object' ? value : {}
  return { title: typeof data.title === 'string' && data.title.trim() ? data.title.trim().slice(0, 100) : title,
    imageUrl: publicCover(data.coverUrl, baseUrl) }
}
function valid(data) { return (data.state === 'ready' || data.state === 'empty') && !!data.versionId }
function message(data, pageId) {
  const link = pageId ? safeLink({ type: 'PAGE', targetId: pageId }) : null
  if (!valid(data) || (pageId && !link)) return { ...fallback }
  return { title: data.share.title, path: link ? `/pages/micro/detail?pageId=${link.targetId}` : fallback.path,
    ...(data.share.imageUrl ? { imageUrl: data.share.imageUrl } : {}) }
}
function timeline(data, pageId) {
  const result = message(data, pageId)
  if (!valid(data) || (pageId && result.path === fallback.path)) return { title: fallback.title }
  return { title: result.title, ...(result.imageUrl ? { imageUrl: result.imageUrl } : {}),
    ...(pageId ? { query: `pageId=${safeLink({ type: 'PAGE', targetId: pageId }).targetId}` } : {}) }
}
function hide() { if (typeof wx.hideShareMenu === 'function') wx.hideShareMenu() }
function show() { if (typeof wx.showShareMenu === 'function') wx.showShareMenu({ menus: ['shareAppMessage', 'shareTimeline'] }) }
module.exports = { contentShare, message, timeline, hide, show }
