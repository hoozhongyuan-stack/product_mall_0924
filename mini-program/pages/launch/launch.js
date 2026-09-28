const api = require('../../lib/api')
const startup = require('../../lib/startup')

const DURATION_MS = 3000

function assetUrl(path) {
  if (typeof path !== 'string' || !/^\/api\/v1\/app\/assets\/[^/?#]+\/file$/.test(path)) return ''
  return `${api.baseUrl()}${path}`
}

Page({
  data: {
    mediaUrl: '', fallbackUrl: '', mediaState: 'brand',
    remainingSeconds: 3, progressPercent: 100, versionId: '',
  },

  onLoad() {
    this.departing = false
    this.ready = false
    this.visible = false
    this.remainingMs = DURATION_MS
    return this.loadConfig()
  },

  async loadConfig() {
    try {
      const config = await api.get('/api/v1/app/startup')
      if (this.departing) return
      const gifUrl = assetUrl(config.gifUrl)
      const fallbackUrl = assetUrl(config.fallbackUrl)
      this.setData({ versionId: config.versionId || '', fallbackUrl,
        mediaUrl: gifUrl || fallbackUrl,
        mediaState: gifUrl ? 'gif' : fallbackUrl ? 'fallback' : 'brand' })
    } catch (_) {
      if (!this.departing) this.setData({ mediaUrl: '', fallbackUrl: '', mediaState: 'brand' })
    }
  },

  onReady() {
    this.ready = true
    if (this.visible) this.resumeTimer()
  },

  onShow() {
    this.visible = true
    if (this.ready) this.resumeTimer()
  },

  onHide() {
    this.visible = false
    this.pauseTimer()
  },

  onUnload() {
    this.departing = true
    this.clearTimer()
  },

  onGifError() {
    if (this.data.mediaState === 'gif' && this.data.fallbackUrl) {
      this.setData({ mediaUrl: this.data.fallbackUrl, mediaState: 'fallback' })
    } else {
      this.setData({ mediaUrl: '', mediaState: 'brand' })
    }
  },

  resumeTimer() {
    if (this.departing || this.timer || !this.ready || !this.visible) return
    this.startedAt = Date.now()
    this.timer = setTimeout(() => this.finish(), this.remainingMs)
    this.tickTimer = setInterval(() => {
      const remaining = Math.max(0, this.remainingMs - (Date.now() - this.startedAt))
      this.setData({ remainingSeconds: Math.max(1, Math.ceil(remaining / 1000)),
        progressPercent: Math.round(remaining / DURATION_MS * 100) })
    }, 100)
  },

  pauseTimer() {
    if (!this.timer) return
    this.remainingMs = Math.max(0, this.remainingMs - (Date.now() - this.startedAt))
    this.clearTimer()
    this.setData({ remainingSeconds: Math.max(1, Math.ceil(this.remainingMs / 1000)),
      progressPercent: Math.round(this.remainingMs / DURATION_MS * 100) })
  },

  clearTimer() {
    if (this.timer) clearTimeout(this.timer)
    if (this.tickTimer) clearInterval(this.tickTimer)
    this.timer = undefined
    this.tickTimer = undefined
  },

  skip() { this.finish() },

  finish() {
    if (this.departing) return
    this.departing = true
    this.clearTimer()
    const target = startup.completeLaunch()
    wx.reLaunch({ url: target, fail() { wx.reLaunch({ url: '/pages/home/home' }) } })
  },
})
