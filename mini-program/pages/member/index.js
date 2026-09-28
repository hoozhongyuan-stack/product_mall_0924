const { memberPage } = require('../../lib/member-page')
const { orderFilters } = require('../../lib/order-filters')
const { presentOverview } = require('../../lib/member')
Page({ ...memberPage({ path: '/api/v1/app/member/overview', present: presentOverview }),
  openPoints() { wx.navigateTo({ url: '/pages/member/points' }) },
  openConsumption() { wx.navigateTo({ url: '/pages/member/consumption' }) },
  openExchange() { wx.navigateTo({ url: '/pages/exchange/index' }) },
  openCoupons() { wx.navigateTo({ url: '/pages/coupons/index' }) },
  openCampaigns() { wx.navigateTo({ url: '/pages/coupons/campaigns' }) },
  openAddresses() { wx.navigateTo({ url: '/pages/addresses/addresses' }) },
  openOrders(event) {
    const filters = orderFilters(event && event.currentTarget ? event.currentTarget.dataset : {})
    const query = Object.entries(filters).filter(([, value]) => value).map(([key, value]) => `${key}=${value}`).join('&')
    wx.navigateTo({ url: `/pages/orders/list${query ? '?' + query : ''}` })
  },
})
