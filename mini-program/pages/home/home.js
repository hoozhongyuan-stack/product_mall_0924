const api = require('../../lib/api')
const home = require('../../lib/home')
const pageLinks = require('../../lib/page-links')
const startup = require('../../lib/startup')

Page({
  data: {
    state: 'loading', error: '', components: [], versionId: '',
    theme: { pageBackgroundColor: '#FFF5E8', headerBackgroundColor: '#B63F32',
      brandTextColor: '#FFF8EF' },
    keyword: '', failedAssets: {},
  },

  onLoad() {
    if (startup.redirectIfPending(() => this.onLoad())) return
    this.homeToken = 0
    this.shown = false
    return this.loadHome()
  },

  onShow() {
    if (startup.redirectIfPending()) return
    if (this.shown) return this.loadHome()
    this.shown = true
  },

  onPullDownRefresh() {
    this.loadHome().finally(() => wx.stopPullDownRefresh())
  },

  async loadHome() {
    const token = ++this.homeToken
    this.setData({ state: 'loading', error: '' })
    try {
      const data = await api.get('/api/v1/app/home')
      if (token !== this.homeToken) return
      const content = home.homeContent(data.config, api.baseUrl())
      this.setData({ ...content, versionId: data.versionId, state: 'ready', failedAssets: {} })
    } catch (error) {
      if (token !== this.homeToken) return
      this.setData({ state: error.statusCode === 404 ? 'unpublished' : 'error',
        error: error.message, components: [], versionId: '' })
    }
  },

  onSearchInput(event) { this.setData({ keyword: event.detail.value }) },

  submitSearch() {
    const keyword = this.data.keyword.trim()
    const suffix = keyword ? `?keyword=${encodeURIComponent(keyword)}` : '?focusSearch=1'
    pageLinks.navigateInternal(`/pages/index/index${suffix}`)
  },

  browseCatalog() { pageLinks.navigateInternal('/pages/index/index') },
  openCart() { pageLinks.navigateInternal('/pages/cart/cart') },
  openOrders() { pageLinks.navigateInternal('/pages/orders/list') },
  openMember() { pageLinks.navigateInternal('/pages/member/index') },

  openLink(event) {
    pageLinks.openLink(event.currentTarget.dataset.link)
  },

  onImageError(event) {
    const url = event.currentTarget.dataset.url
    this.setData({ failedAssets: { ...this.data.failedAssets, [url]: true } })
  },
})
