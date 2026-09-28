const { safeLink } = require('./home')

function pageStack() {
  return typeof getCurrentPages === 'function' ? getCurrentPages() : []
}

function blocked(message) {
  if (typeof wx.showToast === 'function') wx.showToast({ title: message, icon: 'none' })
  return false
}

function navigateInternal(url) {
  if (pageStack().length >= 10) wx.redirectTo({ url })
  else wx.navigateTo({ url })
}

function openLink(candidate, currentPageId = '') {
  const link = safeLink(candidate)
  if (!link) return false
  if (link.type === 'PAGE') {
    const stack = pageStack()
    if (link.targetId === currentPageId || stack.some((page) =>
      page.route === 'pages/micro/detail' && page.options && page.options.pageId === link.targetId)) {
      return blocked('该页面已打开，请返回查看')
    }
    if (stack.length >= 10) return blocked('页面层级已满，请返回后再打开')
    navigateInternal(`/pages/micro/detail?pageId=${link.targetId}`)
    return true
  }
  if (link.type === 'PRODUCT') {
    navigateInternal(`/pages/product/detail?productId=${link.targetId}`)
  } else if (link.type === 'CATEGORY') {
    navigateInternal(`/pages/index/index?categoryId=${link.targetId}`)
  } else if (link.targetId === 'SEARCH') {
    navigateInternal('/pages/index/index?focusSearch=1')
  } else if (link.targetId === 'CATALOG') {
    navigateInternal('/pages/index/index')
  }
  return true
}

module.exports = { openLink, navigateInternal }
