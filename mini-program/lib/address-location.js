const api = require('./api')
function normalize(value) {
  if (!value || typeof value !== 'object') return null
  const keys = ['poiName', 'locationAddress', 'province', 'city', 'district']
  if (keys.some(key => typeof value[key] !== 'string' || !value[key].trim())) return null
  if (value.latitude === null || value.longitude === null || value.latitude === '' || value.longitude === '') return null
  const latitude = Number(value.latitude), longitude = Number(value.longitude)
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null
  return { ...Object.fromEntries(keys.map(key => [key, value[key].trim()])), latitude, longitude }
}
function search(q, city) { return api.get('/api/v1/app/address-locations/search', { q, city }) }
function reverse(latitude, longitude) { return api.get('/api/v1/app/address-locations/reverse', { latitude, longitude }) }
function eligibility(storeId, point) { return api.get('/api/v1/app/address-locations/eligibility', { storeId, latitude: point.latitude, longitude: point.longitude }) }
module.exports = { normalize, search, reverse, eligibility }
