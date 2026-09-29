import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../src/api'
import { createAssetUploadQueue } from '../../src/shared/asset-upload-queue'

const file = (name = 'image.png', type = 'image/png') => new File(['image'], name, { type, lastModified: 1 })
const asset = { assetId: 'saved', kind: 'IMAGE' as const, contentType: 'image/png', byteSize: 5, adminUrl: '/file' }
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

describe('asset upload queue', () => {
  it('validates every file, bounds a batch, and never sends rejected files', async () => {
    const upload = vi.fn().mockResolvedValue(asset)
    const queue = createAssetUploadQueue(upload, () => true)
    expect(queue.add([file(), file('bad.mp4', 'video/mp4')], 'IMAGE')).toBe('')
    expect(queue.rows.value.map(row => row.status)).toEqual(['WAITING', 'INVALID'])
    expect(queue.add(Array.from({ length: 19 }, (_, i) => file(`${i}.png`)), 'IMAGE')).toContain('20')
    expect(queue.rows.value).toHaveLength(2)
    await queue.start()
    expect(upload).toHaveBeenCalledTimes(1)
    expect(queue.rows.value.map(row => row.status)).toEqual(['SUCCEEDED', 'INVALID'])
    expect(queue.completed.value).toBe(1)
  })

  it('uploads serially, freezes per-file type, and allows removal before dispatch', async () => {
    const first = deferred<typeof asset>()
    const upload = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(asset)
    const queue = createAssetUploadQueue(upload, () => true)
    queue.add([file('one.png'), file('two.png')], 'IMAGE')
    queue.add([file('film.mp4', 'video/mp4')], 'VIDEO')
    const running = queue.start()
    expect(upload).toHaveBeenCalledTimes(1)
    queue.remove(queue.rows.value[0]!.id)
    expect(queue.rows.value).toHaveLength(3)
    queue.remove(queue.rows.value[1]!.id)
    first.resolve(asset)
    await running
    expect(upload).toHaveBeenCalledTimes(2)
    expect(upload.mock.calls[1]![1]).toBe('VIDEO')
    expect(queue.rows.value.every(row => row.status === 'SUCCEEDED')).toBe(true)
  })

  it('continues after a definite rejection and retries only that rejected file', async () => {
    const upload = vi.fn().mockRejectedValueOnce(new ApiError('文件无效', 400, 'INVALID_FILE')).mockResolvedValue(asset)
    const queue = createAssetUploadQueue(upload, () => true)
    queue.add([file('one.png'), file('two.png')], 'IMAGE')
    await queue.start()
    const rejected = queue.rows.value[0]!
    expect(queue.rows.value.map(row => row.status)).toEqual(['FAILED', 'SUCCEEDED'])
    queue.retry(queue.rows.value[1]!.id)
    queue.retry(rejected.id)
    await queue.start()
    expect(upload).toHaveBeenCalledTimes(3)
    expect(queue.completed.value).toBe(2)
  })

  it.each([new TypeError('network'), new ApiError('服务异常', 500, 'SERVER_ERROR'), new ApiError('超时', 408, 'TIMEOUT')])(
    'does not automatically resend an uncertain upload or dispatch the rest', async reason => {
      const upload = vi.fn().mockRejectedValue(reason)
      const queue = createAssetUploadQueue(upload, () => true)
      queue.add([file('one.png'), file('two.png')], 'IMAGE')
      await queue.start()
      expect(queue.rows.value.map(row => row.status)).toEqual(['UNKNOWN', 'WAITING'])
      expect(queue.rows.value[0]!.message).toContain('核对')
      queue.retry(queue.rows.value[0]!.id)
      expect(queue.rows.value[0]!.status).toBe('UNKNOWN')
      expect(upload).toHaveBeenCalledTimes(1)
      expect(queue.running.value).toBe(false)
    },
  )

  it('pauses on authorization loss, then refuses new dispatch without authorization', async () => {
    let authorized = true
    const upload = vi.fn().mockRejectedValueOnce(new ApiError('权限已变化', 403, 'FORBIDDEN'))
    const queue = createAssetUploadQueue(upload, () => authorized)
    queue.add([file('one.png'), file('two.png')], 'IMAGE')
    await queue.start()
    expect(upload).toHaveBeenCalledTimes(1)
    authorized = false
    await queue.start()
    expect(queue.add([file('three.png')], 'IMAGE')).toContain('权限')
    expect(upload).toHaveBeenCalledTimes(1)
  })

  it('stops after the in-flight result without pretending that it was rolled back', async () => {
    const request = deferred<typeof asset>()
    const upload = vi.fn().mockReturnValue(request.promise)
    const queue = createAssetUploadQueue(upload, () => true)
    queue.add([file('one.png'), file('two.png')], 'IMAGE')
    const running = queue.start()
    queue.pause()
    request.resolve(asset)
    await running
    expect(queue.rows.value.map(row => row.status)).toEqual(['SUCCEEDED', 'WAITING'])
    expect(queue.hasPending.value).toBe(true)
    queue.remove(queue.rows.value[1]!.id)
    expect(queue.hasPending.value).toBe(false)
    queue.clearFinished()
    expect(queue.rows.value).toEqual([])
  })

  it('discards stale responses when an account changes or a picker unmounts', async () => {
    const request = deferred<typeof asset>()
    const queue = createAssetUploadQueue(vi.fn().mockReturnValue(request.promise), () => true)
    queue.add([file()], 'IMAGE')
    const running = queue.start()
    queue.invalidate()
    request.resolve(asset)
    await running
    expect(queue.rows.value).toEqual([])
    expect(queue.running.value).toBe(false)
    expect(queue.completed.value).toBe(0)
  })
})
