const { memberPage } = require('../../lib/member-page')
const { presentOverview } = require('../../lib/member')
const base = memberPage({ path: '/api/v1/app/member/overview', present: presentOverview })
Page({ ...base,
  data: { ...base.data, panel: 'rights', panels: [{ key: 'rights', label: '权益与等级' }, { key: 'points', label: '积分账户' }, { key: 'rules', label: '会员规则' }] },
  selectPanel(event) { const panel = event.currentTarget.dataset.panel; if (this.data.panels.some(item => item.key === panel)) this.setData({ panel }) },
  login() { wx.navigateTo({ url: '/pages/login/login?returnTo=member&next=center' }) },
  openPoints() { wx.navigateTo({ url: '/pages/member/points' }) },
  openConsumption() { wx.navigateTo({ url: '/pages/member/consumption' }) },
})
