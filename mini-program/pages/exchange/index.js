const api = require('../../lib/api')
const { presentOffer } = require('../../lib/exchange')
const { presentList } = require('../../lib/member')
const { navigateInternal } = require('../../lib/page-links')
Page({
  data: { state: 'loading', rows: [], page: 0, total: 0, error: '', loadingMore: false, moreError: '', failedImages: {} },
  onShow() { this.hidden = false; return this.refresh() },
  onHide() { this.hidden = true; this.generation = (this.generation || 0) + 1; this.setData({ rows: [], loadingMore: false }) },
  onUnload() { this.onHide() },
  onPullDownRefresh() { return this.refresh().finally(() => wx.stopPullDownRefresh()) },
  onReachBottom() { return this.loadMore() },
  refresh() { return this.load(1) },
  loadMore() { if (this.data.state !== 'ready' || this.data.loadingMore || this.data.rows.length >= this.data.total) return; return this.load(this.data.page + 1) },
  async load(page) {
    const generation = page === 1 ? this.generation = (this.generation || 0) + 1 : this.generation
    this.setData(page === 1 ? { state: 'loading', rows: [], error: '', failedImages: {} } : { loadingMore: true, moreError: '' })
    try {
      const value = await api.get('/api/v1/app/exchange-products', { page, pageSize: 20 })
      if (this.hidden || generation !== this.generation) return
      const result = presentList(value, page, row => presentOffer(row, api.baseUrl()))
      this.setData({ state: result.total ? 'ready' : 'empty', rows: page === 1 ? result.rows : [...this.data.rows, ...result.rows], page, total: result.total, loadingMore: false })
    } catch (error) {
      if (this.hidden || generation !== this.generation) return
      this.setData(page === 1 ? { state: 'error', error: error.message } : { loadingMore: false, moreError: error.message })
    }
  },
  openOffer(event) { const id = event.currentTarget.dataset.id; if (this.data.rows.some(row => row.id === id)) navigateInternal(`/pages/exchange/detail?id=${encodeURIComponent(id)}`) },
  openOrders() { navigateInternal('/pages/orders/list?orderKind=POINTS') },
  openPoints() { navigateInternal('/pages/member/points') },
  onImageError(event) { this.setData({ failedImages: { ...this.data.failedImages, [event.currentTarget.dataset.id]: true } }) },
})
