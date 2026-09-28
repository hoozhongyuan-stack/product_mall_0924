const api = require('../../lib/api')
const exchange = require('../../lib/exchange')
const { requestKey } = require('../../lib/orders')
const { navigateInternal } = require('../../lib/page-links')
function identity() { return wx.getStorageSync('mall.memberToken') || '' }
Page({
  data: { state: 'loading', error: '', offer: null, address: null, quantity: '1', quote: null, quoteState: 'idle', quoteError: '', quoteWarning: '', availablePoints: null, canSubmit: false, pending: null, submitting: false, submitError: '', failedImage: false },
  onLoad(options) { this.offerId = options.id || ''; this.hidden = false; this.shown = false; return this.refresh() },
  onShow() { this.hidden = false; if (!this.shown) { this.shown = true; return }; return this.refresh() },
  onHide() { this.hidden = true; this.generation = (this.generation || 0) + 1; this.quoteGeneration = (this.quoteGeneration || 0) + 1; this.clearPrivate(); this.setData({ state: 'loading' }) },
  onUnload() { this.onHide() },
  clearPrivate() { this.memberId = null; this.loadedToken = null; this.setData({ address: null, quote: null, availablePoints: null, canSubmit: false, pending: null, submitting: false, submitError: '', quoteState: 'idle' }) },
  valid(generation, token) {
    if (this.hidden || generation !== this.generation) return false
    if (token !== identity()) { this.clearPrivate(); this.setData({ state: 'auth', error: '登录状态已改变，请重新打开兑换商品。' }); return false }
    return true
  },
  onPullDownRefresh() { return Promise.resolve(this.refresh()).finally(() => wx.stopPullDownRefresh()) },
  async refresh() {
    if (this.data.submitting) return
    const generation = this.generation = (this.generation || 0) + 1, token = identity()
    this.quoteGeneration = (this.quoteGeneration || 0) + 1
    this.clearPrivate(); this.setData({ state: 'loading', error: '', offer: null, failedImage: false })
    if (!this.offerId) { this.setData({ state: 'unavailable', error: '兑换商品链接无效，请从积分商城重新进入。' }); return }
    try {
      let member = null, addresses = [], addressError = null
      if (token) [member, addresses] = await Promise.all([api.get('/api/v1/app/member/overview'), api.get('/api/v1/app/addresses').catch(error => { addressError = error; return [] })])
      if (!this.valid(generation, token)) return
      if (token && (!member || typeof member.id !== 'string' || !member.points || !Number.isSafeInteger(member.points.availablePoints) || !Array.isArray(addresses))) throw new Error('会员积分或收货信息暂不可用，请重新加载。')
      this.memberId = member && member.id; this.loadedToken = token
      const pending = member ? exchange.stored(member.id) : null
      this.setData({ availablePoints: member ? member.points.availablePoints : null, pending })
      if (addressError) {
        if (pending) { this.setData({ state: 'ready', offer: null, error: '履约信息暂不可读取，仍可核对原兑换结果。' }); return }
        throw addressError
      }
      let offer
      try { offer = exchange.presentOffer(await api.get(`/api/v1/app/exchange-products/${encodeURIComponent(this.offerId)}`), api.baseUrl()) }
      catch (error) {
        if (!this.valid(generation, token)) return
        if (pending) { this.setData({ state: 'ready', offer: null, error: '商品信息暂不可查看，仍可核对原兑换结果。' }); return }
        throw error
      }
      if (!this.valid(generation, token)) return
      const selected = wx.getStorageSync('mall.selectedAddressId'), address = addresses.find(a => a.id === selected) || addresses.find(a => a.isDefault) || null
      this.setData({ state: 'ready', offer, address: offer.fulfillmentKind === 'SHIP' ? address : null })
    } catch (error) {
      if (!this.valid(generation, token)) return
      this.setData({ state: error.statusCode === 401 ? 'auth' : error.statusCode === 404 ? 'unavailable' : 'error', error: error.message })
    }
  },
  onQuantity(event) { if (this.data.submitting || this.data.pending) return; this.quoteGeneration = (this.quoteGeneration || 0) + 1; this.setData({ quantity: event.detail.value, quote: null, quoteState: 'idle', canSubmit: false, quoteError: '', quoteWarning: '' }) },
  async requestQuote() {
    if (this.data.submitting || this.data.pending || !this.data.offer || !this.valid(this.generation, this.loadedToken)) return
    if (!this.loadedToken || !this.memberId) { this.setData({ quoteError: '请登录后核对可用积分与兑换数量。' }); return }
    let quantity
    try { quantity = exchange.quantity(this.data.quantity) } catch (error) { this.setData({ quoteError: error.message }); return }
    const token = this.loadedToken, generation = this.generation, quoteGeneration = this.quoteGeneration = (this.quoteGeneration || 0) + 1
    this.setData({ quoteState: 'loading', quote: null, quoteError: '', quoteWarning: '', canSubmit: false })
    try {
      const body = { offerId: this.offerId, quantity, ...(this.data.address ? { addressId: this.data.address.id } : {}) }
      const quote = exchange.presentQuote(await api.post('/api/v1/app/exchange-quotes', body))
      if (!this.valid(generation, token) || quoteGeneration !== this.quoteGeneration) return
      if (quote.offerId !== this.offerId || quote.quantity !== quantity || (quote.line.fulfillmentKind === 'SHIP' && quote.addressId !== (this.data.address && this.data.address.id))) throw new Error('积分报价与本次选择不一致，请重新核对。')
      const expired = Date.parse(quote.expiresAt) <= Date.now()
      const warning = !quote.exchangeOrderAvailable ? '积分兑换暂未开放，可以浏览商品和核对积分。' : expired ? '积分报价已到期，请重新核对。' : !quote.ready ? quote.blockedMessage || '积分、库存或履约信息不满足兑换条件，请调整后重新核对。' : ''
      this.setData({ quote, quoteState: 'ready', quoteWarning: warning, canSubmit: quote.ready && quote.exchangeOrderAvailable && !expired })
    } catch (error) {
      if (!this.valid(generation, token) || quoteGeneration !== this.quoteGeneration) return
      this.setData({ quoteState: 'error', quoteError: error.message })
    }
  },
  async submit() {
    if (this.data.submitting || this.data.pending || !this.data.canSubmit || !this.data.quote || !this.valid(this.generation, this.loadedToken) || !this.loadedToken || !this.memberId) return
    if (Date.parse(this.data.quote.expiresAt) <= Date.now()) { this.setData({ canSubmit: false, quoteError: '积分报价已到期，请重新核对。' }); return }
    const pending = { memberId: this.memberId, offerId: this.offerId, totalPoints: this.data.quote.totalPoints, key: requestKey(), body: { quoteId: this.data.quote.quoteId } }
    try { exchange.save(pending) } catch (error) { this.setData({ submitError: error.message }); return }
    this.setData({ pending }); return this.performSubmit(pending, false)
  },
  retrySubmit() { if (this.data.submitting || !this.data.pending || this.memberId !== this.data.pending.memberId) return; return this.performSubmit(this.data.pending, true) },
  async performSubmit(pending, recover) {
    const generation = this.generation, token = this.loadedToken
    if (!token || !this.valid(generation, token) || this.memberId !== pending.memberId) return
    this.setData({ submitting: true, submitError: '', canSubmit: false })
    try {
      let result
      if (recover) {
        const operation = await api.get(`/api/v1/app/exchange-operations/${pending.key}`)
        if (!this.valid(generation, token)) return
        if (operation && operation.status === 'COMPLETED') { if (!exchange.validResult(operation.result, pending)) throw Error('invalid result'); result = operation.result }
        else if (!operation || operation.status !== 'NOT_FOUND') throw Error('unknown operation')
      }
      if (!result) result = await api.post('/api/v1/app/exchange-orders', pending.body, { 'Idempotency-Key': pending.key })
      if (!this.valid(generation, token)) return
      if (!exchange.validResult(result, pending)) throw Error('invalid result')
      exchange.clear(pending); this.setData({ state: 'submitted', pending: null, submitting: false, quote: null })
      wx.redirectTo({ url: `/pages/orders/detail?id=${encodeURIComponent(result.orderId)}&result=1`, fail: () => this.setData({ submitError: '兑换成功，页面跳转失败。请从我的订单查看。' }) })
    } catch (error) {
      if (!this.valid(generation, token)) return
      const definite = error.statusCode >= 400 && error.statusCode < 500 && ![401,408,429].includes(error.statusCode)
      if (definite) { try { exchange.clear(pending); this.setData({ pending: null, quote: null, quoteState: 'idle' }) } catch (_) {} }
      this.setData({ submitting: false, submitError: definite ? `${error.message} 请重新核对积分与兑换条件。` : '兑换结果尚未确认。请核对原兑换凭证，避免重复扣除积分。' })
    }
  },
  chooseAddress() { if (this.data.pending || this.data.submitting) return; if (!identity()) return this.login(); navigateInternal('/pages/addresses/addresses?select=1') },
  login() { navigateInternal('/pages/login/login?returnTo=exchange') },
  openOrders() { navigateInternal('/pages/orders/list?orderKind=POINTS') },
  openPoints() { navigateInternal('/pages/member/points') },
  openMall() { navigateInternal('/pages/exchange/index') },
  onImageError() { this.setData({ failedImage: true }) },
})
