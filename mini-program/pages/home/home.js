const stores = require('../../lib/stores')
const api = require('../../lib/api')
const home = require('../../lib/home')
const sharing = require('../../lib/published-sharing')
const pageCoupons = require('../../lib/page-coupons')
const pageLinks = require('../../lib/page-links')
const startup = require('../../lib/startup')

Page({
  data: {
    couponState: 'idle', couponData: {}, couponError: '', couponClaim: { busy: false, pending: null, message: '', claimError: '' },
    share: { title: '商城首页', imageUrl: '' }, state: 'loading', error: '', components: [], versionId: '',
    theme: { pageBackgroundColor: '#FFF5E8', headerBackgroundColor: '#B63F32',
      brandTextColor: '#FFF8EF' },
    keyword: '', failedAssets: {}, store: null, storeError: '', capsuleTop: 30, capsuleRight: 190, headerPadding: 52,
  },

  onLoad() {
    if (startup.redirectIfPending(() => this.onLoad())) return
    this.homeToken = 0
    this.shown = false
    return this.loadStore().then(() => this.loadHome())
  },

  async loadStore() {
    const generation = this.storeGeneration = (this.storeGeneration || 0) + 1
    const originalId = (stores.selected() || {}).id || null
    const valid = () => generation === this.storeGeneration && ((stores.selected() || {}).id || null) === originalId
    try {
      let selected = stores.selected()
      if (!selected) {
        const result = await api.get('/api/v1/app/stores')
        let location = null
        try { location = await stores.locate() } catch (e) { this.setData({ storeError: e.message }) }
        selected = location ? stores.rank(result.items.filter(s => s.acceptingOrders && s.supportedModes && s.supportedModes.length), location)[0] || null : null
        if (!valid()) return
        if (selected) stores.select(selected)
      } else { selected = await api.get(`/api/v1/app/stores/${encodeURIComponent(selected.id)}`); if (!valid()) return; stores.select(selected) }
      const rect = wx.getMenuButtonBoundingClientRect ? wx.getMenuButtonBoundingClientRect() : null
      const info = wx.getWindowInfo ? wx.getWindowInfo() : null
      this.setData({ store: selected, ...(selected ? { storeError: '' } : {}), ...(rect ? { capsuleTop: rect.top, headerPadding: rect.top, capsuleRight: info ? info.windowWidth - rect.left + 12 : 190 } : {}) })
    } catch (e) { if (valid()) this.setData({ store: null, storeError: e.message }) }
  },
  chooseStore() { wx.navigateTo({ url: '/pages/stores/list' }) },
  onShow() {
    if (startup.redirectIfPending()) return
    if (this.shown) return this.loadStore().then(() => this.loadHome())
    this.shown = true
  },

  onHide() { this.storeGeneration = (this.storeGeneration || 0) + 1; this.homeToken += 1; pageCoupons.discard(this) },
  onUnload() { this.onHide() },

  onPullDownRefresh() {
    this.loadHome().finally(() => wx.stopPullDownRefresh())
  },

  async loadHome() {
    const token = ++this.homeToken
    if (!this.data.store && stores.selected()) { this.setData({ state: 'error', error: this.data.storeError || '所选门店暂不可用，请重新选择门店。', components: [] }); return }
    sharing.hide()
    pageCoupons.discard(this)
    this.setData({ state: 'loading', error: '', versionId: '', share: { title: '商城首页', imageUrl: '' } })
    try {
      const data = await api.get('/api/v1/app/home', { schemaVersion: 4, ...stores.query() })
      if (token !== this.homeToken) return
      if (!data.versionId || !data.config || typeof data.config !== 'object') throw new Error('首页内容暂不可用，请稍后重试。')
      const content = home.homeContent(data.config, api.baseUrl(), data.componentData)
      this.setData({ ...content, share: sharing.contentShare(data.share, data.shopName || '商城首页', api.baseUrl()), versionId: data.versionId, state: 'ready', failedAssets: {} })
      sharing.show()
      await pageCoupons.mount(this, 'home', data.versionId)
    } catch (error) {
      if (token !== this.homeToken) return
      this.setData({ state: error.statusCode === 404 ? 'unpublished' : 'error',
        error: home.contentError(error), components: [], versionId: '' })
    }
  },

  claimCoupon(event) { return pageCoupons.claim(this, event) },
  retryCouponClaim() { return pageCoupons.recover(this) },
  reloadCoupons() { return this.data.couponRefreshPage ? this.loadHome() : pageCoupons.reload(this) },

  onShareAppMessage() { return sharing.message(this.data) },
  onShareTimeline() { return sharing.timeline(this.data) },

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
