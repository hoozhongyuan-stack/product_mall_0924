const api = require('./api')

const RECOVERY_KEY = 'mall.checkoutRecovery.v1'

function loggedIn() { return Boolean(wx.getStorageSync && wx.getStorageSync('mall.memberToken')) }

function rememberCheckout(items) {
  const safe = (Array.isArray(items) ? items : []).slice(0, 50).filter((row) =>
    row && typeof row.skuId === 'string' && Number.isInteger(row.quantity) && row.quantity > 0)
    .map((row) => ({ skuId: row.skuId, quantity: row.quantity,
      ...(Number.isSafeInteger(row.seenPriceFen) && row.seenPriceFen >= 0 ?
        { seenPriceFen: row.seenPriceFen } : {}) }))
  wx.setStorageSync(RECOVERY_KEY, safe)
}

function recoverCheckout() {
  const rows = wx.getStorageSync(RECOVERY_KEY)
  wx.removeStorageSync(RECOVERY_KEY)
  return Array.isArray(rows) ? rows : []
}

function login() {
  return new Promise((resolve, reject) => wx.login({
    success(result) { result.code ? resolve(result.code) : reject(new Error('微信登录未取得授权码。')) },
    fail() { reject(new Error('微信登录失败，请稍后重试。')) },
  })).then((code) => api.post('/api/v1/app/auth/wechat', { code }))
    .then((result) => { wx.setStorageSync('mall.memberToken', result.accessToken); return result })
}

module.exports = { loggedIn, rememberCheckout, recoverCheckout, login }
