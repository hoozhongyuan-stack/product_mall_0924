const api = require('../../lib/api')
const cart = require('../../lib/cart')
const session = require('../../lib/session')
const { money } = require('../../lib/catalog')
const orders = require('../../lib/orders')

function expiryLabel(value) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '请重新获取'
  const two = (number) => String(number).padStart(2, '0')
  return `${two(date.getHours())}:${two(date.getMinutes())}`
}

function validFen(value) { return Number.isSafeInteger(value) && value >= 0 }

function settlementAmounts(quote) {
  const { goodsTotalFen, shippingFeeFen, couponDiscountFen, pointsDiscountFen, payableFen } = quote
  const complete = [goodsTotalFen, shippingFeeFen, couponDiscountFen, pointsDiscountFen, payableFen]
    .every(validFen) && couponDiscountFen + pointsDiscountFen <= goodsTotalFen &&
    goodsTotalFen - couponDiscountFen - pointsDiscountFen + shippingFeeFen === payableFen
  return {
    settlementReady: complete,
    goodsTotal: validFen(goodsTotalFen) ? money(goodsTotalFen) : '待核算',
    shippingFee: validFen(shippingFeeFen) ? money(shippingFeeFen) : '待核算',
    couponDiscount: validFen(couponDiscountFen) ? `−${money(couponDiscountFen)}` : '待核算',
    pointsDiscount: validFen(pointsDiscountFen) ? `−${money(pointsDiscountFen)}` : '待核算',
    total: complete ? money(payableFen) : '待核算',
  }
}

function couponOptions(quote) {
  const choices = Array.isArray(quote.availableCoupons) ? quote.availableCoupons : []
  return [{ id: null, title: '不使用优惠券' }, ...choices
    .filter((item) => item && typeof item.id === 'string')
    .map((item) => ({ id: item.id, title: item.title || item.name || '可用优惠券' }))]
}

