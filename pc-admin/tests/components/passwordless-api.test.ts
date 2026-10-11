import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmedWrite } from '../../src/api'

afterEach(() => { vi.unstubAllGlobals(); document.cookie = 'csrftoken=; Max-Age=0; path=/' })
describe('business writes after session login', () => {
  it('sends one CSRF-protected business request and preserves its idempotency key without confirmation', async () => {
    document.cookie = 'csrftoken=session-csrf; path=/'
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ success: true, data: { revision: 3 } }) })
    vi.stubGlobal('fetch', fetchMock)
    const result = await confirmedWrite('page.publish', '', '/pages/home/publish', 'POST', { expectedRevision: 2 }, 'home', 2, { 'Idempotency-Key': 'original-request' })
    expect(result).toEqual({ revision: 3 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, options] = fetchMock.mock.calls[0]!
    expect(path).toBe('/api/v1/admin/pages/home/publish')
    expect(options.credentials).toBe('same-origin')
    expect(options.headers.get('X-CSRFToken')).toBe('session-csrf')
    expect(options.headers.get('Idempotency-Key')).toBe('original-request')
    expect(options.headers.has('X-Action-Confirmation')).toBe(false)
    expect(JSON.parse(options.body)).toEqual({ expectedRevision: 2 })
  })
})
