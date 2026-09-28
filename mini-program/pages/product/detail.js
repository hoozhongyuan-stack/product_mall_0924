const api = require('../../lib/api')
const catalog = require('../../lib/catalog')
const startup = require('../../lib/startup')
const cart = require('../../lib/cart')

Page({
  data: {
    state: 'loading', error: '', product: null,
    selectedSkuId: '', selectedPrice: '', selectedUnit: '', selectedAvailable: 0, selectedEligible: false,
    mediaIndex: 0, failedImages: {}, videoFailed: false, quantity: 1,
  },

  onLoad(options) {
    if (startup.redirectIfPending(() => this.onLoad(options))) return
    this.productId = options.productId || ''
    this.shown = false
    this.detailToken = 0
    return this.loadDetail()
  },

  onShow() {
    if (startup.redirectIfPending()) return
    if (this.shown) this.loadDetail()
    this.shown = true
  },

  onPullDownRefresh() {
    this.loadDetail().finally(() => wx.stopPullDownRefresh())
  },

  async loadDetail() {
    const token = ++this.detailToken
    if (!/^[0-9a-fA-F-]{36}$/.test(this.productId)) {
      this.setData({ state: 'unavailable', error: '商品链接无效。' })
      return
    }
    this.setData({ state: 'loading', error: '' })
    try {
      const result = await api.get(`/api/v1/app/products/${encodeURIComponent(this.productId)}`)
      if (token !== this.detailToken) return
      const product = catalog.productDetail(result, api.baseUrl())
      const selected = product.skus.find((sku) => sku.id === this.data.selectedSkuId) ||
        product.skus.find((sku) => sku.id === product.defaultSkuId)
      this.setData({ state: 'ready', product,
        selectedSkuId: selected ? selected.id : '',
        selectedPrice: selected ? selected.price : product.price,
        selectedUnit: selected ? selected.unit : '',
        selectedAvailable: selected ? selected.availableQuantity : 0,
        selectedEligible: selected ? selected.cartEligible : false,
        mediaIndex: 0, failedImages: {}, videoFailed: false })
      wx.setNavigationBarTitle({ title: product.name })
    } catch (error) {
      if (token !== this.detailToken) return
      this.setData({ state: error.statusCode === 404 ? 'unavailable' : 'error', error: error.message,
        product: null })
    }
  },

  chooseSku(event) {
    const product = this.data.product
    if (!product) return
    const selected = product.skus.find((sku) => sku.id === event.currentTarget.dataset.id)
    if (selected) this.setData({ selectedSkuId: selected.id,
      selectedPrice: selected.price, selectedUnit: selected.unit,
      selectedAvailable: selected.availableQuantity, selectedEligible: selected.cartEligible })
  },

  changeQuantity(event) {
    const next = this.data.quantity + Number(event.currentTarget.dataset.step)
    if (next >= 1 && next <= 9999) this.setData({ quantity: next })
  },
  selection() {
    const product = this.data.product
    const sku = product && product.skus.find((item) => item.id === this.data.selectedSkuId)
    if (!sku) throw new Error('请先选择规格。')
    if (!sku.cartEligible || this.data.quantity > sku.availableQuantity) throw new Error('当前库存不足，请调整规格或数量。')
    return { skuId: sku.id, quantity: this.data.quantity, seenPriceFen: sku.priceFen,
      name: product.name, imageUrl: product.images[0] || '', spec: sku.label, unit: sku.unit }
  },
  addToCart() {
    try {
      cart.add(this.selection())
      wx.showToast({ title: '已加入购物车', icon: 'success' })
    } catch (error) { wx.showToast({ title: error.message, icon: 'none' }) }
  },
  buyNow() {
    try {
      wx.setStorageSync('mall.checkoutSelection.v1', [this.selection()])
      wx.navigateTo({ url: '/pages/checkout/checkout' })
    } catch (error) { wx.showToast({ title: error.message, icon: 'none' }) }
  },
  openCart() { wx.navigateTo({ url: '/pages/cart/cart' }) },

  onMediaChange(event) { this.setData({ mediaIndex: event.detail.current }) },
  onImageError(event) {
    const index = event.currentTarget.dataset.index
    this.setData({ failedImages: { ...this.data.failedImages, [index]: true } })
  },
  onVideoError() { this.setData({ videoFailed: true }) },
  backToCatalog() {
    if (getCurrentPages().length > 1) wx.navigateBack({ delta: 1 })
    else wx.reLaunch({ url: '/pages/index/index' })
  },
})
