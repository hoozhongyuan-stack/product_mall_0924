const { memberPage } = require('../../lib/member-page')
const { presentOverview } = require('../../lib/member')
Page({ ...memberPage({ path: '/api/v1/app/member/overview', present: presentOverview }),
  openPoints() { wx.navigateTo({ url: '/pages/member/points' }) },
  openConsumption() { wx.navigateTo({ url: '/pages/member/consumption' }) },
  openExchange() { wx.navigateTo({ url: '/pages/exchange/index' }) },
  openCoupons() { wx.navigateTo({ url: '/pages/coupons/index' }) },
  openCampaigns() { wx.navigateTo({ url: '/pages/coupons/campaigns' }) },
  openOrders() { wx.navigateTo({ url: '/pages/orders/list' }) },
})
