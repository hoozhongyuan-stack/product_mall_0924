export const EVENT_OPTIONS = [
  ['ORDER_PAID', '订单支付成功'],
  ['ORDER_SHIPPED', '订单首次发货'],
  ['REFUND_SUCCEEDED', '退款成功'],
]

export const STATUS_OPTIONS = [
  ['BLOCKED', '已阻断'], ['READY', '待处理'], ['RESERVED', '已预留'],
  ['CLAIMED', '发送处理中'], ['RETRY_WAIT', '待重试'], ['UNKNOWN', '待核验'],
  ['FAILED', '处理失败'], ['SIMULATED', '仅模拟完成'],
]

const REASONS = {
  NO_TEMPLATE: '事件发生时没有可用模板',
  TEMPLATE_DISABLED: '事件发生时模板未启用',
  TEMPLATE_MISMATCH: '授权与模板不匹配',
  TEMPLATE_UNAVAILABLE: '发送前模板已不可用',
  NO_AUTHORIZATION: '事件发生时没有有效授权',
  AUTHORIZATION_REJECTED: '用户拒绝授权',
  AUTHORIZATION_REVOKED: '授权已撤销',
  AUTHORIZATION_UNVERIFIED: '授权证据未核验',
  AUTHORIZATION_UNAVAILABLE: '发送前授权已不可用',
  AUTHORIZATION_EXPIRED: '授权已过期',
  AUTHORIZATION_MISMATCH: '发送前授权不匹配',
  MEMBER_DISABLED: '会员账号已停用',
  MEMBER_IDENTITY_MISSING: '缺少小程序身份',
  MEMBER_IDENTITY_CHANGED: '小程序身份已变化',
  LEASE_EXPIRED: '发送租约过期，结果待核验',
  RETRYABLE_FAILURE: '临时失败，等待有限重试',
  PERMANENT_FAILURE: '发送失败，不再自动重试',
  RETRY_EXHAUSTED: '已达到重试上限',
  SENDER_EXCEPTION: '发送结果不确定，需核验',
  UNKNOWN_RESULT: '发送结果不确定，需核验',
  RATE_LIMIT: '通道限流',
}

export const eventLabel = value => EVENT_OPTIONS.find(([code]) => code === value)?.[1] || value
export const statusLabel = value => STATUS_OPTIONS.find(([code]) => code === value)?.[1] || value
export const reasonLabel = value => REASONS[value] || (value || '—')

export function canRecover(task, hasPermission) {
  return !!hasPermission && task.status === 'RESERVED' && task.recoverable === true
}

export function taskQuery(filters, cursor = '') {
  const params = new URLSearchParams()
  for (const key of ['eventType', 'status', 'createdFrom', 'createdTo']) {
    if (filters[key]) params.set(key, filters[key])
  }
  params.set('limit', '20')
  if (cursor) params.set('cursor', cursor)
  return `?${params.toString()}`
}
