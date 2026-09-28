const INTENT_KEY = 'mall.wechatPaymentIntent.v1'

// Store retry identifiers only. Never store signed payment parameters or payer data.
function load(orderId) {
  const saved = wx.getStorageSync(INTENT_KEY)
  const value = saved && saved[orderId]
  if (!value || value.orderId !== orderId || !/^[a-f0-9-]{36}$/.test(value.key) ||
      !['PREPAY', 'PAYMENT_UNKNOWN'].includes(value.phase)) return null
  return value
}
function save(value) {
  const saved = wx.getStorageSync(INTENT_KEY)
  wx.setStorageSync(INTENT_KEY, { ...(saved && typeof saved === 'object' ? saved : {}), [value.orderId]: value })
}
function clear(orderId) {
  const saved = wx.getStorageSync(INTENT_KEY)
  const remaining = saved && typeof saved === 'object' ? Object.fromEntries(Object.entries(saved).filter(([id]) => id !== orderId)) : {}
  if (Object.keys(remaining).length) wx.setStorageSync(INTENT_KEY, remaining)
  else wx.removeStorageSync(INTENT_KEY)
}
function invoke(result) {
  const value = result && result.paymentParameters
  if (!result || typeof result.attemptId !== 'string' || !value || value.signType !== 'RSA' ||
      typeof value.timeStamp !== 'string' || !/^\d{1,16}$/.test(value.timeStamp) || !/^prepay_id=\S+$/.test(value.package) ||
      typeof value.nonceStr !== 'string' || !value.nonceStr || typeof value.paySign !== 'string' || !value.paySign) {
    return Promise.reject(new Error('支付参数不完整，请重新获取服务端预支付结果。'))
  }
  if (typeof wx.requestPayment !== 'function') return Promise.reject(new Error('当前微信环境不支持支付，请更新微信后重试。'))
  return new Promise((resolve) => wx.requestPayment({ timeStamp: value.timeStamp, nonceStr: value.nonceStr,
    package: value.package, signType: value.signType, paySign: value.paySign,
    success: () => resolve('SUCCESS'),
    fail: (error) => resolve(error && /cancel/.test(error.errMsg || '') ? 'CANCELLED' : 'UNKNOWN'),
  }))
}
module.exports = { load, save, clear, invoke }
