const { avatarDisplayUrl } = require('./member')
const api = require('./api')
function token() { return wx.getStorageSync('mall.memberToken') || '' }
const profileControls = {
  editProfile() { if (this.data.member) this.setData({ editingProfile: true, nicknameDraft: this.data.member.nickname || '', profileError: '' }) },
  cancelProfile() { if (!this.data.profileBusy) this.setData({ editingProfile: false, nicknameDraft: '', profileError: '' }) },
  inputNickname(event) { this.setData({ nicknameDraft: event.detail.value }) },
  async saveProfile(event) {
    if (event && event.detail && event.detail.value) this.setData({ nicknameDraft: event.detail.value.nickname || '' })
    const nickname = this.data.nicknameDraft.trim()
    if (!nickname || Array.from(nickname).length > 40) { this.setData({ profileError: '昵称需为 1 至 40 个字符。' }); return }
    return this.updateProfile(() => api.put('/api/v1/app/member/profile', { nickname, expectedRevision: this.data.member.profileRevision }))
  },
  chooseAvatar(event) {
    const file = event.detail && event.detail.avatarUrl
    if (!file) return
    return this.updateProfile(() => api.upload('/api/v1/app/member/avatar', file, { expectedRevision: String(this.data.member.profileRevision) }), true)
  },
  async updateProfile(operation, keepEditor = false) {
    if (!this.data.member || this.data.profileBusy) return
    const generation = this.generation, memberToken = token()
    this.setData({ profileBusy: true, profileError: '' })
    try {
      const profile = await operation()
      if (!this.valid(generation, memberToken)) return
      this.setData({ member: { ...this.data.member, ...profile, avatarDisplayUrl: avatarDisplayUrl(profile.avatarUrl) }, editingProfile: keepEditor && this.data.editingProfile, nicknameDraft: keepEditor ? this.data.nicknameDraft : '', profileBusy: false })
    } catch (error) {
      if (!this.valid(generation, memberToken)) return
      if (error.statusCode === 401) { this.invalidate(); this.setData({ state: 'auth', error: '' }); return }
      this.setData({ profileBusy: false, profileError: error.statusCode === 409 ? '资料已更新，请重新加载后再修改。' : error.message })
    }
  },
}
module.exports = { profileControls }
