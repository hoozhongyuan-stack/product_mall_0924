const session = require('../../lib/session')

Page({
  data: { agreed: false, busy: false, error: '' },
  onLoad(options) { this.returnTo = ['checkout', 'orders', 'member', 'coupons', 'exchange'].includes(options.returnTo) ? options.returnTo : '' },
  toggleAgreement(event) { this.setData({ agreed: event.detail.value.includes('agree') }) },
  openPrivacy() {
    if (wx.openPrivacyContract) wx.openPrivacyContract({ fail: () => wx.showToast({ title: '隐私条款暂不可查看', icon: 'none' }) })
    else wx.showToast({ title: '隐私条款暂不可查看', icon: 'none' })
  },
  async login() {
    if (!this.data.agreed || this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      await session.login()
      if (this.returnTo) wx.navigateBack({ delta: 1 })
      else wx.navigateTo({ url: '/pages/cart/cart' })
    } catch (error) { this.setData({ error: error.message }) }
    finally { this.setData({ busy: false }) }
  },
  back() { wx.navigateBack({ delta: 1 }) },
})
