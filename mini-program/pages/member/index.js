const api = require('../../lib/api')
const { memberPage } = require('../../lib/member-page')
const { orderFilters } = require('../../lib/order-filters')
const { presentOverview } = require('../../lib/member')
const { profileControls } = require('../../lib/member-profile')
const session = require('../../lib/session')
const { memberReturnTarget } = require('../../lib/member-route')
const base = memberPage({ path: '/api/v1/app/member/overview', present: presentOverview })
Page({ ...base, ...profileControls,
  data: { ...base.data, hasStoreCenter: false, editingProfile: false, nicknameDraft: '', profileError: '', profileBusy: false },
  async onShow() {
    const identity = wx.getStorageSync('mall.memberToken'); this.setData({ hasStoreCenter: false })
    await base.onShow.call(this)
    if (!identity || identity !== wx.getStorageSync('mall.memberToken')) return
    try { const result = await api.get('/api/v1/app/store-center/stores'); if (identity === wx.getStorageSync('mall.memberToken')) this.setData({ hasStoreCenter: !!result.items.length }) } catch (_) { this.setData({ hasStoreCenter: false }) }
  },
  openStoreCenter() { wx.navigateTo({ url: '/pages/store-center/index' }) },
  clearPrivate() { base.clearPrivate.call(this); this.setData({ hasStoreCenter: false, editingProfile: false, nicknameDraft: '', profileError: '', profileBusy: false }) },
  avatarError() { if (this.data.member) this.setData({ member: { ...this.data.member, avatarDisplayUrl: '' } }) },
  async logout() {
    if (this.data.profileBusy) return
    const originalToken = wx.getStorageSync('mall.memberToken')
    this.setData({ profileBusy: true, profileError: '' })
    try { await session.logout(); this.invalidate(); this.setData({ state: 'auth', error: '' }) }
    catch (error) {
      if (originalToken !== wx.getStorageSync('mall.memberToken')) { this.invalidate(); this.setData({ state: 'auth', error: '' }) }
      else this.setData({ profileError: error.message })
    }
    finally { this.setData({ profileBusy: false }) }
  },
  openPrivate(next, options = {}) {
    const target = memberReturnTarget({ next, ...options })
    if (!target) return
    if (session.loggedIn() && this.data.state !== 'auth') wx.navigateTo({ url: target })
    else {
      const filters = next === 'orders' ? orderFilters(options) : {}
      const query = Object.entries(filters).filter(([, value]) => value)
        .map(([key, value]) => `&${key}=${value}`).join('')
      wx.navigateTo({ url: `/pages/login/login?returnTo=member&next=${next}${query}` })
    }
  },
  openPoints() { this.openPrivate('points') },
  openConsumption() { this.openPrivate('consumption') },
  openExchange() { wx.navigateTo({ url: '/pages/exchange/index' }) },
  openCoupons() { this.openPrivate('coupons') },
  openCampaigns() { this.openPrivate('campaigns') },
  openAddresses() { this.openPrivate('addresses') },
  openOrders(event) {
    const filters = orderFilters(event && event.currentTarget ? event.currentTarget.dataset : {})
    this.openPrivate('orders', filters)
  },
})
