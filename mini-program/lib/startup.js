const LAUNCH_URL = '/pages/launch/launch'
const HOME_URL = '/pages/home/home'
const ENTRY_ROUTES = new Set([
  'pages/home/home', 'pages/index/index',
  'pages/product/detail', 'pages/micro/detail',
])

let context = null

function targetFromOptions(options = {}) {
  const path = typeof options.path === 'string' ? options.path.replace(/^\//, '') : ''
  if (!ENTRY_ROUTES.has(path)) return HOME_URL
  const query = options.query && typeof options.query === 'object' ? options.query : {}
  const params = Object.entries(query)
    .filter(([key, value]) => /^[A-Za-z][A-Za-z0-9_]{0,49}$/.test(key) &&
      (typeof value === 'string' || typeof value === 'number') && String(value).length <= 1000)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
  return `/${path}${params.length ? `?${params.join('&')}` : ''}`
}

function captureLaunch(options) {
  context = { target: targetFromOptions(options), pending: true, entering: false }
}

function targetUrl() { return context ? context.target : HOME_URL }

function redirectIfPending(onFailure) {
  if (!context || !context.pending) return false
  if (context.entering) return true
  const entry = context
  entry.entering = true
  const recover = () => {
    if (context !== entry || !entry.pending) return
    context = { ...entry, pending: false, entering: false }
    if (typeof onFailure === 'function') onFailure()
  }
  try {
    wx.redirectTo({ url: LAUNCH_URL, fail: recover })
  } catch (_) {
    recover()
  }
  return true
}

function completeLaunch() {
  const target = targetUrl()
  if (context) context = { ...context, pending: false, entering: false }
  return target
}

function resetForTests() { context = null }

module.exports = { captureLaunch, completeLaunch, redirectIfPending,
  resetForTests, targetUrl, targetFromOptions }
