const api = require('../../lib/api')
const home = require('../../lib/home')
const pageLinks = require('../../lib/page-links')
const startup = require('../../lib/startup')

Page({
  data: {
    state: 'loading', error: '', pageId: '', versionId: '', name: '',
    components: [], keyword: '', failedAssets: {},
    theme: { pageBackgroundColor: '#FFF5E8', headerBackgroundColor: '#B63F32',
      brandTextColor: '#FFF8EF' },
  },

  onLoad(options = {}) {
    if (startup.redirectIfPending(() => this.onLoad(options))) return
    this.requestToken = 0
    this.shown = false
    const link = home.safeLink({ type: 'PAGE', targetId: options.pageId })
    if (!link) {
      this.setData({ state: 'error', error: '页面地址无效，请返回后重新打开。' })
      return Promise.resolve()
    }
    this.pageId = link.targetId
    this.setData({ pageId: this.pageId })
    return this.loadPage()
  },

  onShow() {
    if (startup.redirectIfPending()) return
    if (this.shown) return this.loadPage()
    this.shown = true
  },

  onUnload() { this.requestToken += 1 },

  onPullDownRefresh() {
    this.loadPage().finally(() => wx.stopPullDownRefresh())
  },

  async loadPage() {
    if (!this.pageId) return
    const token = ++this.requestToken
    this.setData({ state: 'loading', error: '' })
    try {
      const data = await api.get(`/api/v1/app/pages/${this.pageId}`)
      if (token !== this.requestToken) return
      if (data.pageId !== this.pageId || !data.versionId || !data.config ||
          typeof data.config !== 'object') throw new Error('页面内容暂不可用，请稍后重试。')
      const content = home.homeContent(data.config, api.baseUrl())
      const name = typeof data.name === 'string' && data.name.trim() ? data.name.trim() : '专题页面'
      this.setData({ ...content, state: content.components.length ? 'ready' : 'empty',
        name, versionId: data.versionId,
        failedAssets: {} })
      if (typeof wx.setNavigationBarTitle === 'function') wx.setNavigationBarTitle({ title: name })
    } catch (error) {
      if (token !== this.requestToken) return
      this.setData({ state: error.statusCode === 404 ? 'unpublished' : 'error',
        error: error.message, components: [], versionId: '', name: '' })
    }
  },

  onSearchInput(event) { this.setData({ keyword: event.detail.value }) },

  submitSearch() {
    const keyword = this.data.keyword.trim()
    const suffix = keyword ? `?keyword=${encodeURIComponent(keyword)}` : '?focusSearch=1'
    pageLinks.navigateInternal(`/pages/index/index${suffix}`)
  },

  browseCatalog() { pageLinks.navigateInternal('/pages/index/index') },

  goBack() {
    if (typeof getCurrentPages === 'function' && getCurrentPages().length > 1) {
      wx.navigateBack({ delta: 1 })
    } else {
      wx.reLaunch({ url: '/pages/home/home' })
    }
  },

  openLink(event) { pageLinks.openLink(event.currentTarget.dataset.link, this.pageId) },

  onImageError(event) {
    const url = event.currentTarget.dataset.url
    this.setData({ failedAssets: { ...this.data.failedAssets, [url]: true } })
  },
})
