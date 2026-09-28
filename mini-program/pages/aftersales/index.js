const api = require('../../lib/api')
const session = require('../../lib/session')
const sale = require('../../lib/aftersales')
const { money } = require('../../lib/catalog')
const { navigateInternal } = require('../../lib/page-links')
Page({
  data: { isPoints: false, state: 'loading', error: '', lines: [], rows: [], page: 0, total: 0, loadingMore: false,
    moreError: '', selectedLine: null, selectedOption: null, lineIndex: 0, optionIndex: 0,
    optionLabels: [], quantity: '1', reason: '', preview: null, previewState: 'idle', previewError: '',
    submitting: false, uncertain: false, actionError: '', actionMessage: '' },
  onLoad(options) {
    this.inactive = false
    this.orderId = options.orderId || ''
    this.shown = false
    this.intent = sale.loadIntent(this.orderId)
    this.setData({ uncertain: !!this.intent, ...(this.intent ? { reason: this.intent.body.reason,
      quantity: String(this.intent.body.quantity) } : {}) })
    this.protectDraft()
    return this.refresh()
  },
  onShow() { if (!this.shown) { this.shown = true; return }; return this.refresh() },
  onUnload() { this.inactive = true; this.generation = (this.generation || 0) + 1; this.previewGeneration = (this.previewGeneration || 0) + 1 },
  onPullDownRefresh() { return this.refresh().finally(() => wx.stopPullDownRefresh()) },
  onReachBottom() { return this.loadMore() },
  clearPrivate(message) {
    this.generation = (this.generation || 0) + 1
    this.previewGeneration = (this.previewGeneration || 0) + 1
    if (this.intent) sale.clearIntent(this.orderId, this.intent.key)
    this.intent = null
    this.memberToken = ''
    this.setData({ state: 'auth', error: message || '请登录后查看自己的售后。', lines: [], rows: [],
      selectedLine: null, selectedOption: null, reason: '', quantity: '1', preview: null,
      previewState: 'idle', uncertain: false, submitting: false, loadingMore: false, actionError: '', actionMessage: '' })
    this.protectDraft()
  },
  current(member, generation) {
    if (this.inactive) return false
    if (generation !== undefined && generation !== this.generation) return false
    if (!member || member !== wx.getStorageSync('mall.memberToken')) {
      this.clearPrivate('登录状态已改变，请重新查看自己的售后。'); return false
    }
    return true
  },
  async refresh() {
    if (this.data.submitting) return
    const currentMember = wx.getStorageSync('mall.memberToken')
    if (this.memberToken && this.memberToken !== currentMember) this.clearPrivate('登录状态已改变，请重新查看自己的售后。')
    const generation = this.generation = (this.generation || 0) + 1
    this.previewGeneration = (this.previewGeneration || 0) + 1
    if (!session.loggedIn()) { this.clearPrivate(); return }
    if (!this.orderId) { this.setData({ state: 'unavailable', error: '订单链接无效，请从我的订单重新进入。' }); return }
    const member = this.memberToken = wx.getStorageSync('mall.memberToken')
    this.setData({ state: 'loading', error: '', lines: [], rows: [], selectedLine: null,
      selectedOption: null, preview: null, previewState: 'idle', loadingMore: false, moreError: '' })
    try {
      const [options, list] = await Promise.all([
        api.get(`/api/v1/app/orders/${encodeURIComponent(this.orderId)}/aftersale-options`),
        api.get('/api/v1/app/aftersales', { orderId: this.orderId, page: 1, pageSize: 20 })])
      if (!this.current(member, generation)) return
      if (!Array.isArray(options.items) || !Array.isArray(list.items) || !Number.isInteger(list.total)) throw new Error('售后内容不完整，请重新加载。')
      const isPoints = options.orderKind === 'POINTS'
      const lines = options.items.map((line) => ({ ...line, optionLabels: (line.options || []).map((option) => sale.optionLabel(option, line.fulfillmentKind, options.orderKind)) }))
      this.setData({ state: 'ready', isPoints, lines, rows: list.items.map(sale.presentCase), page: list.page,
        total: list.total, lineLabels: lines.map((line) => line.title) })
      if (!this.intent && lines.length) this.selectLine(0)
      if (this.intent) this.setData({ uncertain: true })
    } catch (error) {
      if (!this.current(member, generation)) return
      if (error.statusCode === 401) this.clearPrivate(error.message)
      else this.setData({ state: error.statusCode === 404 ? 'unavailable' : 'error', error: error.message })
    }
  },
  async loadMore() {
    if (this.data.state !== 'ready' || this.data.loadingMore || this.data.rows.length >= this.data.total) return
    const member = this.memberToken; const generation = this.generation
    this.setData({ loadingMore: true, moreError: '' })
    try {
      const result = await api.get('/api/v1/app/aftersales', { orderId: this.orderId, page: this.data.page + 1, pageSize: 20 })
      if (!this.current(member, generation)) return
      if (!Array.isArray(result.items)) throw new Error('售后列表不完整，请重试。')
      this.setData({ rows: [...this.data.rows, ...result.items.map(sale.presentCase)], page: result.page, total: result.total })
    } catch (error) {
      if (!this.current(member, generation)) return
      if (error.statusCode === 401) this.clearPrivate(error.message)
      else this.setData({ moreError: error.message })
    } finally { if (this.current(member, generation)) this.setData({ loadingMore: false }) }
  },
  selectLine(index) {
    this.previewGeneration = (this.previewGeneration || 0) + 1
    const line = this.data.lines[index]
    if (!line) return
    this.setData({ lineIndex: index, selectedLine: line, optionIndex: 0, selectedOption: line.options[0] || null,
      optionLabels: line.optionLabels || line.options.map((option) => sale.optionLabel(option, line.fulfillmentKind, this.data.isPoints ? 'POINTS' : 'CASH')), quantity: '1', preview: null, previewState: 'idle' })
  },
  onLine(event) { if (!this.intent && !this.data.submitting) this.selectLine(Number(event.detail.value)) },
  onOption(event) {
    if (this.intent || this.data.submitting) return
    const index = Number(event.detail.value)
    this.setData({ optionIndex: index, selectedOption: this.data.selectedLine.options[index], preview: null, previewState: 'idle' })
    this.previewGeneration = (this.previewGeneration || 0) + 1
  },
  onQuantity(event) {
    if (this.intent || this.data.submitting) return
    this.previewGeneration = (this.previewGeneration || 0) + 1
    this.setData({ quantity: event.detail.value, preview: null, previewState: 'idle', previewError: '' })
  },
  onReason(event) {
    if (this.intent || this.data.submitting) return
    this.setData({ reason: event.detail.value, actionError: '', actionMessage: '' }); this.protectDraft()
  },
  selection() {
    const line = this.data.selectedLine; const option = this.data.selectedOption; const quantity = Number(this.data.quantity)
    if (!line || !option || !/^\d+$/.test(String(this.data.quantity)) || !Number.isSafeInteger(quantity) || quantity < 1 || quantity > option.maxQuantity) {
      throw new Error('请选择可申请的商品、类型，并填写范围内的整数数量。')
    }
    return { lineId: line.lineId, kind: option.kind, redemptionScope: option.redemptionScope, quantity }
  },
  async previewSelection() {
    if (this.intent || this.data.submitting || !this.current(this.memberToken)) return
    const generation = this.previewGeneration = (this.previewGeneration || 0) + 1; const member = this.memberToken
    this.setData({ preview: null, previewState: 'loading', previewError: '' })
    try {
      const body = this.selection(); const result = await api.post('/api/v1/app/aftersales/preview', body)
      if (generation !== this.previewGeneration || !this.current(member)) return
      if (!Number.isSafeInteger(result.amountFen) || result.amountFen < 0 || result.quantity !== body.quantity || result.lineId !== body.lineId) throw new Error('服务端退款金额不完整，请重新核对。')
      const isPoints = result.orderKind === 'POINTS'
      if (isPoints && (!Number.isSafeInteger(result.refundPoints) || result.refundPoints < 0)) throw new Error('服务端退回积分不完整，请重新核对。')
      this.setData({ preview: { ...result, isPoints, amountLabel: isPoints ? `${result.refundPoints} 积分` : money(result.amountFen), cashCopy: isPoints ? '兑换积分不会在申请后立即退回；审核与最终确认完成后返还，原已过期积分沿成交规则给予短期有效期。' : result.amountFen === 0 ? '本次没有现金退回；审核通过后按规则处理数量与权益。' : '' }, previewState: 'ready' })
    } catch (error) {
      if (generation !== this.previewGeneration || !this.current(member)) return
      if (error.statusCode === 401) this.clearPrivate(error.message)
      else this.setData({ previewState: 'error', previewError: error.message })
    }
  },
  protectDraft() {
    if (this.data.reason.trim() || this.intent) {
      if (wx.enableAlertBeforeUnload) wx.enableAlertBeforeUnload({ message: this.intent ? '申请结果尚未确认，请返回本订单继续核对同一申请。' : '离开后不会保存未提交的售后说明。' })
    } else if (wx.disableAlertBeforeUnload) wx.disableAlertBeforeUnload()
  },
  async submit() {
    if (this.data.submitting || !this.current(this.memberToken)) return
    if (!this.intent) {
      const reason = this.data.reason.trim()
      if (reason.length < 5 || reason.length > 500) { this.setData({ actionError: '请填写 5—500 字的售后原因和说明。' }); return }
      if (this.data.previewState !== 'ready' || !this.data.preview) { this.setData({ actionError: '请先核对服务端退款金额。' }); return }
      try { this.intent = sale.saveIntent(this.orderId, { ...this.selection(), reason }) }
      catch (error) { this.setData({ actionError: error.message }); return }
    }
    const intent = this.intent; const member = this.memberToken
    this.setData({ submitting: true, actionError: '', actionMessage: '', uncertain: true }); this.protectDraft()
    try {
      const result = await api.post('/api/v1/app/aftersales', intent.body, { 'Idempotency-Key': intent.key })
      if (!this.current(member)) return
      sale.clearIntent(this.orderId, intent.key); this.intent = null
      this.setData({ reason: '', uncertain: false, actionMessage: '申请已提交，等待平台审核。' }); this.protectDraft()
      navigateInternal(`/pages/aftersales/detail?id=${encodeURIComponent(result.caseId)}`)
    } catch (error) {
      if (!this.current(member)) return
      if (error.statusCode === 401) { this.clearPrivate(error.message); return }
      if (error.statusCode && error.statusCode < 500) {
        sale.clearIntent(this.orderId, intent.key); this.intent = null
        this.setData({ submitting: false, uncertain: false }); await this.refresh()
        this.setData({ actionError: `${error.message} 请重新核对可申请范围和金额后提交。` })
      } else this.setData({ actionError: `${error.message} 申请结果尚未确认，请重试同一申请。` })
    } finally {
      if (this.current(member)) { this.setData({ submitting: false, uncertain: !!this.intent }); this.protectDraft() }
    }
  },
  openCase(event) { if (this.current(this.memberToken)) navigateInternal(`/pages/aftersales/detail?id=${encodeURIComponent(event.currentTarget.dataset.id)}`) },
  openOrder() { navigateInternal(`/pages/orders/detail?id=${encodeURIComponent(this.orderId)}`) },
  openOrders() { navigateInternal('/pages/orders/list') },
  login() { navigateInternal('/pages/login/login?returnTo=orders') },
})