Page({
  data: { state: 'loading', error: '', quote: null, lines: [], goodsTotal: '待核算',
    shippingFee: '待核算', couponDiscount: '待核算', pointsDiscount: '待核算',
    total: '待核算', settlementReady: false, canSubmit: false, expiresLabel: '',
    address: null, failedImages: {}, loggedIn: false, quoteWarning: '',
    couponOptions: [{ id: null, title: '不使用优惠券' }], couponIndex: 0,
    couponLabel: '暂无可用优惠券', availablePointsLabel: '登录后查看',
    pointsInput: '0', benefitError: '', paymentMethod: 'OFFLINE', paymentChoices: [], offlinePolicy: null,
    submitting: false, submitError: '', loginPromptOpen: false, stockPromptOpen: false, stockAdjustable: false, stockLines: [] },
  onLoad() {
    this.items = wx.getStorageSync('mall.checkoutSelection.v1') || []
    this.couponId = null
    this.pointsToUse = 0
    this.shown = false
    this.pendingSubmission = orders.pendingOrder()
    if (this.pendingSubmission) {
      this.setData({ state: 'uncertain', error: '上次提交的结果尚未确认。请使用同一请求重试，或查看我的订单。' })
      return
    }
    return this.refresh()
  },
  onShow() {
    if (!this.shown) { this.shown = true; return }
    if (this.pendingSubmission && session.loggedIn()) {
      this.setData({ state: 'uncertain', error: '上次提交的结果尚未确认。请重试确认订单结果。' })
      return
    }
    const recovered = session.recoverCheckout()
    if (recovered.length) this.items = recovered
    if (this.items && !this.pendingSubmission && !this.data.submitting) return this.refresh()
  },
  async refresh() {
    if (this.pendingSubmission || this.data.submitting) return
    const token = this.quoteToken = (this.quoteToken || 0) + 1
    if (!Array.isArray(this.items) || !this.items.length) {
      this.setData({ state: 'empty', error: '未选择商品。', canSubmit: false }); return
    }
    if (!session.loggedIn()) { this.couponId = null; this.pointsToUse = 0 }
    this.setData({ state: 'loading', error: '', benefitError: '', canSubmit: false,
      loggedIn: session.loggedIn(), loginPromptOpen: false })
    try {
      let address = null
      if (session.loggedIn()) {
        const addresses = await api.get('/api/v1/app/addresses')
        const chosen = wx.getStorageSync('mall.selectedAddressId')
        address = addresses.find((item) => item.id === chosen) || addresses.find((item) => item.isDefault) || null
      }
      const quote = await api.post('/api/v1/app/checkout/quotes', {
        items: cart.quoteItems(this.items), ...(address ? { addressId: address.id } : {}),
        ...(this.couponId ? { couponId: this.couponId } : {}),
        ...(this.pointsToUse ? { pointsToUse: this.pointsToUse } : {}),
      })
      if (token !== this.quoteToken) return
      if (!Array.isArray(quote.lines)) throw new Error('报价内容不完整，请重新获取。')
      const amounts = settlementAmounts(quote)
      const options = couponOptions(quote)
      const selectedId = quote.selectedCouponId === undefined ? this.couponId : quote.selectedCouponId
      const couponIndex = Math.max(0, options.findIndex((item) => item.id === selectedId))
      const availablePoints = quote.availablePoints
      const pointsToUse = Number.isSafeInteger(quote.pointsToUse) ? quote.pointsToUse : this.pointsToUse
      const supportsOffline = Array.isArray(quote.availablePaymentMethods) && quote.availablePaymentMethods.includes('OFFLINE')
      const offlinePolicy = supportsOffline ? await api.get('/api/v1/app/payments/offline-policy') : null
      if (token !== this.quoteToken) return
      const paymentChoices = []
      if (supportsOffline && offlinePolicy && offlinePolicy.configured === true &&
          typeof offlinePolicy.instructions === 'string' && offlinePolicy.instructions.trim()) paymentChoices.push({ value: 'OFFLINE', label: '线下支付' })
      if (Array.isArray(quote.availablePaymentMethods) && quote.availablePaymentMethods.includes('WECHAT')) paymentChoices.push({ value: 'WECHAT', label: '微信支付' })
      const paymentMethod = paymentChoices.some((choice) => choice.value === this.data.paymentMethod) ?
        this.data.paymentMethod : paymentChoices.length ? paymentChoices[0].value : ''
      this.couponId = selectedId
      this.pointsToUse = pointsToUse
      this.setData({ state: 'ready', quote, lines: quote.lines.map((line) => ({ ...line,
        price: money(line.unitPriceFen), amount: money(line.lineAmountFen),
        specLabel: line.specs && line.specs.length ?
          line.specs.map((spec) => `${spec.name}：${spec.value}`).join(' · ') : '默认规格',
        image: line.imageUrl ? `${api.baseUrl()}${line.imageUrl}` : '' })),
      ...amounts, offlinePolicy, paymentChoices, paymentMethod, canSubmit: !!paymentMethod && session.loggedIn() &&
        quote.orderSubmissionAvailable === true && amounts.settlementReady &&
        quote.ready === true && quote.confirmRequired !== true &&
        (!quote.addressRequired || !!address),
      expiresLabel: expiryLabel(quote.expiresAt), address,
      couponOptions: options, couponIndex,
      couponLabel: selectedId ? (couponIndex ? options[couponIndex].title : '已选优惠券') :
        (options.length > 1 ? '选择优惠券' : '暂无可用优惠券'),
      availablePointsLabel: !session.loggedIn() ? '登录后查看' :
        Number.isSafeInteger(availablePoints) && availablePoints >= 0 ?
          `可用 ${availablePoints} 积分` : '积分暂不可用',
      pointsInput: String(pointsToUse),
      quoteWarning: !quote.ready ? '有商品不可售或库存不足，请返回购物车调整。' :
        quote.confirmRequired ? '适用价格已变化，请确认后继续。' :
          !amounts.settlementReady ? '结算金额尚未核算完整，请稍后重试。' : '' })
    } catch (error) {
      if (token === this.quoteToken) this.setData({ state: error.statusCode === 401 ? 'auth' : 'error',
        error: error.message, quote: null, canSubmit: false, total: '待核算' })
    }
  },
  async submitOrder() {
    if (this.data.submitting) return
    if (!session.loggedIn()) {
      if (this.data.state === 'ready' || this.pendingSubmission) this.setData({ loginPromptOpen: true })
      return
    }
    if (!this.pendingSubmission && !this.data.canSubmit) return
    if (!this.pendingSubmission) {
      this.pendingSubmission = { key: orders.requestKey(), body: { quoteId: this.data.quote.quoteId, paymentMethod: this.data.paymentMethod },
        items: cart.quoteItems(this.items || []) }
      orders.savePendingOrder(this.pendingSubmission)
    }
    this.setData({ submitting: true, submitError: '' })
    try {
      const order = await api.post('/api/v1/app/orders', this.pendingSubmission.body,
        { 'Idempotency-Key': this.pendingSubmission.key })
      if (!order || typeof order.orderId !== 'string') throw new Error('订单响应不完整，请重试确认结果。')
      const submittedItems = this.pendingSubmission.items || []
      orders.clearPendingOrder()
      this.pendingSubmission = null
      const quantities = new Map(submittedItems.map((item) => [item.skuId, item.quantity]))
      cart.write(cart.read().flatMap((row) => {
        const remaining = row.quantity - (quantities.get(row.skuId) || 0)
        return remaining > 0 ? [{ ...row, quantity: remaining }] : []
      }))
      wx.removeStorageSync('mall.checkoutSelection.v1')
      this.setData({ canSubmit: false, state: 'submitted', error: '订单已提交，可从我的订单查看。' })
      wx.redirectTo({ url: `/pages/orders/detail?id=${encodeURIComponent(order.orderId)}&result=1`,
        fail: () => this.setData({ submitError: '订单已提交，页面跳转失败。请从我的订单查看。' }) })
    } catch (error) {
      if (error.statusCode && error.statusCode < 500 && error.statusCode !== 401) {
        orders.clearPendingOrder(); this.pendingSubmission = null
        this.setData({ state: 'error', error: `${error.message} 请重新获取报价。`, canSubmit: false })
        if (error.code === 'OUT_OF_STOCK') {
          this.setData({ submitting: false })
          await this.loadStockChange()
        }
      } else {
        this.setData({ state: error.statusCode === 401 ? 'auth' : 'uncertain',
          error: error.statusCode === 401 ? error.message : '提交结果尚未确认。请重试确认结果，避免重复创建订单。',
          submitError: error.message, canSubmit: false })
      }
    } finally { this.setData({ submitting: false }) }
  },
  async loadStockChange() {
    this.setData({ stockPromptOpen: true, stockAdjustable: false, stockLines: [] })
    await this.refresh()
    if (this.data.state !== 'ready') return
    const affected = this.data.lines.filter((line) => line.status !== 'OK')
    this.setData({ stockLines: affected,
      stockAdjustable: affected.length > 0 && affected.every((line) => line.status === 'OUT_OF_STOCK' &&
        Number.isSafeInteger(line.availableQuantity) && line.availableQuantity > 0 && line.quantity > line.availableQuantity) })
  },
  dismissStockPrompt() { this.setData({ stockPromptOpen: false }) },
  adjustStock() {
    if (!this.data.stockAdjustable || this.data.state !== 'ready') return
    const available = new Map(this.data.stockLines.map((line) => [line.skuId, line.availableQuantity]))
    this.items = this.items.map((row) => ({ ...row, quantity: available.has(row.skuId) ?
      Math.min(row.quantity, available.get(row.skuId)) : row.quantity }))
    wx.setStorageSync('mall.checkoutSelection.v1', this.items)
    this.setData({ stockPromptOpen: false, stockAdjustable: false })
    return this.refresh()
  },
  selectPayment(event) {
    const method = event.detail.value
    if (this.data.submitting || !this.data.paymentChoices.some((choice) => choice.value === method)) return
    const quote = this.data.quote
    this.setData({ paymentMethod: method, canSubmit: session.loggedIn() && quote.orderSubmissionAvailable === true &&
      this.data.settlementReady && quote.ready === true && quote.confirmRequired !== true && (!quote.addressRequired || !!this.data.address) })
  },
  openOrders() { wx.navigateTo({ url: '/pages/orders/list' }) },
  selectCoupon(event) {
    const index = Number(event.detail.value)
    const selected = this.data.couponOptions[index]
    if (!selected) return
    this.couponId = selected.id
    return this.refresh()
  },
  onPointsInput(event) { this.setData({ pointsInput: event.detail.value, benefitError: '' }) },
  applyPoints() {
    const value = this.data.pointsInput.trim()
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) {
      this.setData({ benefitError: '请输入非负整数积分。' }); return
    }
    const points = Number(value)
    if (Number.isSafeInteger(this.data.quote.availablePoints) && points > this.data.quote.availablePoints) {
      this.setData({ benefitError: '超过当前可用积分，请调整后重试。' }); return
    }
    this.pointsToUse = points
    return this.refresh()
  },
  clearBenefits() {
    this.couponId = null
    this.pointsToUse = 0
    return this.refresh()
  },
  dismissLoginPrompt() { this.setData({ loginPromptOpen: false }) },
  loginToContinue() {
    session.rememberCheckout(this.items)
    this.setData({ loginPromptOpen: false })
    wx.navigateTo({ url: '/pages/login/login?returnTo=checkout' })
  },
  chooseAddress() {
    if (!session.loggedIn()) {
      this.loginToContinue()
      return
    }
    wx.navigateTo({ url: '/pages/addresses/addresses?select=1' })
  },
  confirmChanges() {
    const current = new Map(this.data.quote.lines.map((line) => [line.skuId, line.unitPriceFen]))
    this.items = this.items.map((row) => ({ ...row, seenPriceFen: current.get(row.skuId) }))
    wx.setStorageSync('mall.checkoutSelection.v1', this.items)
    return this.refresh()
  },
  backToCart() { wx.navigateTo({ url: '/pages/cart/cart' }) },
  onImageError(event) {
    const id = event.currentTarget.dataset.id
    this.setData({ failedImages: { ...this.data.failedImages, [id]: true } })
  },
})
