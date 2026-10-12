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

const READ_TIMEOUT_MS = 15_000

/** Bound only reads. An in-flight business write keeps its original recovery identity. */
async function boundedRead<T>(read: (signal: AbortSignal) => Promise<T>, original?: AbortSignal | null): Promise<T> {
  const controller = new AbortController()
  let timedOut = false
  const cancel = () => controller.abort(original?.reason)
  if (original?.aborted) cancel()
  else original?.addEventListener('abort', cancel, { once: true })
  const deadline = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, READ_TIMEOUT_MS)
  try {
    return await read(controller.signal)
  } catch (error) {
    if (timedOut) throw new ApiError('读取超时，请检查网络后重新读取。', 408, 'READ_TIMEOUT')
    throw error
  } finally {
    clearTimeout(deadline)
    original?.removeEventListener('abort', cancel)
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method?.toUpperCase() || 'GET'
  if (method !== 'GET' && !csrfCookie()) {
    await boundedRead(signal => fetch('/api/v1/admin/auth/csrf', { credentials: 'same-origin', signal }))
  }
  const headers = new Headers(init.headers)
  if (method !== 'GET') {
    if (init.body instanceof FormData) headers.delete('Content-Type')
    else headers.set('Content-Type', 'application/json')
    headers.set('X-CSRFToken', csrfCookie())
  }
  const execute = async (signal?: AbortSignal | null): Promise<T> => {
    let result: Response
    try {
      result = await fetch(`/api/v1/admin${path}`, { ...init, headers, credentials: 'same-origin', signal })
    } catch (error) {
      if (method === 'GET' && !(error instanceof Error && error.name === 'AbortError')) {
        throw new ApiError('网络读取失败，请检查网络后重新读取。', 0, 'READ_FAILED')
      }
      throw error
    }
    if (result.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('admin-session-expired'))
    let payload: ApiEnvelope<T>
    try {
      payload = (await result.json()) as ApiEnvelope<T>
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') throw error
      throw new Error(method === 'GET' ? '数据读取失败，请重新读取。' : '服务暂时不可用，请刷新页面后重试。')
    }
    if (!result.ok || !payload.success || payload.data === undefined) {
      throw new ApiError(payload.error?.message || '请求失败，请稍后重试。', result.status, payload.error?.code || 'UNKNOWN')
    }
    return payload.data
  }
  return method === 'GET' ? boundedRead(execute, init.signal) : execute(init.signal)
}

/** Compatibility wrapper for existing business confirmations; session and CSRF are enforced by api. */
export async function confirmedWrite<T>(
  _action: string,
  _obsoletePassword: string,
  path: string,
  method: 'POST' | 'PATCH' | 'PUT',
  body: Record<string, unknown>,
  _objectId = '',
  _revision = 0,
  extraHeaders: Record<string, string> = {},
): Promise<T> {

  return api<T>(path, {
    method,
    headers: extraHeaders,
    body: JSON.stringify(body),
  })
}
