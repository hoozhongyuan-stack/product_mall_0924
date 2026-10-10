const api = require('./api')
const { couponPage } = require('./coupon-page')
const { presentCampaign, readPending } = require('./coupons')
const { safeLink } = require('./home')
const labels = { GUEST: '登录后领取', AVAILABLE: '领取优惠券', LIMIT_REACHED: '已达领取上限', SOLD_OUT: '已抢光', EXPIRED: '已过期', NOT_STARTED: '尚未开始', UNAVAILABLE: '暂不可领取' }
function identity() { return wx.getStorageSync ? wx.getStorageSync('mall.memberToken') || '' : '' }
function rows(value, components) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('优惠券资料不完整，请重新加载。')
  return Object.fromEntries(components.map(component => {
    const values = value[component.id]
    if (!Array.isArray(values) || values.length > component.limit) throw new Error('优惠券资料不完整，请重新加载。')
    return [component.id, values.map(row => {
      if (!row || !safeLink({ type: 'PAGE', targetId: row.id }) || !labels[row.claimState] || row.canClaim !== (row.claimState === 'AVAILABLE')) throw new Error('优惠券状态不完整，请重新加载。')
      return { ...presentCampaign(row), claimLabel: labels[row.claimState],
        ...(typeof row.scopeRestricted === 'boolean' ? { scopeLabel: row.scopeRestricted ? '指定商品适用' : '全场商品适用' } : {}) }
    })]
  }))
}
function discard(host) {
  const controller = host.couponController
  if (controller) { controller.disposed = true; controller.invalidate() }
  host.couponController = null
  host.setData({ couponData: {}, couponState: 'idle', couponError: '', couponRefreshPage: false, couponClaim: { busy: false, pending: null, message: '', claimError: '' } })
}
function create(host, pageId, versionId, components) {
  const definition = couponPage(true)
  const controller = { ...definition, data: structuredData(definition.data), generation: 0, disposed: false,
    setData(patch) {
      this.data = { ...this.data, ...patch }
      if (host.couponController !== this) return
      host.setData({ couponClaim: { busy: this.data.busy, pending: this.data.pending, message: this.data.message, claimError: this.data.claimError },
        ...(patch.rows && !patch.rows.length ? { couponData: {} } : {}),
        ...(patch.state === 'auth' ? { couponState: 'error', couponError: patch.error || '登录状态已改变，请重新加载页面。' } : {}) })
    },
    refresh() { return load(host, this) },
    pageId, versionId, components,
  }
  host.couponController = controller
  return controller
}
function structuredData(value) { return JSON.parse(JSON.stringify(value)) }
async function mount(host, pageId, versionId) {
  discard(host)
  const components = host.data.components.filter(item => item.type === 'COUPON_LIST')
  if (!components.length) return
  return load(host, create(host, pageId, versionId, components))
}
async function load(host, controller) {
  if (!controller || controller.disposed || controller.data.busy) return
  const generation = ++controller.generation, token = identity()
  controller.clearPrivate()
  controller.setData({ state: 'loading' })
  host.setData({ couponState: 'loading', couponError: '', couponData: {} })
  try {
    const data = await api.get(`/api/v1/app/pages/${controller.pageId}/coupons`, { versionId: controller.versionId })
    if (!controller.valid(generation, token) || host.couponController !== controller) return
    if (!data || data.pageId !== controller.pageId || data.versionId !== controller.versionId ||
        (token ? typeof data.memberId !== 'string' || !data.memberId : data.memberId !== null)) {
      const error = new Error('页面或会员资料已变化，请重新加载页面。')
      error.code = 'PAGE_COUPON_CONTEXT_INVALID'
      throw error
    }
    const mapped = rows(data.componentData, controller.components)
    if (!token && Object.values(mapped).flat().some(row => row.canClaim)) throw new Error('登录状态不一致，请重新加载。')
    controller.memberId = data.memberId
    controller.loadedToken = token
    controller.setData({ state: 'ready', rows: Object.values(mapped).flat(), pending: data.memberId ? readPending(data.memberId) : null })
    host.setData({ couponState: 'ready', couponData: mapped, couponRefreshPage: false })
  } catch (error) {
    if (!controller.valid(generation, token) || host.couponController !== controller) return
    controller.setData({ state: error.statusCode === 401 ? 'auth' : 'error' })
    host.setData({ couponState: 'error', couponRefreshPage: error.statusCode === 409 || error.code === 'PAGE_COUPON_CONTEXT_INVALID', couponError: error.statusCode === 409 ? '页面已更新，请重新加载页面。' : error.message, couponData: {} })
  }
}
function claim(host, event) {
  const controller = host.couponController, dataset = event && event.currentTarget && event.currentTarget.dataset || {}
  if (!controller || controller.disposed || host.data.couponState !== 'ready') return
  const values = host.data.couponData[dataset.component]
  const row = values && values.find(item => item.id === dataset.id)
  if (!row) return
  if (!identity()) { wx.navigateTo({ url: '/pages/login/login?returnTo=coupons' }); return }
  return controller.claim({ currentTarget: { dataset: { id: row.id } } })
}
function recover(host) { return host.couponController && host.couponController.retryClaim() }
function reload(host) { return host.couponController && host.couponController.refresh() }
module.exports = { mount, discard, claim, recover, reload }
