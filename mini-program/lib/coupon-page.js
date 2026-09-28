const api = require('./api')
const { presentList } = require('./member')
const { presentCampaign, presentCoupon, readPending, savePending, clearPending, validResult, STATUSES } = require('./coupons')
const { requestKey } = require('./orders')
function identity() { return wx.getStorageSync('mall.memberToken') || '' }
function couponPage(campaigns = false) {
  return {
    data: { state: 'loading', error: '', rows: [], page: 0, total: 0, status: 'AVAILABLE', loadingMore: false, moreError: '', busy: false, pending: null, message: '', claimError: '' },
    onShow() { this.disposed = false; return this.refresh() },
    onHide() { this.invalidate() },
    onUnload() { this.disposed = true; this.invalidate() },
    invalidate() { this.generation = (this.generation || 0) + 1; this.memberId = null; this.loadedToken = null; this.clearPrivate() },
    clearPrivate() { this.setData({ rows: [], page: 0, total: 0, loadingMore: false, moreError: '', pending: null, message: '', claimError: '', busy: false }) },
    valid(generation, token) {
      if (this.disposed || this.generation !== generation) return false
      if (token !== identity()) { this.invalidate(); this.setData({ state: 'auth', error: '登录状态已改变，请重新打开优惠券页面。' }); return false }
      return true
    },
    onPullDownRefresh() { return Promise.resolve(this.refresh()).finally(() => wx.stopPullDownRefresh()) },
    onReachBottom() { return this.loadMore() },
    refresh() { if (this.data.busy) return Promise.resolve(); return this.load(1) },
    loadMore() { if (this.data.state !== 'ready' || this.data.loadingMore || this.data.busy || this.data.rows.length >= this.data.total) return; return this.load(this.data.page + 1) },
    changeStatus(event) { const status = event.currentTarget.dataset.status; if (!STATUSES[status] || status === this.data.status) return; this.setData({ status }); return this.refresh() },
    async load(page) {
      const generation = page === 1 ? this.generation = (this.generation || 0) + 1 : this.generation, token = identity()
      if (!token) { this.invalidate(); this.setData({ state: 'auth', error: '请登录后领取或查看自己的优惠券。' }); return }
      if (page === 1) { this.clearPrivate(); this.setData({ state: 'loading', error: '' }) }
      else this.setData({ loadingMore: true, moreError: '' })
      try {
        const path = campaigns ? '/api/v1/app/coupon-campaigns' : '/api/v1/app/member/coupons'
        const query = { page, pageSize: 20, ...(campaigns ? {} : { status: this.data.status }) }
        const values = await Promise.all([api.get(path, query), ...(campaigns && page === 1 ? [api.get('/api/v1/app/member/overview')] : [])])
        if (!this.valid(generation, token)) return
        if (campaigns && page === 1) { if (!values[1] || typeof values[1].id !== 'string') throw new Error('会员资料暂不可用，请重新加载。'); this.memberId = values[1].id; this.loadedToken = token }
        const result = presentList(values[0], page, campaigns ? presentCampaign : presentCoupon)
        this.loadedToken = token
        this.setData({ state: result.total ? 'ready' : 'empty', rows: page === 1 ? result.rows : [...this.data.rows, ...result.rows], page, total: result.total, loadingMore: false,
          ...(campaigns ? { pending: readPending(this.memberId) } : {}) })
      } catch (error) {
        if (!this.valid(generation, token)) return
        if (error.statusCode === 401 || !identity()) { this.invalidate(); this.setData({ state: 'auth', error: error.message }) }
        else this.setData(page === 1 ? { state: 'error', error: error.message } : { loadingMore: false, moreError: error.message })
      }
    },
    async claim(event) {
      if (!campaigns || this.data.busy || this.data.pending || this.data.state !== 'ready' || !this.memberId) return
      if (this.loadedToken !== identity()) { this.invalidate(); this.setData({ state: 'auth', error: '登录状态已改变，请重新打开优惠券页面。' }); return }
      const row = this.data.rows.find(r => r.id === event.currentTarget.dataset.id)
      if (!row || !row.canClaim) return
      const pending = { memberId: this.memberId, campaignId: row.id, key: requestKey() }
      try { savePending(pending) } catch (error) { this.setData({ claimError: error.message }); return }
      this.setData({ pending }); return this.performClaim(pending, false)
    },
    retryClaim() { if (this.data.busy || !this.data.pending || this.memberId !== this.data.pending.memberId) return; return this.performClaim(this.data.pending, true) },
    async performClaim(pending, recover) {
      const generation = this.generation, token = identity()
      if (!token || token !== this.loadedToken || pending.memberId !== this.memberId) { this.invalidate(); this.setData({ state: 'auth', error: '请重新登录后核对领取结果。' }); return }
      this.setData({ busy: true, claimError: '', message: '' })
      try {
        let result
        if (recover) {
          const operation = await api.get(`/api/v1/app/member/coupon-claims/${pending.key}`)
          if (!this.valid(generation, token)) return
          if (operation && operation.status === 'COMPLETED') {
            if (!validResult(operation.result, pending)) throw new Error('领取结果资料不完整。')
            result = operation.result
          }
          else if (!operation || operation.status !== 'NOT_FOUND') throw new Error('领取结果暂不可确认。')
        }
        if (!result) result = await api.post(`/api/v1/app/coupon-campaigns/${encodeURIComponent(pending.campaignId)}/claim`, {}, { 'Idempotency-Key': pending.key })
        if (!this.valid(generation, token)) return
        if (!validResult(result, pending)) throw new Error('领取结果资料不完整。')
        clearPending(pending)
        this.setData({ busy: false, pending: null })
        const refreshed = this.refresh(), refreshedGeneration = this.generation
        await refreshed
        if (this.valid(refreshedGeneration, token)) this.setData({ message: '领取成功，可前往我的优惠券查看。' })
      } catch (error) {
        if (!this.valid(generation, token)) return
        const definitive = error.statusCode >= 400 && error.statusCode < 500 && ![401,408,429].includes(error.statusCode)
        if (definitive) { try { clearPending(pending); this.setData({ pending: null }) } catch (_) {} }
        this.setData({ busy: false, claimError: definitive ? error.message : '领取结果尚未确认。请核对结果，系统将沿用原领取凭证，避免重复领取。' })
      }
    },
    openProduct(event) { const { row, id } = event.currentTarget.dataset, item = this.data.rows.find(r => r.id === row); if (item && item.productIds.includes(id)) wx.navigateTo({ url: `/pages/product/detail?id=${encodeURIComponent(id)}` }) },
    openOrder(event) { if (this.loadedToken !== identity()) { this.invalidate(); this.setData({ state: 'auth', error: '登录状态已改变，请重新打开优惠券页面。' }); return }
      const row = this.data.rows.find(r => r.id === event.currentTarget.dataset.id); if (row && row.orderId) wx.navigateTo({ url: `/pages/orders/detail?id=${encodeURIComponent(row.orderId)}` }) },
    openMine() { wx.navigateTo({ url: '/pages/coupons/index' }) },
    openCampaigns() { wx.navigateTo({ url: '/pages/coupons/campaigns' }) },
    openCatalog() { wx.navigateTo({ url: '/pages/index/index' }) },
    login() { wx.navigateTo({ url: '/pages/login/login?returnTo=coupons' }) },
  }
}
module.exports = { couponPage }
