const api = require('../../lib/api')
const session = require('../../lib/session')
const sale = require('../../lib/aftersales')
const returns = require('../../lib/return-shipment')
const { navigateInternal } = require('../../lib/page-links')
Page({
  data: { state: 'loading', error: '', saleCase: null, withdrawing: false, submittingReturn: false, returnUncertain: false, returnChecked: false,
    carrierName: '', trackingNo: '', actionError: '', actionMessage: '' },
  onLoad(options) {
    this.inactive = false; this.caseId = options.id || ''; this.shown = false
    this.returnIntent = returns.loadIntent(this.caseId)
    if (this.returnIntent) this.setData({ carrierName: this.returnIntent.body.carrierName,
      trackingNo: this.returnIntent.body.trackingNo, returnUncertain: true })
    this.protectDraft()
    return this.refresh()
  },
  onShow() { if (!this.shown) { this.shown = true; return }; return this.refresh() },
  onUnload() { this.inactive = true; this.generation = (this.generation || 0) + 1 },
  onPullDownRefresh() { return this.refresh().finally(() => wx.stopPullDownRefresh()) },
  clearPrivate(message) {
    this.generation = (this.generation || 0) + 1
    if (this.returnIntent) returns.clearIntent(this.caseId, this.returnIntent.key)
    this.returnIntent = null; this.memberToken = ''; this.returnFormDirty = false
    this.setData({ state: 'auth', saleCase: null, error: message || '请登录后查看自己的售后。',
      withdrawing: false, submittingReturn: false, returnUncertain: false, returnChecked: false,
      carrierName: '', trackingNo: '', actionError: '', actionMessage: '' })
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
    const token = wx.getStorageSync('mall.memberToken')
    if (this.memberToken && this.memberToken !== token) this.clearPrivate('登录状态已改变，请重新查看自己的售后。')
    if (this.data.withdrawing || this.data.submittingReturn) return
    const generation = this.generation = (this.generation || 0) + 1
    if (!session.loggedIn()) { this.clearPrivate(); return }
    if (!this.caseId) { this.setData({ state: 'unavailable', saleCase: null, error: '售后链接无效，请从订单重新进入。' }); return }
    const member = this.memberToken = wx.getStorageSync('mall.memberToken')
    this.setData({ state: 'loading', error: '', saleCase: null, returnChecked: false })
    try {
      const result = await api.get(`/api/v1/app/aftersales/${encodeURIComponent(this.caseId)}`)
      if (!this.current(member, generation)) return
      if (result.caseId !== this.caseId) throw new Error('售后内容不完整，请重新加载。')
      const row = sale.presentCase(result)
      const recorded = returns.matchesRecorded(this.returnIntent, row)
      if (recorded || !row.canSubmitReturnShipment) { this.clearReturnIntent(); this.returnFormDirty = false }
      this.setData({ state: 'ready', saleCase: row, returnChecked: true,
        ...(!this.returnIntent && !this.returnFormDirty ? { carrierName: row.returnShipment ? row.returnShipment.carrierName : '',
          trackingNo: row.returnShipment ? row.returnShipment.trackingNo : '' } : {}),
        ...(recorded ? { actionMessage: '退货物流已登记，以平台最新记录为准。', actionError: '' } : {}) })
      this.protectDraft()
    } catch (error) {
      if (!this.current(member, generation)) return
      if (error.statusCode === 401) this.clearPrivate(error.message)
      else this.setData({ state: error.statusCode === 404 ? 'unavailable' : 'error', saleCase: null, error: error.message })
    }
  },
  protectDraft() {
    const row = this.data.saleCase
    const changed = !!(this.data.carrierName || this.data.trackingNo) && (!row || !row.returnShipment ||
      this.data.carrierName !== row.returnShipment.carrierName || this.data.trackingNo !== row.returnShipment.trackingNo)
    if ((changed || this.returnIntent) && wx.enableAlertBeforeUnload) {
      wx.enableAlertBeforeUnload({ message: this.returnIntent ? '物流登记结果待核实，再次进入可刷新并恢复。' : '退货物流尚未保存，离开后需要重新填写。' })
    } else if (wx.disableAlertBeforeUnload) wx.disableAlertBeforeUnload()
  },
  clearReturnIntent() {
    if (this.returnIntent) returns.clearIntent(this.caseId, this.returnIntent.key)
    this.returnIntent = null
    this.setData({ returnUncertain: false })
  },
  onCarrierName(event) { this.updateReturnField('carrierName', event.detail.value) },
  onTrackingNo(event) { this.updateReturnField('trackingNo', event.detail.value) },
  updateReturnField(field, value) {
    if (this.data.submittingReturn || this.returnIntent || !this.data.saleCase ||
      !this.data.saleCase.canSubmitReturnShipment || !this.current(this.memberToken)) return
    this.returnFormDirty = true
    this.setData({ [field]: value, actionError: '', actionMessage: '' }); this.protectDraft()
  },
  async submitReturnShipment() {
    const row = this.data.saleCase
    if (!row || !row.canSubmitReturnShipment || this.data.submittingReturn || this.data.withdrawing ||
      !this.current(this.memberToken)) return
    if (this.returnIntent && !this.data.returnChecked) {
      this.setData({ actionError: '上次登记结果待核实，请先刷新处理进度，再按原资料重试。' }); return
    }
    try {
      if (!this.returnIntent) this.returnIntent = returns.saveIntent(this.caseId, {
        expectedRevision: row.revision, carrierName: this.data.carrierName, trackingNo: this.data.trackingNo })
    } catch (error) { this.setData({ actionError: error.message }); return }
    const intent = this.returnIntent; const member = this.memberToken; const generation = this.generation
    this.setData({ submittingReturn: true, actionError: '', actionMessage: '' }); this.protectDraft()
    try {
      const result = await api.post(`/api/v1/app/aftersales/${encodeURIComponent(this.caseId)}/return-shipment`,
        intent.body, { 'Idempotency-Key': intent.key })
      if (!this.current(member, generation)) return
      if (result.caseId !== this.caseId) throw new Error('售后内容不完整，请刷新核对登记结果。')
      const saved = sale.presentCase(result)
      this.clearReturnIntent(); this.returnFormDirty = false
      this.setData({ saleCase: saved, carrierName: saved.returnShipment ? saved.returnShipment.carrierName : intent.body.carrierName,
        trackingNo: saved.returnShipment ? saved.returnShipment.trackingNo : intent.body.trackingNo,
        returnChecked: true, actionMessage: '退货物流已登记，等待平台收货验收。退款尚未完成。' })
    } catch (error) {
      if (!this.current(member, generation)) return
      if (error.statusCode === 401) { this.clearPrivate(error.message); return }
      if (!error.statusCode || error.statusCode >= 500) {
        this.setData({ returnUncertain: true, returnChecked: false,
          actionError: '物流登记结果暂未确认。已保留本次资料，请先刷新处理进度；刷新成功后可按原资料重试。' })
      } else {
        this.clearReturnIntent()
        this.setData({ submittingReturn: false, actionError: error.message })
        if (error.statusCode === 409) {
          const reload = this.refresh(); const refreshGeneration = this.generation
          await reload
          if (this.current(member, refreshGeneration)) this.setData({ actionError: `${error.message} 请按最新处理状态继续。` })
        }
      }
    } finally {
      if (this.current(member, generation)) { this.setData({ submittingReturn: false }); this.protectDraft() }
    }
  },
  async withdraw() {
    const row = this.data.saleCase
    if (!row || !row.canWithdraw || this.data.withdrawing || this.data.submittingReturn || !this.current(this.memberToken)) return
    const member = this.memberToken
    this.setData({ withdrawing: true, actionError: '', actionMessage: '' })
    try {
      const confirmed = await new Promise((resolve) => wx.showModal({ title: '确认撤销售后申请？',
        content: '仅待审核申请可撤销。撤销后会释放本次申请占用的数量与金额，已经审核的申请不能撤销。',
        confirmText: '撤销申请', cancelText: '保留申请', confirmColor: '#b42318',
        success: (result) => resolve(result.confirm), fail: () => resolve(false) }))
      if (!confirmed || !this.current(member)) return
      const result = await api.post(`/api/v1/app/aftersales/${encodeURIComponent(this.caseId)}/withdraw`, { expectedRevision: row.revision })
      if (!this.current(member)) return
      this.setData({ saleCase: sale.presentCase(result), actionMessage: '申请已撤销。' })
    } catch (error) {
      if (!this.current(member)) return
      if (error.statusCode === 401) { this.clearPrivate(error.message); return }
      this.setData({ withdrawing: false }); await this.refresh()
      if (this.current(member)) this.setData({ actionError: `${error.message} 已重新核对当前申请状态；结果以平台最新记录为准。` })
    } finally { if (this.current(member)) this.setData({ withdrawing: false }) }
  },
  openOrder() {
    if (this.data.saleCase && this.current(this.memberToken)) navigateInternal(`/pages/orders/detail?id=${encodeURIComponent(this.data.saleCase.orderId)}`)
  },
  openApplications() {
    if (this.data.saleCase && this.current(this.memberToken)) navigateInternal(`/pages/aftersales/index?orderId=${encodeURIComponent(this.data.saleCase.orderId)}`)
  },
  openOrders() { navigateInternal('/pages/orders/list') },
  login() { navigateInternal('/pages/login/login?returnTo=orders') },
})
