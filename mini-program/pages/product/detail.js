const stores = require('../../lib/stores')
const api = require('../../lib/api')
const catalog = require('../../lib/catalog')
const startup = require('../../lib/startup')
const cart = require('../../lib/cart')

Page({
  data: {
    state: 'loading', error: '', product: null,
    selectedSkuId: '', selectedPrice: '', selectedUnit: '', selectedAvailable: 0, selectedEligible: false,
    mediaIndex: 0, failedImages: {}, videoFailed: false, quantity: 1,
    drawerOpen: false, drawerError: '', selectedLabel: '', cartCount: 0, replacingCartItem: false,
  },

  onLoad(options) {
    if (startup.redirectIfPending(() => this.onLoad(options))) return
    this.productId = options.productId || ''
    this.openSelectionOnLoad = options.selectSku === '1'
    if (typeof options.replaceSkuId === 'string' && options.replaceSkuId) {
      const source = cart.read().find((row) => row.skuId === options.replaceSkuId)
      this.originalCartRow = source ? { ...source } : null
      this.setData({ replacingCartItem: true, selectedSkuId: source ? source.skuId : '', quantity: source ? source.quantity : 1 })
    }
    this.shown = false
    this.detailToken = 0
    return this.loadDetail()
  },

  onShow() {
    if (startup.redirectIfPending()) return
    this.updateCartCount()
    if (this.shown) return this.loadDetail()
    this.shown = true
  },

  onUnload() { this.detailToken += 1; this.setData({ drawerOpen: false }) },

  onPullDownRefresh() {
    this.loadDetail().finally(() => wx.stopPullDownRefresh())
  },

  async loadDetail() {
    const token = ++this.detailToken
    if (!/^[0-9a-fA-F-]{36}$/.test(this.productId)) {
      this.setData({ state: 'unavailable', error: '商品链接无效。' })
      return
    }
    this.closeSkuDrawer()
    this.setData({ state: 'loading', error: '' })
    try {
      const result = await api.get(`/api/v1/app/products/${encodeURIComponent(this.productId)}`, stores.query())
      if (token !== this.detailToken) return
      const product = catalog.productDetail(result, api.baseUrl())
      const selected = product.skus.find((sku) => sku.id === this.data.selectedSkuId && sku.cartEligible && sku.availableQuantity > 0) ||
        product.skus.find((sku) => sku.cartEligible && sku.availableQuantity > 0) ||
        product.skus.find((sku) => sku.id === product.defaultSkuId)
      this.setData({ state: 'ready', product,
        selectedSkuId: selected ? selected.id : '', selectedLabel: selected ? selected.label : '',
        quantity: Math.max(1, Math.min(this.data.quantity, selected ? selected.availableQuantity : 1)),
        selectedPrice: selected ? selected.price : product.price,
        selectedUnit: selected ? selected.unit : '',
        selectedAvailable: selected ? selected.availableQuantity : 0,
        selectedEligible: selected ? selected.cartEligible : false,
        mediaIndex: 0, failedImages: {}, videoFailed: false })
      this.updateCartCount()
      wx.setNavigationBarTitle({ title: product.name })
      if (this.openSelectionOnLoad) { this.openSelectionOnLoad = false; this.openSkuDrawer() }
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
    if (selected && selected.cartEligible && selected.availableQuantity > 0) this.setData({ selectedSkuId: selected.id,
      selectedLabel: selected.label, quantity: Math.min(this.data.quantity, selected.availableQuantity), drawerError: '',
      selectedPrice: selected.price, selectedUnit: selected.unit,
      selectedAvailable: selected.availableQuantity, selectedEligible: selected.cartEligible })
  },

  changeQuantity(event) {
    const next = this.data.quantity + Number(event.currentTarget.dataset.step)
    if (Number.isInteger(next) && next >= 1 && next <= Math.min(9999, this.data.selectedAvailable)) this.setData({ quantity: next, drawerError: '' })
  },
  selection() {
    const product = this.data.product
    const sku = product && product.skus.find((item) => item.id === this.data.selectedSkuId)
    if (!sku) throw new Error('请先选择规格。')
    if (!sku.cartEligible || this.data.quantity > sku.availableQuantity) throw new Error('当前库存不足，请调整规格或数量。')
    return { skuId: sku.id, quantity: this.data.quantity, seenPriceFen: sku.priceFen,
      name: product.name, imageUrl: product.images[0] || '', spec: sku.label, unit: sku.unit }
  },
  updateCartCount() {
    const rows = wx.getStorageSync ? cart.read() : []
    this.setData({ cartCount: rows.reduce((total, row) => total + row.quantity, 0) })
  },
  openSkuDrawer() {
    if (this.data.state !== 'ready' || !this.data.product || this.data.drawerOpen) return
    const { selectedSkuId, selectedLabel, selectedPrice, selectedUnit, selectedAvailable, selectedEligible, quantity } = this.data
    this.selectionBeforeDrawer = { selectedSkuId, selectedLabel, selectedPrice, selectedUnit, selectedAvailable, selectedEligible, quantity }
    this.setData({ drawerOpen: true, drawerError: '' })
  },
  closeSkuDrawer() {
    this.setData({ ...this.selectionBeforeDrawer, drawerOpen: false, drawerError: '' })
    this.selectionBeforeDrawer = null
  },
  finishSelection() { this.selectionBeforeDrawer = null; this.setData({ drawerOpen: false, drawerError: '' }) },
  stopPropagation() {},
  addToCart() { this.openSkuDrawer() },
  buyNow() { this.openSkuDrawer() },
  confirmAddToCart() {
    if (!this.data.drawerOpen) return
    try {
      const selection = this.selection()
      if (this.data.replacingCartItem) this.replaceCartSelection(selection)
      else cart.add(selection)
      this.finishSelection()
      this.updateCartCount()
      wx.showToast({ title: this.data.replacingCartItem ? '已更新规格' : '已加入购物车', icon: 'success' })
      if (this.data.replacingCartItem) wx.navigateBack({ delta: 1, fail: () => wx.redirectTo({ url: '/pages/cart/cart' }) })
    } catch (error) { this.setData({ drawerError: error.message }) }
  },
  replaceCartSelection(selection) {
    const rows = cart.read()
    const original = this.originalCartRow
    const current = original && rows.find((row) => row.skuId === original.skuId)
    if (!current || ['quantity', 'seenPriceFen', 'selected'].some((key) => current[key] !== original[key])) {
      throw new Error('购物车内容已发生变化，请返回后重新选择规格。')
    }
    const target = rows.find((row) => row.skuId === selection.skuId && row.skuId !== original.skuId)
    const quantity = selection.quantity + (target ? target.quantity : 0)
    if (quantity > Math.min(9999, this.data.selectedAvailable)) throw new Error('更换后数量超过当前库存，请调整数量。')
    const replacement = { ...selection, quantity, selected: current.selected !== false }
    cart.write(rows.flatMap((row) => row.skuId === original.skuId ? [replacement] :
      row.skuId === selection.skuId ? [] : [row]))
  },
  confirmBuyNow() {
    if (!this.data.drawerOpen || this.data.replacingCartItem) return
    try {
      wx.setStorageSync('mall.checkoutSelection.v1', [this.selection()])
      this.finishSelection()
      wx.navigateTo({ url: '/pages/checkout/checkout', fail: () => {
        this.openSkuDrawer(); this.setData({ drawerError: '页面打开失败，请重试。' })
      } })
    } catch (error) { this.setData({ drawerError: error.message }) }
  },
  onShareAppMessage() {
    return { title: this.data.product ? this.data.product.name : '商品详情',
      path: `/pages/product/detail?productId=${encodeURIComponent(this.productId)}` }
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
