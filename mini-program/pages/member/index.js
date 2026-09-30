const { memberPage } = require('../../lib/member-page')
const { orderFilters } = require('../../lib/order-filters')
const { presentOverview } = require('../../lib/member')
const session = require('../../lib/session')
const { memberReturnTarget } = require('../../lib/member-route')
Page({ ...memberPage({ path: '/api/v1/app/member/overview', present: presentOverview }),
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
