const api = require('../../lib/api')

Page({
  data: { state: 'loading', error: '', addresses: [], selecting: false, selectedId: '' },
  onLoad(options) { this.setData({ selecting: options.select === '1', selectedId: wx.getStorageSync('mall.selectedAddressId') || '' }) },
  onShow() { return this.refresh() },
  async refresh() {
    this.setData({ state: 'loading', error: '' })
    try {
      const addresses = await api.get('/api/v1/app/addresses')
      this.setData({ addresses, state: addresses.length ? 'ready' : 'empty' })
    } catch (error) { this.setData({ state: error.statusCode === 401 ? 'auth' : 'error', error: error.message }) }
  },
  select(event) {
    if (!this.data.selecting || !this.data.addresses.some((item) => item.id === event.currentTarget.dataset.id)) return
    wx.setStorageSync('mall.selectedAddressId', event.currentTarget.dataset.id)
    wx.navigateBack({ delta: 1 })
  },
  login() { wx.navigateTo({ url: '/pages/login/login?returnTo=addresses' }) },
  add() { wx.navigateTo({ url: '/pages/addresses/edit' }) },
  edit(event) { wx.navigateTo({ url: `/pages/addresses/edit?id=${encodeURIComponent(event.currentTarget.dataset.id)}` }) },
  remove(event) {
    const address = this.data.addresses.find((item) => item.id === event.currentTarget.dataset.id)
    if (!address) return
    wx.showModal({ title: '删除收货地址', content: '删除后不能恢复。', success: async (result) => {
      if (!result.confirm) return
      try { await api.remove(`/api/v1/app/addresses/${address.id}`, { expectedRevision: address.revision }); this.refresh() }
      catch (error) { wx.showToast({ title: error.message, icon: 'none' }); this.refresh() }
    } })
  },
})
