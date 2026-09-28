const api = require('../../lib/api')

const empty = { recipientName: '', phone: '', province: '', city: '', district: '',
  detail: '', isDefault: false }

Page({
  data: { form: empty, state: 'ready', error: '', busy: false, revision: null },
  onLoad(options) { this.addressId = options.id || ''; if (this.addressId) return this.load() },
  async load() {
    this.setData({ state: 'loading', error: '' })
    try {
      const rows = await api.get('/api/v1/app/addresses')
      const item = rows.find((row) => row.id === this.addressId)
      if (!item) throw new Error('地址已不存在。')
      this.setData({ form: { recipientName: item.recipientName, phone: item.phone,
        province: item.province, city: item.city, district: item.district,
        detail: item.detail, isDefault: item.isDefault }, revision: item.revision, state: 'ready' })
    } catch (error) { this.setData({ state: 'error', error: error.message }) }
  },
  change(event) {
    const key = event.currentTarget.dataset.key
    this.setData({ form: { ...this.data.form, [key]: event.detail.value } })
  },
  toggleDefault(event) { this.setData({ form: { ...this.data.form, isDefault: event.detail.value } }) },
  async save() {
    if (this.data.busy) return
    const form = this.data.form
    if (Object.keys(empty).some((key) => key !== 'isDefault' && !String(form[key]).trim())) {
      this.setData({ error: '请填写完整的收货信息。' }); return
    }
    this.setData({ busy: true, error: '' })
    try {
      if (this.addressId) await api.put(`/api/v1/app/addresses/${this.addressId}`,
        { ...form, expectedRevision: this.data.revision })
      else await api.post('/api/v1/app/addresses', form)
      wx.navigateBack({ delta: 1 })
    } catch (error) { this.setData({ error: error.statusCode === 409 ?
      '地址已被修改。当前填写内容已保留，请核对后返回地址列表重新打开。' : error.message }) }
    finally { this.setData({ busy: false }) }
  },
})
