const { orderFilters } = require('../../lib/order-filters')
const api = require('../../lib/api')
const session = require('../../lib/session')
const { statusLabel, dateLabel } = require('../../lib/orders')
const { money } = require('../../lib/catalog')
Page({
  data: { state: 'loading', error: '', rows: [], page: 0, total: 0, loadingMore: false, moreError: '', status: '', fulfillment: '', isPointsList: false },
  onLoad(options = {}) {
    this.orderKind = options.orderKind === 'POINTS' ? 'POINTS' : ''
    this.setData({ ...orderFilters(options), isPointsList: this.orderKind === 'POINTS' })
  },
  onShow() { return this.refresh() },
  onPullDownRefresh() { return this.refresh().finally(() => wx.stopPullDownRefresh()) },
  onReachBottom() { return this.loadMore() },
  refresh() { return this.load(1) },
  loadMore() {
    if (this.data.state !== 'ready' || this.data.loadingMore || this.data.rows.length >= this.data.total) return
    return this.load(this.data.page + 1)
  },
  async load(page) {
    const token = page === 1 ? this.listToken = (this.listToken || 0) + 1 : this.listToken
    if (!session.loggedIn()) { this.setData({ state: 'auth', rows: [], error: '请登录后查看自己的订单。' }); return }
    this.setData(page === 1 ? { state: 'loading', rows: [], error: '', moreError: '', loadingMore: false } : { loadingMore: true, moreError: '' })
    try {
      const result = await api.get('/api/v1/app/orders', { page, pageSize: 20, status: this.data.status, fulfillment: this.data.fulfillment, orderKind: this.orderKind || '' })
      if (token !== this.listToken) return
      if (!Array.isArray(result.items) || !Number.isInteger(result.total)) throw new Error('订单列表不完整，请重新加载。')
      const rows = result.items.map((order) => ({ ...order, statusLabel: statusLabel(order), isPoints: order.orderKind === 'POINTS', totalLabel: order.orderKind === 'POINTS' ? `${order.exchangePoints} 积分` : money(order.payableFen), createdLabel: dateLabel(order.createdAt) }))
      this.setData({ state: result.total ? 'ready' : 'empty', rows: page === 1 ? rows : [...this.data.rows, ...rows],
        page: result.page, total: result.total, loadingMore: false })
    } catch (error) {
      if (token !== this.listToken) return
      if (error.statusCode === 401) this.setData({ state: 'auth', rows: [], error: error.message, loadingMore: false })
      else this.setData(page === 1 ? { state: 'error', error: error.message } : { moreError: error.message, loadingMore: false })
    }
  },
  filter(event) { this.setData(orderFilters(event.currentTarget.dataset)); return this.refresh() },
  openOrder(event) { wx.navigateTo({ url: `/pages/orders/detail?id=${encodeURIComponent(event.currentTarget.dataset.id)}` }) },
  login() { wx.navigateTo({ url: '/pages/login/login?returnTo=orders' }) },
  shop() { wx.navigateTo({ url: '/pages/index/index' }) },
})
