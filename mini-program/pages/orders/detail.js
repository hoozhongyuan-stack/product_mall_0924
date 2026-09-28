const api = require('../../lib/api')
const session = require('../../lib/session')
const orders = require('../../lib/orders')
const wechat = require('../../lib/wechat-payment')
const voucherImages = require('../../lib/voucher-image')
const { navigateInternal } = require('../../lib/page-links')

Page({
  data: { state: 'loading', error: '', order: null, items: [], reports: [], statusLabel: '',
    isPaid: false, canReport: false, canCancel: false, isResult: false, resultTitle: '',
    note: '', actionError: '', actionMessage: '', reporting: false, cancelling: false,
    paying: false, querying: false, canPayWechat: false, showWechatPayment: false, paymentUncertain: false,
    confirmingReceipt: false, shipment: null, canConfirmReceipt: false, failedVoucherImages: {},
    voucherQrPaths: {}, voucherQrLoading: {}, trackingState: 'idle', trackingEvents: [] },
  onLoad(options) {
    this.orderId = options.id || ''
    this.paymentIntent = wechat.load(this.orderId)
    this.setData({ paymentUncertain: !!this.paymentIntent && this.paymentIntent.phase === 'PAYMENT_UNKNOWN' })
    this.shown = false
    this.setData({ isResult: options.result === '1' })
    return this.refresh()
  },
  onShow() {
    if (!this.shown) { this.shown = true; return }
    return this.refresh()
  },
  onUnload() { this.trackingToken = (this.trackingToken || 0) + 1; this.resetVoucherImages() },
  onPullDownRefresh() { return this.refresh().finally(() => wx.stopPullDownRefresh()) },
  resetVoucherImages() {
    this.voucherImageToken = (this.voucherImageToken || 0) + 1
    voucherImages.cleanup()
    this.setData({ voucherQrPaths: {}, voucherQrLoading: {}, failedVoucherImages: {} })
  },
  async refresh() {
    if (this.data.reporting || this.data.cancelling || this.data.paying || this.data.querying || this.data.confirmingReceipt) return
    this.resetVoucherImages()
    this.trackingToken = (this.trackingToken || 0) + 1
    const token = this.refreshToken = (this.refreshToken || 0) + 1
    if (!this.orderId) { this.setData({ state: 'unavailable', order: null, error: '订单链接无效，请从我的订单重新进入。' }); return }
    if (!session.loggedIn()) {
      this.clearPayment()
      this.reportIntent = null
      this.setData({ state: 'auth', order: null, items: [], reports: [], shipment: null, failedVoucherImages: {}, note: '', reportUncertain: false,
        canReport: false, canCancel: false, error: '请登录后查看自己的订单。' }); return
    }
    this.setData({ state: 'loading', error: '', order: null, items: [], reports: [], shipment: null,
      trackingState: 'idle', trackingEvents: [], failedVoucherImages: {} })
    const memberSession = wx.getStorageSync('mall.memberToken')
    try {
      const order = await api.get(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}`)
      if (token !== this.refreshToken) return
      if (memberSession !== wx.getStorageSync('mall.memberToken')) {
        this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新核实自己的订单。' }); return
      }
      this.acceptOrder(order)
      if (this.data.shipment) await this.loadTracking()
    } catch (error) {
      if (error.statusCode === 401) { this.reportIntent = null; this.clearPayment() }
      if (token === this.refreshToken) this.setData({ state: error.statusCode === 401 ? 'auth' : error.statusCode === 404 ? 'unavailable' : 'error',
        error: error.message, order: null, items: [], reports: [], shipment: null, failedVoucherImages: {},
        ...(error.statusCode === 401 ? { note: '', reportUncertain: false } : {}),
        canReport: false, canCancel: false })
    }
  },
  onNote(event) {
    if (this.data.reporting || this.reportIntent) return
    this.setData({ note: event.detail.value, actionError: '', actionMessage: '' })
    this.protectDraft()
  },
  protectDraft() {
    if (this.data.note.trim() || this.reportIntent) {
      if (typeof wx.enableAlertBeforeUnload === 'function') wx.enableAlertBeforeUnload({ message: '付款报告尚未确认提交，离开后不会保存草稿。' })
    } else if (typeof wx.disableAlertBeforeUnload === 'function') wx.disableAlertBeforeUnload()
  },
  async reportPayment() {
    if ((!this.data.canReport && !this.reportIntent) || this.data.reporting || this.data.cancelling || this.data.paying || this.data.querying || this.data.confirmingReceipt) return
    if (!this.reportIntent && this.data.order && orders.present(this.data.order).isExpired) {
      this.setData({ ...orders.present(this.data.order), actionError: '付款时限已到，不能提交新的付款报告。请刷新订单状态，已付款请联系平台核实。' })
      return
    }
    const note = this.reportIntent ? this.reportIntent.note : this.data.note.trim()
    if (!note || note.length > 500) { this.setData({ actionError: '请填写 1—500 字的付款说明或流水参考信息。' }); return }
    if (!this.reportIntent) this.reportIntent = { key: orders.requestKey(), note }
    this.setData({ reporting: true, actionError: '', actionMessage: '' })
    try {
      const order = await api.post(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}/payment-report`,
        { note: this.reportIntent.note }, { 'Idempotency-Key': this.reportIntent.key })
      this.setData({ ...orders.present(order), note: '', actionMessage: '付款报告已提交，仍待平台核实实际到账。' })
      this.reportIntent = null
    } catch (error) {
      if (error.statusCode && error.statusCode < 500 && error.statusCode !== 401) this.reportIntent = null
      if (error.statusCode === 401) {
        this.resetVoucherImages()
        this.clearPayment()
        this.reportIntent = null
        this.setData({ state: 'auth', error: error.message, order: null, items: [], reports: [], note: '', canReport: false, canCancel: false })
      }
      this.setData({ actionError: `${error.message}${this.reportIntent ? ' 报告结果尚未确认，请重试同一报告。' : ''}` })
    } finally {
      this.setData({ reporting: false, reportUncertain: !!this.reportIntent })
      this.protectDraft()
    }
  },
  async cancelOrder() {
    if (!this.data.canCancel || this.data.reporting || this.data.cancelling || this.data.paying || this.data.querying || this.data.confirmingReceipt) return
    this.setData({ cancelling: true, actionError: '', actionMessage: '' })
    try {
      const confirmed = await new Promise((resolve) => wx.showModal({ title: '确认取消订单？',
        content: '取消会释放库存和优惠占用。若您已经付款，请先等待平台核实；关闭后到账会进入异常处理，原订单不会恢复。',
        confirmText: '取消订单', cancelText: '保留订单', confirmColor: '#b42318',
        success: (result) => resolve(result.confirm), fail: () => resolve(false) }))
      if (!confirmed) return
      const order = await api.post(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}/cancel`, {})
      this.setData({ ...orders.present(order), actionMessage: '订单已关闭。若已付款，请联系平台处理异常到账。' })
    } catch (error) {
      if (error.statusCode === 401) this.paymentError(error)
      this.setData({ actionError: error.message })
    } finally { this.setData({ cancelling: false }) }
  },
  clearPayment() {
    this.paymentIntent = null
    wechat.clear(this.orderId)
    this.setData({ paymentUncertain: false, canPayWechat: false, showWechatPayment: false })
  },
  acceptOrder(order) {
    this.resetVoucherImages()
    const display = orders.present(order)
    if (order.status !== 'PENDING_PAYMENT') this.clearPayment()
    const qrRows = order.status === 'PAID' ? order.items.filter((item) => item.fulfillmentKind === 'REDEEM' &&
      item.fulfillment && typeof item.fulfillment.voucherQrDataUrl === 'string' && item.fulfillment.voucherQrDataUrl) : []
    const token = this.voucherImageToken
    const memberSession = wx.getStorageSync('mall.memberToken')
    this.displayMemberToken = memberSession
    this.setData({ ...display, state: 'ready', voucherQrLoading: Object.fromEntries(qrRows.map((item) => [item.orderLineId, true])) })
    qrRows.forEach((item) => voucherImages.write(item.fulfillment.voucherQrDataUrl).then((path) => {
      if (token !== this.voucherImageToken || memberSession !== wx.getStorageSync('mall.memberToken')) {
        voucherImages.release(path); return
      }
      this.setData({ voucherQrPaths: { ...this.data.voucherQrPaths, [item.orderLineId]: path },
        voucherQrLoading: { ...this.data.voucherQrLoading, [item.orderLineId]: false } })
    }).catch(() => {
      if (token !== this.voucherImageToken || memberSession !== wx.getStorageSync('mall.memberToken')) return
      this.setData({ failedVoucherImages: { ...this.data.failedVoucherImages, [item.orderLineId]: true },
        voucherQrLoading: { ...this.data.voucherQrLoading, [item.orderLineId]: false } })
    }))
  },
  async loadTracking() {
    if (!this.data.shipment || !session.loggedIn()) return
    const memberSession = wx.getStorageSync('mall.memberToken')
    if (this.displayMemberToken !== memberSession) {
      this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录查看物流轨迹。' }); return
    }
    const token = this.trackingToken = (this.trackingToken || 0) + 1
    this.setData({ trackingState: 'loading', trackingEvents: [],
      trackingMessage: '正在查询物流轨迹…' })
    try {
      const result = await api.get(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}/tracking`)
      if (token !== this.trackingToken) return
      if (memberSession !== wx.getStorageSync('mall.memberToken')) {
        this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录查看物流轨迹。' }); return
      }
      const events = Array.isArray(result.events) ? result.events.map((event, index) => ({
        ...event, key: `${index}-${event.time}` })) : []
      const status = result.status
      const messages = { IN_TRANSIT: '运输中，以下为承运商提供的物流轨迹。',
        DELIVERED: '物流显示已签收；如未收到商品，请联系平台。',
        EXCEPTION: '物流出现异常，请联系承运商或平台。',
        NO_EVENTS: '暂无物流轨迹，请保存运单号并稍后重试。',
        UNAVAILABLE: '物流轨迹暂不可用，发货状态不受影响。请保存快递公司和运单号，稍后重试。' }
      this.setData({ trackingState: status === 'UNAVAILABLE' ? 'error' : 'ready',
        trackingEvents: events, trackingMessage: messages[status] || messages.UNAVAILABLE })
    } catch (error) {
      if (token !== this.trackingToken) return
      if (error.statusCode === 401 || memberSession !== wx.getStorageSync('mall.memberToken')) {
        this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录查看物流轨迹。' }); return
      }
      this.setData({ trackingState: 'error', trackingEvents: [],
        trackingMessage: '物流轨迹暂不可用，发货状态不受影响。请保存快递公司和运单号，稍后重试。' })
    }
  },
  onVoucherImageError(event) {
    const lineId = event.currentTarget.dataset.lineId
    if (!lineId) return
    const path = this.data.voucherQrPaths[lineId]
    if (path) voucherImages.release(path)
    this.setData({ failedVoucherImages: { ...this.data.failedVoucherImages, [lineId]: true },
      voucherQrPaths: { ...this.data.voucherQrPaths, [lineId]: '' } })
  },
  copyVoucher(event) {
    const code = event.currentTarget.dataset.code
    if (!this.data.isPaid || typeof code !== 'string' || !code) return
    if (!session.loggedIn() || this.displayMemberToken !== wx.getStorageSync('mall.memberToken')) {
      this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录查看凭证。' }); return
    }
    wx.setClipboardData({ data: code, success: () => this.setData({ actionMessage: '凭证码已复制。到店核销时请出示给店员。', actionError: '' }),
      fail: () => this.setData({ actionError: '复制失败，请长按凭证码选择复制。' }) })
  },
  paymentError(error) {
    if (error.statusCode === 401) {
      this.trackingToken = (this.trackingToken || 0) + 1
      this.resetVoucherImages()
      this.clearPayment()
      this.reportIntent = null
      this.setData({ state: 'auth', error: error.message, order: null, items: [], reports: [], shipment: null,
        trackingState: 'idle', trackingEvents: [], failedVoucherImages: {}, note: '',
        canReport: false, canCancel: false, reportUncertain: false })
    }
    this.setData({ actionError: error.message })
  },
  async confirmReceipt() {
    if (!this.data.canConfirmReceipt || this.data.confirmingReceipt || this.data.reporting || this.data.cancelling ||
        this.data.paying || this.data.querying) return
    if (!session.loggedIn() || this.displayMemberToken !== wx.getStorageSync('mall.memberToken')) {
      this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录核实自己的订单。' }); return
    }
    this.setData({ confirmingReceipt: true, actionError: '', actionMessage: '' })
    try {
      const confirmed = await new Promise((resolve) => wx.showModal({ title: '确认已收到商品？',
        content: '请先核对实物商品已签收。确认后，实物订单项将完成履约。',
        confirmText: '确认收货', cancelText: '暂不确认', confirmColor: '#24583d',
        success: (result) => resolve(result.confirm), fail: () => resolve(false) }))
      if (!confirmed) return
      this.refreshToken = (this.refreshToken || 0) + 1
      const memberSession = wx.getStorageSync('mall.memberToken')
      const order = await api.post(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}/confirm-receipt`, {})
      if (memberSession !== wx.getStorageSync('mall.memberToken')) {
        this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录核实收货结果。' }); return
      }
      this.acceptOrder(order)
      this.setData({ actionMessage: order.items.some((line) => line.fulfillmentKind === 'REDEEM') ?
        '实物商品已确认收货。核销商品按各自进度继续履约。' : '实物商品已确认收货。' })
    } catch (error) {
      if (error.statusCode === 401) this.paymentError(error)
      this.setData({ actionError: `${error.message} 确认结果尚未核实，请刷新订单后重试。` })
    } finally { this.setData({ confirmingReceipt: false }) }
  },
  async payWechat() {
    if (this.data.paying || this.data.querying || this.data.reporting || this.data.cancelling || this.data.confirmingReceipt) return
    if (this.paymentIntent && this.paymentIntent.phase === 'PAYMENT_UNKNOWN') return this.queryWechat()
    if (!this.data.order || !orders.present(this.data.order).canPayWechat) {
      if (this.data.order) this.setData({ ...orders.present(this.data.order) })
      return
    }
    if (!session.loggedIn()) { this.paymentError({ statusCode: 401, message: '请登录后支付自己的订单。' }); return }
    this.refreshToken = (this.refreshToken || 0) + 1
    const memberSession = wx.getStorageSync('mall.memberToken')
    this.setData({ paying: true, actionError: '', actionMessage: '' })
    if (!this.paymentIntent) this.paymentIntent = { orderId: this.orderId, key: orders.requestKey(), phase: 'PREPAY' }
    wechat.save(this.paymentIntent)
    try {
      const result = await api.post(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}/wechat/prepay`, {},
        { 'Idempotency-Key': this.paymentIntent.key })
      if (memberSession !== wx.getStorageSync('mall.memberToken')) { this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录核实支付结果。' }); return }
      // Persist before opening native payment: process loss cannot trigger a blind second payment.
      this.paymentIntent = { ...this.paymentIntent, phase: 'PAYMENT_UNKNOWN' }
      wechat.save(this.paymentIntent)
      this.setData({ paymentUncertain: true })
      try {
        await wechat.invoke(result)
      } catch (error) {
        // Invalid parameters / unsupported client never opened native payment.
        this.paymentIntent = { ...this.paymentIntent, phase: 'PREPAY' }
        wechat.save(this.paymentIntent)
        this.setData({ paymentUncertain: false })
        throw error
      }
      // wx success, cancel and failure all require the same server query.
      this.setData({ paying: false })
      await this.queryWechat()
    } catch (error) { this.paymentError(error) }
    finally { this.setData({ paying: false }) }
  },
  async queryWechat() {
    if (this.data.querying || this.data.paying || this.data.reporting || this.data.cancelling || this.data.confirmingReceipt || !this.data.order ||
        this.data.order.paymentMethod !== 'WECHAT') return
    if (!session.loggedIn()) { this.paymentError({ statusCode: 401, message: '请登录后查询自己的订单。' }); return }
    this.refreshToken = (this.refreshToken || 0) + 1
    const memberSession = wx.getStorageSync('mall.memberToken')
    this.setData({ querying: true, actionError: '', actionMessage: '' })
    try {
      const result = await api.post(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}/wechat/query`, {})
      const order = await api.get(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}`)
      if (memberSession !== wx.getStorageSync('mall.memberToken')) { this.paymentError({ statusCode: 401, message: '登录状态已改变，请重新登录核实支付结果。' }); return }
      this.acceptOrder(order)
      if (order.status === 'PAID') {
        this.setData({ actionMessage: '服务端已确认收款，请查看最新订单信息。' })
      } else if (order.status === 'CLOSED') {
        this.setData({ actionMessage: '订单已关闭。若已付款，请联系平台处理异常到账。' })
      } else if (result && result.paymentState === 'NOTPAY' &&
          !['SUCCESS', 'USERPAYING', 'CLOSED'].includes(order.wechatPaymentState)) {
        if (this.paymentIntent) {
          this.paymentIntent = { ...this.paymentIntent, phase: 'PREPAY' }
          wechat.save(this.paymentIntent)
        }
        this.setData({ paymentUncertain: false, actionMessage: '服务端查单显示未支付。可在付款时限内再次发起支付。' })
      } else {
        this.paymentIntent = { ...(this.paymentIntent || { orderId: this.orderId, key: orders.requestKey() }), phase: 'PAYMENT_UNKNOWN' }
        wechat.save(this.paymentIntent)
        this.setData({ paymentUncertain: true, actionMessage: result && result.paymentState === 'SUCCESS' ?
          '微信已返回到账，服务端仍在核实结算。请勿重复支付；稍后查询或联系平台核实。' :
          '服务端尚未确认最终支付结果。请稍后查询，避免重复支付。' })
      }
    } catch (error) { this.paymentError(error) }
    finally { this.setData({ querying: false }) }
  },
  login() { wx.navigateTo({ url: '/pages/login/login?returnTo=orders' }) },
  openAftersales() {
    if (!this.data.isPaid || !session.loggedIn() || this.displayMemberToken !== wx.getStorageSync('mall.memberToken')) return
    navigateInternal(`/pages/aftersales/index?orderId=${encodeURIComponent(this.orderId)}`)
  },
  openOrders() { wx.navigateTo({ url: '/pages/orders/list' }) },
  shop() { wx.navigateTo({ url: '/pages/index/index' }) },
})
