const orders = require('./orders')
module.exports = {
  redemptionInput(event) {
    if (this.data.busy || this.pending || this.hidden) return
    const { lineId, key } = event.currentTarget.dataset
    if (!['voucherCode', 'quantity', 'note'].includes(key) || !this.data.redeemItems.some(item => item.orderLineId === lineId && item.canRedeem)) return
    this.setData({ redemptionDrafts: { ...this.data.redemptionDrafts, [lineId]: { ...this.data.redemptionDrafts[lineId], [key]: event.detail.value } }, error: '' })
  },
  async redeem(event) {
    if (this.data.busy || this.pending || this.hidden || !this.data.order) return
    const lineId = event.currentTarget.dataset.lineId
    const item = this.data.redeemItems.find(row => row.orderLineId === lineId && row.canRedeem)
    if (!item) return
    const draft = this.data.redemptionDrafts[lineId] || {}
    const code = String(draft.voucherCode || '').trim(), quantity = Number(draft.quantity), note = String(draft.note || '').trim()
    if (!code || code.length > 200) { this.setData({ error: '请输入顾客出示的权益核销凭证。' }); return }
    if (!Number.isSafeInteger(quantity) || quantity < 1 || quantity > item.fulfillment.availableQuantity) {
      this.setData({ error: `请输入 1—${item.fulfillment.availableQuantity} 次，本次不能超过当前可核销数量。` }); return
    }
    if (note.length > 500) { this.setData({ error: '核销备注不能超过 500 字。' }); return }
    const gen = this.generation
    this.setData({ busy: true, error: '' })
    const confirmed = await new Promise(resolve => wx.showModal({ title: '确认核销权益', content: `${item.name || item.productName}，本次核销 ${quantity} 次。核销后将同步订单剩余权益。`,
      confirmText: '确认核销', success: result => resolve(result.confirm), fail: () => resolve(false) }))
    if (!this.valid(gen)) return
    this.setData({ busy: false })
    if (!confirmed || this.pending) return
    this.pending = { key: orders.requestKey(), endpoint: 'redemptions', body: { orderLineId: lineId, voucherCode: code,
      quantity, expectedRevision: item.fulfillment.voucherRevision, note } }
    return this.send()
  },
}
