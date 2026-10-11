const api = require('../../lib/api')
const locations = require('../../lib/address-location')
const stores = require('../../lib/stores')
const empty = { recipientName: '', phone: '', province: '', city: '', district: '', detail: '',
  poiName: '', locationAddress: '', doorDetail: '', latitude: null, longitude: null, isDefault: false }
Page({
  data: { form: empty, state: 'ready', error: '', busy: false, revision: null, fieldErrors: {},
    delivery: false, legacyDetail: '', eligibility: null },
  onLoad(options = {}) {
    this.memberToken = wx.getStorageSync('mall.memberToken'); this.alive = true; this.addressId = options.id || ''; this.storeId = options.storeId || ''
    this.selecting = options.select === '1'
    this.setData({ delivery: options.deliveryMode === 'DELIVERY', selecting: this.selecting })
    if (this.addressId) return this.load()
  },
  onShow() {
    this.alive = true
    if (!this.sameMember()) { this.setData({ form: { ...empty }, state: 'error', error: '登录身份已变化，请返回并重新打开地址。', busy: false }); return }
    if (this.savedWhileHidden) { const result = this.savedWhileHidden; this.savedWhileHidden = null; return this.finishSave(result.saved, result.point) }
    if (this.data.state === 'loading' && !this.loadPending) return this.load()
  },
  onHide() { this.alive = false; this.loadToken = (this.loadToken || 0) + 1; this.loadPending = false; this.locationToken = (this.locationToken || 0) + 1 },
  sameMember() { return this.memberToken === wx.getStorageSync('mall.memberToken') },
  onUnload() { this.destroyed = true; this.alive = false; this.locationToken = (this.locationToken || 0) + 1 },
  async load() {
    const token = this.loadToken = (this.loadToken || 0) + 1
    this.loadPending = true
    this.setData({ state: 'loading', error: '' })
    try {
      const rows = await api.get('/api/v1/app/addresses')
      if (!this.alive || !this.sameMember() || token !== this.loadToken) return
      const item = rows.find(row => row.id === this.addressId)
      if (!item) throw new Error('地址已不存在。')
      const form = Object.fromEntries(Object.keys(empty).map(key => [key, item[key] ?? empty[key]]))
      this.setData({ form, legacyDetail: !item.doorDetail ? item.detail : '', revision: item.revision, state: 'ready' })
      if (locations.normalize(form)) await this.checkEligibility(form)
    } catch (error) { if (this.alive && this.sameMember() && token === this.loadToken) this.setData({ state: 'error', error: error.message }) }
    finally { if (token === this.loadToken) this.loadPending = false }
  },
  change(event) {
    const key = event.currentTarget.dataset.key
    if (key === 'detail') this.locationToken = (this.locationToken || 0) + 1
    if (!['recipientName', 'phone', 'detail', 'doorDetail'].includes(key)) return
    this.setData({ form: { ...this.data.form, [key]: event.detail.value,
      ...(key === 'detail' ? { latitude: null, longitude: null, poiName: '', locationAddress: '', doorDetail: '' } : {}) },
    fieldErrors: { ...this.data.fieldErrors, [key]: '' } })
  },
  changeRegion(event) {
    const values = event.detail.value
    if (!Array.isArray(values) || values.length !== 3 || values.some(v => typeof v !== 'string' || !v.trim())) return
    const [province, city, district] = values
    this.locationToken = (this.locationToken || 0) + 1
    this.setData({ form: { ...this.data.form, province, city, district, latitude: null, longitude: null, poiName: '', locationAddress: '', doorDetail: '' },
      eligibility: null, fieldErrors: { ...this.data.fieldErrors, province: '', city: '', district: '' } })
  },
  chooseLocation() {
    if (this.data.busy) return
    wx.navigateTo({ url: `/pages/addresses/location?city=${encodeURIComponent(this.data.form.city)}&storeId=${encodeURIComponent(this.storeId)}`,
      events: { locationSelected: value => { if (!this.destroyed && this.sameMember()) { this.alive = true; this.acceptLocation(value) } } } })
  },
  acceptLocation(value) {
    const point = locations.normalize(value)
    if (!point) { this.setData({ error: '位置资料不完整，请重新选择具体小区或大厦。' }); return }
    this.setData({ form: { ...this.data.form, ...point }, error: '', eligibility: null,
      fieldErrors: { ...this.data.fieldErrors, poiName: '', province: '', city: '', district: '' } })
    return this.checkEligibility(point)
  },
  async checkEligibility(point) {
    const token = this.locationToken = (this.locationToken || 0) + 1
    if (!this.data.delivery || !this.storeId) return
    try {
      const eligibility = await locations.eligibility(this.storeId, point)
      if (this.alive && this.sameMember() && token === this.locationToken) this.setData({ eligibility })
    } catch (_) { if (this.alive && this.sameMember() && token === this.locationToken) this.setData({ eligibility: { code: 'UNKNOWN', message: '暂时无法核对配送范围，保存后可重新核对。' } }) }
  },
  toggleDefault(event) { this.setData({ form: { ...this.data.form, isDefault: event.detail.value } }) },
  async finishSave(saved, point) {
    let use = false
    if (this.selecting && this.data.delivery && this.storeId && saved && saved.id) {
      try {
        const eligibility = await locations.eligibility(this.storeId, point)
        const store = stores.selected()
        use = eligibility.code === 'ELIGIBLE' && store && store.id === this.storeId
      } catch (_) { use = false }
    }
    if (this.destroyed || !this.sameMember()) return
    if (!this.alive) { this.savedWhileHidden = { saved, point }; return }
    if (use) wx.setStorageSync('mall.selectedAddressId', saved.id)
    else if (this.selecting && this.data.delivery && wx.showToast) wx.showToast({ title: '地址已保存，请核对配送资格', icon: 'none' })
    wx.navigateBack({ delta: use ? 2 : 1 })
  },
  async save() {
    if (this.data.busy || !this.sameMember()) return
    const form = this.data.form
    const labels = { recipientName: '收货人姓名', phone: '手机号码', province: '省份', city: '城市', district: '区县', ...(this.data.delivery ? { doorDetail: '楼栋、单元或门牌' } : { detail: '详细地址' }) }
    const fieldErrors = Object.fromEntries(Object.entries(labels).filter(([key]) => !String(form[key]).trim()).map(([key, label]) => [key, `请填写${label}。`]))
    if (this.data.delivery && !locations.normalize(form)) fieldErrors.poiName = '请选择具体收货位置，地区会自动带出。'
    if (form.phone && !/^1\d{10}$/.test(form.phone)) fieldErrors.phone = '请填写11位手机号码。'
    if (Object.keys(fieldErrors).length) { this.setData({ error: '请补全收货信息。', fieldErrors }); return }
    const structured = locations.normalize(form) && form.doorDetail.trim()
    const { poiName, locationAddress, doorDetail, ...legacy } = form
    const payload = structured ? { ...form, detail: `${form.locationAddress} ${form.poiName} ${form.doorDetail.trim()}` } : legacy
    this.setData({ busy: true, error: '', fieldErrors: {} })
    try {
      const saved = this.addressId ? await api.put(`/api/v1/app/addresses/${this.addressId}`, { ...payload, expectedRevision: this.data.revision }) : await api.post('/api/v1/app/addresses', payload)
      if (!this.destroyed && this.sameMember()) {
        if (this.alive) await this.finishSave(saved, form)
        else this.savedWhileHidden = { saved, point: form }
      }
    } catch (error) { if (!this.destroyed && this.sameMember()) this.setData({ error: error.statusCode === 409 ? '地址已被修改。当前填写内容已保留，请核对后返回地址列表重新打开。' : error.message }) }
    finally { if (!this.destroyed && this.sameMember()) this.setData({ busy: false }) }
  },
})
