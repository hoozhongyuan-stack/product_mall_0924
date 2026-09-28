function baseUrl() {
  const configured = getApp().globalData.apiBaseUrl
  if (!configured || !/^https?:\/\/[^/]+$/.test(configured)) {
    throw new Error('请先配置小程序接口地址。')
  }
  return configured.replace(/\/$/, '')
}

function request(method, path, body, query = {}, headers = {}) {
  const base = baseUrl()
  const params = Object.entries(query)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
  const url = `${base}${path}${params.length ? `?${params.join('&')}` : ''}`
  return new Promise((resolve, reject) => {
    const token = wx.getStorageSync ? wx.getStorageSync('mall.memberToken') : ''
    wx.request({
      url, method, timeout: 12000,
      header: { ...headers, ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      ...(body ? { data: body } : {}),
      success(result) {
        const payload = result.data
        if (result.statusCode >= 200 && result.statusCode < 300 && payload && payload.success === true) {
          resolve(payload.data)
          return
        }
        const message = result.statusCode === 404 ? '内容已下架或暂不可用。' :
          payload && payload.error && payload.error.message ? payload.error.message : '加载失败，请稍后重试。'
        const failure = new Error(message)
        failure.statusCode = result.statusCode
        failure.code = payload && payload.error && payload.error.code
        if (failure.statusCode === 401 && token && wx.removeStorageSync && token === wx.getStorageSync('mall.memberToken')) {
          wx.removeStorageSync('mall.memberToken')
          wx.removeStorageSync('mall.wechatPaymentIntent.v1')
        }
        reject(failure)
      },
      fail() { reject(new Error('网络连接失败，请检查网络后重试。')) },
    })
  })
}

function get(path, query = {}) { return request('GET', path, null, query) }
function post(path, body, headers = {}) { return request('POST', path, body, {}, headers) }
function put(path, body) { return request('PUT', path, body) }
function remove(path, body) { return request('DELETE', path, body) }

module.exports = { baseUrl, get, post, put, remove }
