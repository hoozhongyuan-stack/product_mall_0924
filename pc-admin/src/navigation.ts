import type { RouteMeta, Router } from 'vue-router'

// Presentation groups only. Access is always read from the existing router metadata.
export const adminSections = [
  { name: '工作台', paths: ['/', '/business/summary'] },
  { name: '商品', paths: ['/catalog'] },
  { name: '库存', paths: ['/inventory', '/inventory/warehouses', '/inventory/inbounds', '/inventory/outbounds', '/inventory/stocktakes', '/inventory/ledgers'] },
  { name: '订单', paths: ['/orders', '/redemptions', '/aftersales', '/fulfillment/settings', '/store/payments'] },
  { name: '前置仓', paths: ['/stores', '/stores/accounts', '/stores/map-settings'] },
  { name: '会员', paths: ['/members', '/members/rules'] },
  { name: '营销', paths: ['/coupons', '/exchange-offers'] },
  { name: '店铺', paths: ['/store/info', '/pages/home', '/pages/micro', '/store/navigation', '/assets', '/store/customer-service'] },
  { name: '小程序', paths: ['/store/wechat-integration', '/subscription-messages', '/subscription-message-tasks', '/store/code-versions'] },
  { name: '系统管理', paths: ['/accounts', '/permission-groups', '/audit-logs'] },
]

export function canAccessRoute(meta: RouteMeta, permissions: readonly string[]) {
  const any = meta.permissionsAny as string[] | undefined
  if (any?.length) return any.some(code => permissions.includes(code))
  return !meta.permission || permissions.includes(String(meta.permission))
}

export function authorizedSections(router: Router, permissions: readonly string[]) {
  return adminSections.map(section => ({
    name: section.name,
    hasSubnavigation: section.paths.length > 1,
    links: section.paths.map(path => ({ path, meta: router.resolve(path).meta }))
      .filter(link => canAccessRoute(link.meta, permissions))
      .map(link => ({ path: link.path, title: String(link.meta.title) })),
  })).filter(section => section.links.length)
}
