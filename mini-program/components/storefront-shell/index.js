const api = require('../../lib/api')
const { NAVIGATION, presentStorefront } = require('../../lib/storefront')

function currentKey() {
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
  const route = pages.length ? `/${pages[pages.length - 1].route}` : ''
  return NAVIGATION.find((item) => item.path === route)?.key || ''
}

Component({
  properties: {
    showNavigation: { type: Boolean, value: false },
    showService: { type: Boolean, value: false },
    productDetail: { type: Boolean, value: false },
  },
  data: {
    items: presentStorefront(null, '').navigation,
    service: null,
    activeKey: '',
    qrOpen: false,
    qrFailed: false,
    serviceIconFailed: false,
    failedNavIcons: {},
  },
  lifetimes: {
    attached() { return this.loadStorefront() },
    detached() { this.requestToken = (this.requestToken || 0) + 1 },
  },
  pageLifetimes: {
    show() { return this.loadStorefront() },
  },
  methods: {
    async loadStorefront() {
      const token = this.requestToken = (this.requestToken || 0) + 1
      this.setData({ activeKey: currentKey() })
      try {
        const result = presentStorefront(await api.get('/api/v1/app/storefront'), api.baseUrl())
        if (token !== this.requestToken) return
        this.setData({ items: result.navigation, service: result.service,
          qrOpen: false, qrFailed: false, serviceIconFailed: false, failedNavIcons: {} })
      } catch (_) {
        if (token !== this.requestToken) return
        const fallback = presentStorefront(null, '')
        this.setData({ items: fallback.navigation, service: null, qrOpen: false })
      }
    },
    changeTab(event) {
      const key = event.currentTarget.dataset.key
      const item = NAVIGATION.find((entry) => entry.key === key)
      if (!item || key === this.data.activeKey) return
      wx.reLaunch({ url: item.path })
    },
    openService() {
      const service = this.data.service
      if (!service) return
      if (service.mode === 'PHONE') {
        wx.makePhoneCall({ phoneNumber: service.phone,
          fail() { wx.showToast({ title: '拨号未完成，可稍后重试', icon: 'none' }) } })
      } else if (service.mode === 'QR') {
        this.setData({ qrOpen: true, qrFailed: false })
      }
    },
    closeQr() { this.setData({ qrOpen: false }) },
    onQrError() { this.setData({ qrFailed: true }) },
    onServiceIconError() { this.setData({ serviceIconFailed: true }) },
    onNavIconError(event) {
      const key = String(event.currentTarget.dataset.key || '')
      this.setData({ failedNavIcons: { ...this.data.failedNavIcons, [key]: true } })
    },
  },
})
