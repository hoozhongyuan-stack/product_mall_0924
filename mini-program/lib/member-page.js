const api = require('./api')
function identity() { return wx.getStorageSync ? wx.getStorageSync('mall.memberToken') || '' : '' }
function memberPage({ path, present, paginated = false }) {
  return {
    data: { state: 'loading', error: '', member: null, rows: [], page: 0, total: 0, loadingMore: false, moreError: '' },
    onShow() { this.disposed = false; return this.refresh() },
    onHide() { this.invalidate() },
    onUnload() { this.disposed = true; this.invalidate() },
    invalidate() { this.generation = (this.generation || 0) + 1; this.clearPrivate() },
    clearPrivate() { this.setData({ member: null, rows: [], page: 0, total: 0, loadingMore: false, moreError: '' }) },
    onPullDownRefresh() { return this.refresh().finally(() => wx.stopPullDownRefresh()) },
    onReachBottom() { return this.loadMore() },
    refresh() { return this.load(1) },
    loadMore() {
      if (!paginated || this.data.state !== 'ready' || this.data.loadingMore || this.data.rows.length >= this.data.total) return
      return this.load(this.data.page + 1)
    },
    valid(generation, memberToken) {
      if (this.disposed || generation !== this.generation) return false
      if (memberToken !== identity()) { this.clearPrivate(); this.setData({ state: 'auth', error: '登录状态已改变，请重新打开会员中心。' }); return false }
      return true
    },
    async load(page) {
      const generation = page === 1 ? this.generation = (this.generation || 0) + 1 : this.generation
      const memberToken = identity()
      if (!memberToken) { this.clearPrivate(); this.setData({ state: 'auth', error: '请登录后查看自己的会员权益。' }); return }
      if (page === 1) { this.clearPrivate(); this.setData({ state: 'loading', error: '' }) }
      else this.setData({ loadingMore: true, moreError: '' })
      try {
        const value = await api.get(path, paginated ? { page, pageSize: 20 } : {})
        if (!this.valid(generation, memberToken)) return
        const result = present(value, page)
        this.setData(paginated ? { state: result.total ? 'ready' : 'empty', rows: page === 1 ? result.rows : [...this.data.rows, ...result.rows], page, total: result.total, loadingMore: false } : { state: 'ready', member: result })
      } catch (error) {
        if (!this.valid(generation, memberToken)) return
        if (error.statusCode === 401) { this.clearPrivate(); this.setData({ state: 'auth', error: error.message }) }
        else this.setData(page === 1 ? { state: 'error', error: error.message } : { loadingMore: false, moreError: error.message })
      }
    },
    login() { wx.navigateTo({ url: '/pages/login/login?returnTo=member' }) },
    openOrder(event) { const id = event.currentTarget.dataset.id; if (id) wx.navigateTo({ url: `/pages/orders/detail?id=${encodeURIComponent(id)}` }) },
  }
}
module.exports = { memberPage }
