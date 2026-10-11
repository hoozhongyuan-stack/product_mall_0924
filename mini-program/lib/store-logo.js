// Only publish server-approved assets through the application's own asset route.
function url(store, baseUrl) {
  const path = store && store.logoUrl
  if (typeof path !== 'string' || !/^\/api\/v1\/app\/assets\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/file$/.test(path)) return ''
  return `${baseUrl.replace(/\/$/, '')}${path}`
}
module.exports = { url }
