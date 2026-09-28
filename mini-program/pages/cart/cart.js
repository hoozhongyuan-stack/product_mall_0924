const api = require('../../lib/api')
const cart = require('../../lib/cart')
const { money } = require('../../lib/catalog')

Page({
  data: { rows: [], state: 'loading', error: '', quote: null, total: '¥0', allSelected: false, selectedCount: 0, managing: false, failedImages: {} },
  onShow() { return this.refresh() },
  async refresh() {
    const token = this.refreshToken = (this.refreshToken || 0) + 1
    const rows = cart.read()
    this.setData({ allSelected: rows.length > 0 && rows.every((row) => row.selected !== false),
      selectedCount: rows.filter((row) => row.selected !== false).reduce((total, row) => total + row.quantity, 0),
      rows: rows.map((row) => ({ ...row, price: Number.isSafeInteger(row.seenPriceFen) ? money(row.seenPriceFen) : '待核价', priceCached: true })), state: rows.length ? 'loading' : 'empty', error: '', quote: null })
    if (!rows.length) return
    const selected = cart.selected()
    if (!selected.length) { this.setData({ state: 'ready', total: '¥0' }); return }
    try {
      const quote = await api.post('/api/v1/app/checkout/quotes', { items: cart.quoteItems(selected) })
      if (token !== this.refreshToken) return
      const lines = new Map(quote.lines.map((line) => [line.skuId, line]))
      this.setData({ state: 'ready', quote, total: money(quote.goodsTotalFen),
        rows: rows.map((row) => ({ ...row, live: lines.get(row.skuId) || null,
          previousPrice: Number.isSafeInteger(row.seenPriceFen) ? money(row.seenPriceFen) : '',
          fulfillmentLabel: lines.has(row.skuId) ? (lines.get(row.skuId).fulfillmentKind === 'REDEEM' ? '到店核销' : '快递发货') : '',
          canReselect: /^[0-9a-fA-F-]{36}$/.test((lines.get(row.skuId) || {}).productId || ''),
          price: lines.has(row.skuId) ? lines.get(row.skuId).status === 'NOT_FOUND' ? '已失效' :
            money(lines.get(row.skuId).unitPriceFen) :
            Number.isSafeInteger(row.seenPriceFen) ? money(row.seenPriceFen) : '待核价',
          priceCached: !lines.has(row.skuId) })) })
    } catch (error) { if (token === this.refreshToken) this.setData({ state: 'ready', error: error.message, total: '¥0' }) }
  },
  toggleManaging() { this.setData({ managing: !this.data.managing }) },
  reselectSku(event) {
    const row = this.data.rows.find((item) => item.skuId === event.currentTarget.dataset.id)
    const id = row && row.live && row.live.productId
    if (!id || !/^[0-9a-fA-F-]{36}$/.test(id)) return
    wx.navigateTo({ url: `/pages/product/detail?productId=${encodeURIComponent(id)}&selectSku=1&replaceSkuId=${encodeURIComponent(row.skuId)}` })
  },
  toggle(event) { cart.update(event.currentTarget.dataset.id, { selected: Boolean(event.detail.value.length) }); return this.refresh() },
  toggleAll() {
    const rows = cart.read()
    cart.write(rows.map((row) => ({ ...row, selected: rows.some((item) => item.selected === false) })))
    return this.refresh()
  },
  changeQuantity(event) {
    const id = event.currentTarget.dataset.id
    const row = cart.read().find((item) => item.skuId === id)
    if (!row) return
    try { cart.update(id, { quantity: row.quantity + Number(event.currentTarget.dataset.step) }); return this.refresh() }
    catch (error) { wx.showToast({ title: error.message, icon: 'none' }) }
  },
  removeItem(event) { cart.remove(event.currentTarget.dataset.id); return this.refresh() },
  onImageError(event) {
    const id = event.currentTarget.dataset.id
    this.setData({ failedImages: { ...this.data.failedImages, [id]: true } })
  },
  confirmPrice(event) {
    const row = this.data.rows.find((item) => item.skuId === event.currentTarget.dataset.id)
    if (row && row.live) { cart.update(row.skuId, { seenPriceFen: row.live.unitPriceFen }); return this.refresh() }
  },
  checkout() {
    if (!this.data.quote || !this.data.quote.ready || this.data.quote.confirmRequired) {
      wx.showToast({ title: '请先处理价格或库存变化', icon: 'none' }); return
    }
    wx.setStorageSync('mall.checkoutSelection.v1', cart.selected())
    wx.navigateTo({ url: '/pages/checkout/checkout' })
  },
  shop() { wx.navigateTo({ url: '/pages/index/index' }) },
  openOrders() { wx.navigateTo({ url: '/pages/orders/list' }) },
})
