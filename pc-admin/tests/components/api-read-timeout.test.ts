import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../src/api'

const response = (data: unknown = { ready: true }) => ({ ok: true, status: 200, json: async () => ({ success: true, data }) })
function untilAborted(signal: AbortSignal) {
  return new Promise<never>((_resolve, reject) => {
    if (signal.aborted) reject(new DOMException('Aborted', 'AbortError'))
    else signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  })
}
beforeEach(() => { vi.useFakeTimers(); document.cookie = 'csrftoken=; Max-Age=0; path=/' })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); document.cookie = 'csrftoken=; Max-Age=0; path=/' })

describe('bounded administrative reads', () => {
  it('aborts a stalled read after 15 seconds with a safe retry message', async () => {
    const fetchMock = vi.fn((_path, init) => untilAborted(init.signal))
    vi.stubGlobal('fetch', fetchMock)
    const error = api('/categories').catch(reason => reason)
    await vi.advanceTimersByTimeAsync(15_000)
    expect(await error).toMatchObject({ status: 408, code: 'READ_TIMEOUT' })
    expect((await error).message).toContain('重新读取')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]![1].signal.aborted).toBe(true)
  })
  it('keeps the deadline until the response body is consumed', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_path, init) => ({ ...response(), json: () => untilAborted(init.signal) })))
    const error = api('/categories').catch(reason => reason)
    await vi.advanceTimersByTimeAsync(15_000)
    expect(await error).toBeInstanceOf(ApiError)
    expect(await error).toMatchObject({ code: 'READ_TIMEOUT' })
  })
  it('cleans the deadline after a successful read and preserves headers and credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetchMock)
    expect(await api('/categories', { headers: { 'X-Read-Test': 'yes' } })).toEqual({ ready: true })
    expect(vi.getTimerCount()).toBe(0)
    expect(fetchMock.mock.calls[0]![1].credentials).toBe('same-origin')
    expect(fetchMock.mock.calls[0]![1].headers.get('X-Read-Test')).toBe('yes')
  })
  it('preserves caller cancellation without claiming a timeout', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn((_path, init) => untilAborted(init.signal)))
    const error = api('/categories', { signal: controller.signal }).catch(reason => reason)
    controller.abort()
    expect(await error).toMatchObject({ name: 'AbortError' })
    expect(vi.getTimerCount()).toBe(0)
  })
  it('honors a signal canceled before the read begins', async () => {
    const controller = new AbortController()
    controller.abort()
    vi.stubGlobal('fetch', vi.fn((_path, init) => untilAborted(init.signal)))
    const error = await api('/categories', { signal: controller.signal }).catch(reason => reason)
    expect(error.name).toBe('AbortError')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('never aborts or automatically retries an in-flight business write', async () => {
    document.cookie = 'csrftoken=session-csrf; path=/'
    let resolve!: (value: ReturnType<typeof response>) => void
    const fetchMock = vi.fn(() => new Promise<ReturnType<typeof response>>(done => { resolve = done }))
    vi.stubGlobal('fetch', fetchMock)
    const request = api('/orders/one/ship', { method: 'POST', body: '{}' })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBeUndefined()
    resolve(response({ revision: 2 }))
    expect(await request).toEqual({ revision: 2 })
  })
  it('bounds CSRF bootstrap and never starts a write when bootstrap times out', async () => {
    const fetchMock = vi.fn((_path, init) => untilAborted(init.signal))
    vi.stubGlobal('fetch', fetchMock)
    const error = api('/orders/one/ship', { method: 'POST', body: '{}' }).catch(reason => reason)
    await vi.advanceTimersByTimeAsync(15_000)
    expect(await error).toMatchObject({ code: 'READ_TIMEOUT' })
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual(['/api/v1/admin/auth/csrf'])
  })
  it('expires the session even when a 401 response body cannot be read', async () => {
    const listener = vi.fn()
    window.addEventListener('admin-session-expired', listener)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => { throw new Error('Malformed') } }))
    await expect(api('/me')).rejects.toThrow('重新读取')
    expect(listener).toHaveBeenCalledTimes(1)
    window.removeEventListener('admin-session-expired', listener)
  })
  it('preserves structured server rejection and removes the deadline', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ success: false, error: { code: 'FORBIDDEN', message: '没有读取权限。' } }) }))
    await expect(api('/categories')).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN' })
    expect(vi.getTimerCount()).toBe(0)
  })
  it('offers a readable retry message on offline reads without retrying automatically', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api('/categories')).rejects.toMatchObject({ status: 0, code: 'READ_FAILED' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('preserves caller cancellation while reading the body', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn(async (_path, init) => ({ ...response(), json: () => untilAborted(init.signal) })))
    const error = api('/categories', { signal: controller.signal }).catch(reason => reason)
    await vi.advanceTimersByTimeAsync(1)
    controller.abort()
    expect(await error).toMatchObject({ name: 'AbortError' })
    expect(vi.getTimerCount()).toBe(0)
  })
  it('keeps write network failures and malformed bodies on the existing recovery path', async () => {
    document.cookie = 'csrftoken=session-csrf; path=/'
    const failure = new TypeError('Failed to fetch')
    const fetchMock = vi.fn().mockRejectedValueOnce(failure).mockResolvedValueOnce({ ...response(), json: async () => { throw new Error('Malformed') } })
    vi.stubGlobal('fetch', fetchMock)
    await expect(api('/orders/one/ship', { method: 'POST', body: '{}' })).rejects.toBe(failure)
    await expect(api('/orders/one/ship', { method: 'POST', body: '{}' })).rejects.toThrow('服务暂时不可用')
  })
  it('bootstraps CSRF and accepts upload FormData without forcing a JSON content type', async () => {
    const fetchMock = vi.fn().mockImplementationOnce(async () => { document.cookie = 'csrftoken=upload-csrf; path=/'; return response() }).mockResolvedValueOnce(response({ assetId: 'asset' }))
    vi.stubGlobal('fetch', fetchMock)
    const body = new FormData()
    body.set('file', new File(['test'], 'test.png'))
    expect(await api('/assets', { method: 'POST', body, headers: { 'Content-Type': 'application/json' } })).toEqual({ assetId: 'asset' })
    expect(fetchMock.mock.calls[1]![1].headers.has('Content-Type')).toBe(false)
    expect(fetchMock.mock.calls[1]![1].headers.get('X-CSRFToken')).toBe('upload-csrf')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('leaves login rejection local instead of expiring a session and supplies UNKNOWN fallback', async () => {
    document.cookie = 'csrftoken=session-csrf; path=/'
    const listener = vi.fn()
    window.addEventListener('admin-session-expired', listener)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ success: false }) }))
    await expect(api('/auth/login', { method: 'POST', body: '{}' })).rejects.toMatchObject({ status: 401, code: 'UNKNOWN' })
    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener('admin-session-expired', listener)
  })
})
