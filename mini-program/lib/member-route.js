const { orderFilters } = require('./order-filters')

const PRIVATE_ROUTES = Object.freeze({
  points: '/pages/member/points',
  consumption: '/pages/member/consumption',
  coupons: '/pages/coupons/index',
  campaigns: '/pages/coupons/campaigns',
  addresses: '/pages/addresses/addresses',
  orders: '/pages/orders/list',
})

function memberReturnTarget(options = {}) {
  if (!Object.prototype.hasOwnProperty.call(PRIVATE_ROUTES, options.next)) return ''
  const route = PRIVATE_ROUTES[options.next]
  if (options.next !== 'orders') return route
  const filters = orderFilters(options)
  const query = Object.entries(filters).filter(([, value]) => value)
    .map(([key, value]) => `${key}=${value}`).join('&')
  return `${route}${query ? `?${query}` : ''}`
}

module.exports = { memberReturnTarget }
