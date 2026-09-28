const api = require('../../lib/api')
const catalog = require('../../lib/catalog')
const startup = require('../../lib/startup')

Page({
  data: {
    roots: [], leaves: [], activeRootId: '', activeLeafId: '',
    categoryState: 'loading', categoryError: '',
    products: [], listState: 'idle', listError: '',
    searchInput: '', keyword: '', searchFocused: false,
    page: 0, hasMore: false, loadingMore: false, total: 0, failedImages: {},
  },

  onLoad(options = {}) {
    if (startup.redirectIfPending(() => this.onLoad(options))) return
    this.listToken = 0
    this.shown = false
    this.requestedCategoryId = options.categoryId || ''
    let keyword = typeof options.keyword === 'string' ? options.keyword : ''
    try { keyword = decodeURIComponent(keyword) } catch (_) { /* use the original text */ }
    keyword = keyword.trim().slice(0, 100)
    this.setData({ keyword, searchInput: keyword, searchFocused: options.focusSearch === '1' })
    return this.loadCategories()
  },

  onShow() {
    if (startup.redirectIfPending()) return
    if (this.shown && this.data.categoryState === 'ready') return this.loadProducts(true)
    this.shown = true
  },

  onPullDownRefresh() {
    this.loadCategories().finally(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loadingMore && this.data.listState === 'ready') {
      this.loadProducts(false)
    }
  },

  retryMore() { this.loadProducts(false) },

  async loadCategories() {
    this.setData({ categoryState: 'loading', categoryError: '' })
    try {
      const roots = catalog.categoryTree(await api.get('/api/v1/app/categories'))
      const activeRoot = roots.find((item) => item.id === this.data.activeRootId) ||
        roots.find((item) => item.id === this.requestedCategoryId ||
          item.children.some((child) => child.id === this.requestedCategoryId)) || roots[0]
      const leaves = activeRoot ? activeRoot.children : []
      const activeLeaf = leaves.find((item) => item.id === this.data.activeLeafId) ||
        leaves.find((item) => item.id === this.requestedCategoryId) || leaves[0]
      this.requestedCategoryId = ''
      this.setData({ roots, leaves, activeRootId: activeRoot ? activeRoot.id : '',
        activeLeafId: activeLeaf ? activeLeaf.id : '', categoryState: roots.length ? 'ready' : 'empty' })
      if (roots.length || this.data.keyword) await this.loadProducts(true)
      else this.setData({ products: [], listState: 'empty', hasMore: false, total: 0 })
    } catch (error) {
      this.setData({ categoryState: 'error', categoryError: error.message,
        products: [], listState: 'idle', hasMore: false })
    }
  },

  async loadProducts(reset = true) {
    if (this.data.loadingMore && !reset) return
    const token = ++this.listToken
    const page = reset ? 1 : this.data.page + 1
    this.setData(reset ? { listState: 'loading', listError: '', products: [], page: 0, hasMore: false, failedImages: {} } :
      { loadingMore: true, listError: '' })
    try {
      const query = { page, pageSize: 20, keyword: this.data.keyword }
      if (!this.data.keyword) query.categoryId = this.data.activeLeafId || this.data.activeRootId
      const result = await api.get('/api/v1/app/products', query)
      if (token !== this.listToken) return
      const rows = (result.rows || []).map((item) => catalog.productCard(item, api.baseUrl()))
      const products = reset ? rows : [...this.data.products, ...rows]
      const total = result.total || 0
      this.setData({ products, total, page, hasMore: products.length < total,
        listState: products.length ? 'ready' : 'empty', loadingMore: false })
    } catch (error) {
      if (token !== this.listToken) return
      this.setData({ listState: reset ? 'error' : 'ready', listError: error.message, loadingMore: false })
    }
  },

  selectRoot(event) {
    const root = this.data.roots.find((item) => item.id === event.currentTarget.dataset.id)
    if (!root || root.id === this.data.activeRootId) return
    const leaf = root.children[0]
    this.setData({ activeRootId: root.id, activeLeafId: leaf ? leaf.id : '',
      leaves: root.children, keyword: '', searchInput: '' })
    return this.loadProducts(true)
  },

  selectLeaf(event) {
    const id = event.currentTarget.dataset.id
    if (id === this.data.activeLeafId) return
    this.setData({ activeLeafId: id, keyword: '', searchInput: '' })
    return this.loadProducts(true)
  },

  onSearchInput(event) { this.setData({ searchInput: event.detail.value }) },
  submitSearch() {
    const keyword = this.data.searchInput.trim()
    this.setData({ keyword, searchInput: keyword })
    if (keyword || this.data.activeRootId) return this.loadProducts(true)
  },
  clearSearch() {
    this.setData({ keyword: '', searchInput: '' })
    if (this.data.activeRootId) return this.loadProducts(true)
  },

  onImageError(event) {
    this.setData({ failedImages: { ...this.data.failedImages, [event.currentTarget.dataset.id]: true } })
  },
  openProduct(event) {
    const id = event.currentTarget.dataset.id
    if (!this.data.products.some((item) => item.id === id)) return
    wx.navigateTo({ url: `/pages/product/detail?productId=${encodeURIComponent(id)}` })
  },
  openCart() { wx.navigateTo({ url: '/pages/cart/cart' }) },
})
