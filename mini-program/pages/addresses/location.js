const locations = require('../../lib/address-location')
Page({
  data: { city: '', query: '', items: [], candidate: null, state: 'ready', error: '', busy: false, eligibility: null },
  onLoad(options = {}) { this.memberToken = wx.getStorageSync('mall.memberToken'); this.alive = true; this.storeId = options.storeId || ''; this.token = 0; this.setData({ city: options.city || '' }) },
  onShow() { this.alive = true; if (!this.sameMember()) this.setData({ items: [], candidate: null, busy: false, error: '登录身份已变化，请返回并重新选择地址。' }) },
  sameMember() { return this.memberToken === wx.getStorageSync('mall.memberToken') },
  onHide() { this.alive = false; this.token++; this.setData({ busy: false }) },
  onUnload() { this.destroyed = true; this.alive = false; this.token++ },
  changeQuery(event) { this.invalidate({ query: event.detail.value }) },
  changeCity(event) { this.invalidate({ city: event.detail.value, items: [] }) },
  invalidate(patch) { this.token++; this.setData({ ...patch, candidate: null, eligibility: null, error: '', busy: false, state: 'ready' }) },
  async search() {
    const q = this.data.query.trim(), city = this.data.city.trim()
    if (!q || !city) { this.setData({ error: '请填写收货城市和小区、大厦或街道名称。' }); return }
    const token = ++this.token
    this.setData({ busy: true, state: 'loading', error: '', candidate: null, eligibility: null })
    try {
      const result = await locations.search(q, city)
      if (!this.alive || !this.sameMember() || token !== this.token) return
      const items = (result.items || []).map(locations.normalize).filter(Boolean)
      this.setData({ items, state: items.length ? 'ready' : 'empty' })
    } catch (error) { if (this.alive && this.sameMember() && token === this.token) this.setData({ error: error.message, state: 'error', items: [] }) }
    finally { if (this.alive && this.sameMember() && token === this.token) this.setData({ busy: false }) }
  },
  select(event) {
    const index = Number(event.currentTarget.dataset.index), candidate = this.data.items[index]
    if (!Number.isInteger(index) || !candidate) return
    return this.setCandidate(candidate)
  },
  async setCandidate(value) {
    const candidate = locations.normalize(value)
    if (!candidate) { this.setData({ error: '该位置缺少地区信息，请搜索完整的小区或大厦名称。' }); return }
    const token = ++this.token
    this.setData({ candidate, eligibility: null, error: '', busy: false })
    if (!this.storeId) return
    try {
      const eligibility = await locations.eligibility(this.storeId, candidate)
      if (this.alive && this.sameMember() && token === this.token) this.setData({ eligibility })
    } catch (_) { if (this.alive && this.sameMember() && token === this.token) this.setData({ eligibility: { code: 'UNKNOWN', message: '暂时无法核对配送范围，请稍后在地址列表重试。' } }) }
  },
  nativeLocation() {
    if (this.data.busy) return
    this.nativePending = true
    wx.chooseLocation({ success: value => { if (this.destroyed || !this.sameMember() || !this.nativePending) return; this.nativePending = false; this.alive = true; this.resolveNative(value, true) },
      fail: () => { if (this.destroyed || !this.sameMember() || !this.nativePending) return; this.nativePending = false; this.alive = true; this.setData({ error: '未选定地图位置。可直接填写城市并搜索小区，无需开启定位权限。' }) } })
  },
  currentLocation() {
    if (this.data.busy) return
    const token = ++this.token
    this.setData({ busy: true, error: '' })
    wx.getLocation({ type: 'gcj02', success: value => { if (this.alive && this.sameMember() && token === this.token) this.resolveNative(value, false) },
      fail: () => { if (this.alive && this.sameMember() && token === this.token) this.setData({ busy: false, error: '定位未开启。仍可填写城市、搜索小区并手动选择收货位置。' }) } })
  },
  async resolveNative(value, selected) {
    const token = ++this.token
    this.setData({ busy: true, error: '' })
    try {
      const result = await locations.reverse(value.latitude, value.longitude)
      if (!this.alive || !this.sameMember() || token !== this.token) return
      const point = locations.normalize({ ...result, ...(selected && value.name ? { poiName: value.name } : {}),
        ...(selected && value.address ? { locationAddress: value.address } : {}) })
      if (!point) throw new Error('未获取完整地区，请直接搜索具体收货位置。')
      this.setData({ city: point.city, items: [point], state: 'ready', busy: false })
      if (selected) await this.setCandidate(point)
      else this.setData({ candidate: null, eligibility: null, error: '已找到当前位置。请核对下方地点后选中，再确认收货位置。' })
    } catch (error) { if (this.alive && this.sameMember() && token === this.token) this.setData({ error: error.message, busy: false }) }
  },
  confirm() {
    if (!this.alive || !this.sameMember() || this.data.busy || !locations.normalize(this.data.candidate)) return
    this.getOpenerEventChannel().emit('locationSelected', this.data.candidate)
    wx.navigateBack({ delta: 1 })
  },
})
