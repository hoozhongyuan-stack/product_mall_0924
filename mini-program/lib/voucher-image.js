// A voucher PNG must stay in the Mini Program's private user files, never in a public URL.
const KEY = 'mall.voucherQrFiles.v1'
const MAX_BASE64_LENGTH = 262144
let sequence = 0

function fileSystem() {
  if (!wx.env || !wx.env.USER_DATA_PATH || typeof wx.getFileSystemManager !== 'function') {
    throw new Error('当前设备暂不能显示二维码。')
  }
  return wx.getFileSystemManager()
}

function managedPath(path) {
  const root = wx.env && wx.env.USER_DATA_PATH
  return typeof root === 'string' && typeof path === 'string' &&
    path.startsWith(`${root}/mall-voucher-`) && /^mall-voucher-[A-Za-z0-9-]+\.png$/.test(path.slice(root.length + 1))
}

function tracked() {
  const value = wx.getStorageSync(KEY)
  return Array.isArray(value) ? value.filter(managedPath) : []
}

function unlink(path) {
  if (!managedPath(path) || typeof wx.getFileSystemManager !== 'function') return
  try { wx.getFileSystemManager().unlink({ filePath: path, success: () => {}, fail: () => {} }) } catch (_) { /* Text code remains available. */ }
}

function release(path) {
  if (!managedPath(path)) return
  const remaining = tracked().filter((item) => item !== path)
  if (remaining.length) wx.setStorageSync(KEY, remaining)
  else wx.removeStorageSync(KEY)
  unlink(path)
}

function cleanup() {
  const paths = tracked()
  wx.removeStorageSync(KEY)
  paths.forEach(unlink)
}

function write(dataUrl) {
  const match = typeof dataUrl === 'string' && /^data:image\/png;base64,(iVBORw0KGgo[A-Za-z0-9+/]*={0,2})$/.exec(dataUrl)
  if (!match || match[1].length > MAX_BASE64_LENGTH) return Promise.reject(new Error('二维码图片无效。'))
  let fs
  try { fs = fileSystem() } catch (error) { return Promise.reject(error) }
  const path = `${wx.env.USER_DATA_PATH}/mall-voucher-${Date.now()}-${++sequence}-${Math.random().toString(16).slice(2)}.png`
  const paths = tracked()
  if (paths.length >= 50) return Promise.reject(new Error('二维码图片暂不可用。'))
  try { wx.setStorageSync(KEY, [...paths, path]) } catch (_) { return Promise.reject(new Error('二维码图片暂不可用。')) }
  return new Promise((resolve, reject) => {
    try {
      fs.writeFile({ filePath: path, data: match[1], encoding: 'base64',
        success: () => resolve(path),
        fail: () => { release(path); reject(new Error('二维码图片暂不可用。')) } })
    } catch (_) { release(path); reject(new Error('二维码图片暂不可用。')) }
  })
}

module.exports = { write, release, cleanup }
