export interface Account {
  accountId: string
  loginName: string
  displayName: string
  kind: 'OWNER' | 'STAFF'
  enabled: boolean
  revision: number
  groupIds: string[]
  permissionCodes: string[]
}

export interface PermissionGroup {
  groupId: string
  code: string
  name: string
  enabled: boolean
  revision: number
  permissionCodes: string[]
}

export interface AuditEntry {
  id: string
  actorId: string | null
  actionCode: string
  objectType: string
  objectId: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  result: string
  requestId: string
  occurredAt: string
}

export interface Confirmation {
  confirmationToken: string
  expiresInSeconds: number
}

interface ApiEnvelope<T> {
  success: boolean
  data?: T
  error?: { code: string; message: string }
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code: string) {
    super(message)
    this.name = 'ApiError'
  }
}

function csrfCookie(): string {
  const part = document.cookie.split('; ').find((item) => item.startsWith('csrftoken='))
  return part ? decodeURIComponent(part.split('=')[1] || '') : ''
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method?.toUpperCase() || 'GET'
  if (method !== 'GET' && !csrfCookie()) {
    await fetch('/api/v1/admin/auth/csrf', { credentials: 'same-origin' })
  }
  const headers = new Headers(init.headers)
  if (method !== 'GET') {
    if (init.body instanceof FormData) headers.delete('Content-Type')
    else headers.set('Content-Type', 'application/json')
    headers.set('X-CSRFToken', csrfCookie())
  }
  const result = await fetch(`/api/v1/admin${path}`, {
    ...init,
    headers,
    credentials: 'same-origin',
  })
  let payload: ApiEnvelope<T>
  try {
    payload = (await result.json()) as ApiEnvelope<T>
  } catch {
    throw new Error('服务暂时不可用，请刷新页面后重试。')
  }
  if (result.status === 401 && path !== '/auth/login') {
    window.dispatchEvent(new Event('admin-session-expired'))
  }
  if (!result.ok || !payload.success || payload.data === undefined) {
    throw new ApiError(payload.error?.message || '请求失败，请稍后重试。', result.status, payload.error?.code || 'UNKNOWN')
  }
  return payload.data
}

export async function confirmedWrite<T>(
  action: string,
  currentPassword: string,
  path: string,
  method: 'POST' | 'PATCH' | 'PUT',
  body: Record<string, unknown>,
  objectId = '',
  revision = 0,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const { confirmationToken } = await api<Confirmation>('/auth/confirm', {
    method: 'POST',
    body: JSON.stringify({ action, password: currentPassword, objectId, revision }),
  })
  return api<T>(path, {
    method,
    headers: { ...extraHeaders, 'X-Action-Confirmation': confirmationToken },
    body: JSON.stringify(body),
  })
}
