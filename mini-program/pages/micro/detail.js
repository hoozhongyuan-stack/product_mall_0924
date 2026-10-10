const api = require('../../lib/api')
const home = require('../../lib/home')
const sharing = require('../../lib/published-sharing')
const pageCoupons = require('../../lib/page-coupons')
const pageLinks = require('../../lib/page-links')
const startup = require('../../lib/startup')

Page({
  data: {
    couponState: 'idle', couponData: {}, couponError: '', couponClaim: { busy: false, pending: null, message: '', claimError: '' },
    share: { title: '商城首页', imageUrl: '' }, state: 'loading', error: '', pageId: '', versionId: '', name: '',
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
      sharing.hide()
      pageCoupons.discard(this)
      this.pageId = ''
      this.setData({ pageId: '', versionId: '', state: 'error', error: '页面地址无效，请返回后重新打开。' })
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

  onHide() { this.requestToken += 1; pageCoupons.discard(this) },
  onUnload() { this.onHide() },

  onPullDownRefresh() {
    this.loadPage().finally(() => wx.stopPullDownRefresh())
  },

  async loadPage() {
    if (!this.pageId) return
    const token = ++this.requestToken
    sharing.hide()
    pageCoupons.discard(this)
    this.setData({ state: 'loading', error: '', versionId: '', share: { title: '商城首页', imageUrl: '' } })
    try {
      const data = await api.get(`/api/v1/app/pages/${this.pageId}`, { schemaVersion: 4 })
      if (token !== this.requestToken) return
      if (data.pageId !== this.pageId || !data.versionId || !data.config ||
          typeof data.config !== 'object') throw new Error('页面内容暂不可用，请稍后重试。')
      const content = home.homeContent(data.config, api.baseUrl(), data.componentData)
      const name = typeof data.name === 'string' && data.name.trim() ? data.name.trim() : '专题页面'
      this.setData({ ...content, share: sharing.contentShare(data.share, name, api.baseUrl()), state: content.components.length ? 'ready' : 'empty',
        name, versionId: data.versionId,
        failedAssets: {} })
      sharing.show()
      await pageCoupons.mount(this, this.pageId, data.versionId)
      if (typeof wx.setNavigationBarTitle === 'function') wx.setNavigationBarTitle({ title: name })
    } catch (error) {
      if (token !== this.requestToken) return
      this.setData({ state: error.statusCode === 404 ? 'unpublished' : 'error',
        error: home.contentError(error), components: [], versionId: '', name: '' })
    }
  },

  claimCoupon(event) { return pageCoupons.claim(this, event) },
  retryCouponClaim() { return pageCoupons.recover(this) },
  reloadCoupons() { return this.data.couponRefreshPage ? this.loadPage() : pageCoupons.reload(this) },

  onShareAppMessage() { return sharing.message(this.data, this.data.pageId) },
  onShareTimeline() { return sharing.timeline(this.data, this.data.pageId) },

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
