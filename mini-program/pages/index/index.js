const dining = require('../../lib/dining-alliance')
const stores = require('../../lib/stores')
const api = require('../../lib/api')
const catalog = require('../../lib/catalog')
const startup = require('../../lib/startup')

Page({
  data: {
    roots: [], leaves: [], activeRootId: '', activeLeafId: '',
    categoryState: 'loading', categoryError: '', activeBannerUrl: '', rootScrollId: '', categoryOriginalImages: {}, failedCategoryImages: {}, failedBannerImages: {},
    products: [], listState: 'idle', listError: '',
    searchInput: '', keyword: '', searchFocused: false, globalSearch: false,
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
    this.setData({ keyword, searchInput: keyword, searchFocused: options.focusSearch === '1', globalSearch: !!keyword && !options.categoryId })
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
      const roots = catalog.categoryTree(await api.get('/api/v1/app/categories'), api.baseUrl())
      const activeRoot = roots.find((item) => item.id === this.data.activeRootId) ||
        roots.find((item) => item.id === this.requestedCategoryId ||
          item.children.some((child) => child.id === this.requestedCategoryId)) || roots[0]
      const leaves = activeRoot ? activeRoot.children : []
      const activeLeaf = leaves.find((item) => item.id === this.data.activeLeafId) ||
        leaves.find((item) => item.id === this.requestedCategoryId) || leaves[0]
      this.requestedCategoryId = ''
      this.setData({ roots, leaves, activeRootId: activeRoot ? activeRoot.id : '',
        activeLeafId: activeLeaf ? activeLeaf.id : '', activeBannerUrl: activeLeaf ? activeLeaf.bannerImageUrl : '', rootScrollId: activeRoot ? `root-${activeRoot.id}` : '', categoryOriginalImages: {}, failedCategoryImages: {}, failedBannerImages: {}, categoryState: roots.length ? 'ready' : 'empty' })
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
      const query = { page, pageSize: 20, keyword: this.data.keyword, ...stores.query() }
      if (!this.data.globalSearch && (this.data.activeLeafId || this.data.activeRootId)) query.categoryId = this.data.activeLeafId || this.data.activeRootId
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
    const failedBannerImages = { ...this.data.failedBannerImages }
    if (leaf) delete failedBannerImages[leaf.id]
    this.setData({ activeRootId: root.id, rootScrollId: `root-${root.id}`, activeLeafId: leaf ? leaf.id : '',
      leaves: root.children, activeBannerUrl: leaf ? leaf.bannerImageUrl : '', failedBannerImages, keyword: '', searchInput: '', globalSearch: false })
    return this.loadProducts(true)
  },

  selectLeaf(event) {
    const id = event.currentTarget.dataset.id
    if (id === this.data.activeLeafId) return
    const leaf = this.data.leaves.find(item => item.id === id)
    if (!leaf) return
    const failedBannerImages = { ...this.data.failedBannerImages }
    delete failedBannerImages[id]
    this.setData({ activeLeafId: id, activeBannerUrl: leaf.bannerImageUrl, failedBannerImages, keyword: '', searchInput: '', globalSearch: false })
    return this.loadProducts(true)
  },

  onSearchInput(event) { this.setData({ searchInput: event.detail.value }) },
  submitSearch() {
    const keyword = this.data.searchInput.trim()
    this.setData({ keyword, searchInput: keyword })
    if (keyword || this.data.activeRootId) return this.loadProducts(true)
  },
  clearSearch() {
    this.setData({ keyword: '', searchInput: '', globalSearch: false })
    if (this.data.activeRootId) return this.loadProducts(true)
  },

  categoryImageError(event) {
    const id = event.currentTarget.dataset.id
    const root = this.data.roots.find(item => item.id === id)
    if (!root) return
    const shownUrl = this.data.categoryOriginalImages[id] ? root.imageOriginalUrl : root.imageUrl
    if (event.currentTarget.dataset.url && event.currentTarget.dataset.url !== shownUrl) return
    if (root.imageOriginalUrl && root.imageOriginalUrl !== root.imageUrl && !this.data.categoryOriginalImages[id]) {
      this.setData({ categoryOriginalImages: { ...this.data.categoryOriginalImages, [id]: true } }); return
    }
    this.setData({ failedCategoryImages: { ...this.data.failedCategoryImages, [id]: true } })
  },
  bannerImageError(event) {
    const id = event.currentTarget.dataset.id
    if (id !== this.data.activeLeafId) return
    if (event.currentTarget.dataset.url && event.currentTarget.dataset.url !== this.data.activeBannerUrl) return
    const leaf = this.data.leaves.find(item => item.id === id)
    if (leaf && leaf.bannerOriginalUrl && this.data.activeBannerUrl !== leaf.bannerOriginalUrl) {
      this.setData({ activeBannerUrl: leaf.bannerOriginalUrl }); return
    }
    this.setData({ failedBannerImages: { ...this.data.failedBannerImages, [id]: true } })
  },
  retryBanner() {
    const leaf = this.data.leaves.find(item => item.id === this.data.activeLeafId)
    if (!leaf) return
    const failedBannerImages = { ...this.data.failedBannerImages }
    delete failedBannerImages[leaf.id]
    this.setData({ activeBannerUrl: leaf.bannerImageUrl, failedBannerImages })
  },
  onImageError(event) {
    this.setData({ failedImages: { ...this.data.failedImages, [event.currentTarget.dataset.id]: true } })
  },
  openProduct(event) {
    const id = event.currentTarget.dataset.id
    if (!this.data.products.some((item) => item.id === id)) return
    dining.navigate(`/pages/product/detail?productId=${encodeURIComponent(id)}`)
  },
  openCart() { dining.navigate('/pages/cart/cart') },
})
