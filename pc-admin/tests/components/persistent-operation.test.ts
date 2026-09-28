import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute } from 'vue-router'
import { api, ApiError, type Account } from '../../src/api'
import { useCouponOperation } from '../../src/views/coupons/operation'
import { useExchangeOperation } from '../../src/views/exchange/operation'

vi.mock('../../src/api', async importOriginal => ({ ...await importOriginal<object>(), api: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
const id = '11a27082-c544-4ed8-8b70-3c1070bf560b'
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); sessionStorage.clear(); vi.mocked(api).mockReset() })

async function setup(domain: 'coupon' | 'exchange') {
  const account = ref<Account>({ accountId: 'actor', loginName: 'a', displayName: 'A', kind: 'STAFF', enabled: true,
    revision: 1, groupIds: [], permissionCodes: [`${domain}.read`, `${domain}.manage`, `${domain}.publish`] })
  const dirty = ref(false), success = vi.fn()
  let operation!: ReturnType<typeof useCouponOperation>
  const Page = defineComponent({ setup() {
    const route = useRoute()
    operation = (domain === 'coupon' ? useCouponOperation : useExchangeOperation)(
      () => account.value, () => String(route.params.id), success, () => dirty.value)
    return () => h('p', 'operation')
  } })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:id', component: Page }] })
  await router.push(`/${id}`)
  await router.isReady()
  wrappers.push(mount(RouterView, { global: { plugins: [router] } }))
  await flushPromises()
  return { operation, account, success, dirty, router }
}

for (const domain of ['coupon', 'exchange'] as const) {
  const path = domain === 'coupon' ? '/coupon-campaigns' : '/exchange-offers'
  describe(`${domain} persistent operation`, () => {
    it('freezes the original body and key after an unknown network result', async () => {
      const { operation } = await setup(domain)
      vi.mocked(api).mockRejectedValue(new Error('offline'))
      const body = { nested: { quantity: 1 } }
      const writing = operation.write(path, 'POST', body)
      body.nested.quantity = 2
      await writing
      expect(operation.pending.value?.body).toEqual({ nested: { quantity: 1 } })
      expect(operation.verified.value).toBe(false)
      const key = operation.pending.value!.key
      vi.mocked(api).mockResolvedValueOnce({ status: 'NOT_FOUND' })
      await operation.recover()
      expect(operation.verified.value).toBe(true)
      await operation.write(path, 'POST', { changed: true })
      expect(operation.pending.value?.key).toBe(key)
      const last = vi.mocked(api).mock.calls.at(-1)![1]!
      expect(JSON.parse(last.body as string)).toEqual({ nested: { quantity: 1 } })
      expect(last.headers).toMatchObject({ 'Idempotency-Key': key })
    })

    it('requires current read and write permissions before persisting or sending', async () => {
      const { operation, account } = await setup(domain)
      account.value = { ...account.value, permissionCodes: [`${domain}.read`] }
      await flushPromises()
      await operation.write(path, 'POST', {})
      expect(api).not.toHaveBeenCalled()
      expect(operation.pending.value).toBeNull()
    })

    it('does not send if browser storage cannot preserve the request', async () => {
      const { operation } = await setup(domain)
      vi.stubGlobal('sessionStorage', { getItem: () => null, removeItem: () => {}, clear: () => {}, setItem: () => { throw new Error('full') } })
      await operation.write(path, 'POST', {})
      expect(api).not.toHaveBeenCalled()
      expect(operation.storageError.value).toContain('浏览器存储不可用')
    })

    it('clears definitively rejected writes and explains revision conflicts', async () => {
      const { operation } = await setup(domain)
      vi.mocked(api).mockRejectedValue(new ApiError('conflict', 409, 'REVISION_CONFLICT'))
      await operation.write(path, 'POST', {})
      expect(operation.pending.value).toBeNull()
      expect(operation.error.value).toContain('刷新最新修订')
      expect(operation.busy.value).toBe(false)
    })

    it('retains pending identity on a mismatched completed recovery', async () => {
      const { operation, success } = await setup(domain)
      vi.mocked(api).mockRejectedValueOnce(new Error('offline'))
      await operation.write(path, 'POST', {})
      const key = operation.pending.value!.key
      vi.mocked(api).mockResolvedValueOnce({ status: 'COMPLETED', result: { id: 'wrong' } })
      await operation.recover()
      expect(operation.pending.value?.key).toBe(key)
      expect(operation.verified.value).toBe(false)
      expect(success).not.toHaveBeenCalled()
    })

    it('keeps the request with a visible recovery error if local cleanup fails after rejection', async () => {
      const { operation } = await setup(domain)
      const realStorage = sessionStorage
      vi.stubGlobal('sessionStorage', { getItem: realStorage.getItem.bind(realStorage),
        setItem: realStorage.setItem.bind(realStorage), clear: realStorage.clear.bind(realStorage),
        removeItem: () => { throw new Error('storage unavailable') } })
      vi.mocked(api).mockRejectedValue(new ApiError('conflict', 409, 'REVISION_CONFLICT'))
      await expect(operation.write(path, 'POST', {})).resolves.toBeUndefined()
      expect(operation.pending.value).not.toBeNull()
      expect(operation.verified.value).toBe(false)
      expect(operation.storageError.value).toContain('清除')
      expect(operation.busy.value).toBe(false)
    })

    it('never sends after the actor changes during password confirmation', async () => {
      const { operation, account, success } = await setup(domain)
      let finish!: (value: unknown) => void
      vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      operation.password.value = 'temporary-test-value'
      const writing = operation.write(`${path}/${id}/publish`, 'POST', { expectedRevision: 1 }, `${domain}.publish`, `${domain}.publish`)
      account.value = { ...account.value, accountId: 'other' }
      await flushPromises()
      finish({ confirmationToken: 'test-only-token' })
      await writing
      expect(api).toHaveBeenCalledTimes(1)
      expect(operation.password.value).toBe('')
      expect(operation.pending.value).toBeNull()
      expect(success).not.toHaveBeenCalled()
    })

    const validBody = domain === 'coupon' ? { title: 'Campaign' } : { skuId: id, pointsPrice: 10 }
    const validResult = domain === 'coupon'
      ? { id, revision: 1, status: 'DRAFT', code: 'CAMPAIGN', title: 'Campaign', kind: 'CASH', claimMode: 'SELF',
          minGoodsFen: 0, discountFen: 1, totalQuantity: 10, selfClaimLimit: 1, issuedQuantity: 0, remainingQuantity: 10,
          redeemEligible: false, issuanceEnabled: false, productIds: [], productNames: [], validFrom: '2026-01-01', validUntil: '2026-12-31' }
      : { id, revision: 1, skuId: id, productId: id, productName: 'Product', skuCode: 'SKU', saleUnit: '件',
          fulfillmentKind: 'SHIP', status: 'DRAFT', pointsPrice: 10, specs: [], createdAt: '2026-01-01' }

    it('clears a verified successful write and calls its domain callback exactly once', async () => {
      const { operation, success } = await setup(domain)
      vi.mocked(api).mockResolvedValueOnce(validResult)
      await operation.write(path, 'POST', validBody)
      expect(success).toHaveBeenCalledExactlyOnceWith(validResult)
      expect(operation.pending.value).toBeNull()
      expect(sessionStorage.getItem(`mall:${domain}:actor:${id}`)).toBeNull()
      expect(operation.notice.value).toBe('操作已完成。')
      expect(operation.busy.value).toBe(false)
    })

    it('recovers a matching completed result without reissuing the write', async () => {
      const { operation, success } = await setup(domain)
      vi.mocked(api).mockRejectedValueOnce(new Error('disconnected'))
      await operation.write(path, 'POST', validBody)
      const key = operation.pending.value!.key
      vi.mocked(api).mockResolvedValueOnce({ status: 'COMPLETED', result: validResult })
      await operation.recover()
      expect(api).toHaveBeenLastCalledWith(`/${domain}-operations/${key}`)
      expect(success).toHaveBeenCalledExactlyOnceWith(validResult)
      expect(operation.pending.value).toBeNull()
      expect(operation.notice.value).toContain('上次操作的最终结果')
    })

    it('keeps the original request recoverable when recovery itself fails', async () => {
      const { operation, success } = await setup(domain)
      vi.mocked(api).mockRejectedValueOnce(new Error('offline'))
      await operation.write(path, 'POST', validBody)
      const key = operation.pending.value!.key
      vi.mocked(api).mockRejectedValueOnce(new ApiError('service down', 503, 'UNAVAILABLE'))
      await operation.recover()
      expect(operation.error.value).toBe('service down')
      expect(operation.pending.value?.key).toBe(key)
      expect(operation.verified.value).toBe(false)
      expect(operation.busy.value).toBe(false)
      expect(success).not.toHaveBeenCalled()
    })

    it('detects unreadable session storage on mount and blocks all writes', async () => {
      const storage = sessionStorage
      vi.stubGlobal('sessionStorage', { getItem: () => { throw new Error('storage disabled') },
        setItem: storage.setItem.bind(storage), removeItem: storage.removeItem.bind(storage), clear: storage.clear.bind(storage) })
      const { operation } = await setup(domain)
      expect(operation.storageError.value).toContain('无法保留操作身份')
      await operation.write(path, 'POST', validBody)
      expect(api).not.toHaveBeenCalled()
    })

    it('re-establishes writable storage before unlocking a rejected request for retry', async () => {
      const { operation } = await setup(domain)
      const storage = sessionStorage
      vi.stubGlobal('sessionStorage', { getItem: storage.getItem.bind(storage), setItem: storage.setItem.bind(storage),
        clear: storage.clear.bind(storage), removeItem: () => { throw new Error('storage disabled') } })
      vi.mocked(api).mockRejectedValueOnce(new ApiError('forbidden', 403, 'FORBIDDEN'))
      await operation.write(path, 'POST', validBody)
      const key = operation.pending.value!.key
      expect(operation.storageError.value).toContain('无法清除')
      vi.stubGlobal('sessionStorage', storage)
      vi.mocked(api).mockResolvedValueOnce({ status: 'NOT_FOUND' })
      await operation.recover()
      expect(operation.storageError.value).toBe('')
      expect(operation.verified.value).toBe(true)
      expect(operation.pending.value?.key).toBe(key)
      expect(JSON.parse(storage.getItem(`mall:${domain}:actor:${id}`)!).key).toBe(key)
    })

    it('does not submit a password-confirmed action without its password, then uses the confirmed token', async () => {
      const { operation, success } = await setup(domain)
      const writePath = `${path}/${id}/${domain === 'coupon' ? 'publish' : 'availability'}`
      const body = { expectedRevision: 1, status: 'ON_SALE' }
      await operation.write(writePath, 'POST', body, `${domain}.publish`, `${domain}.publish`)
      expect(api).not.toHaveBeenCalled()
      operation.password.value = 'ephemeral'
      const result = { ...validResult, revision: 2, status: domain === 'coupon' ? 'PUBLISHED' : 'ON_SALE' }
      vi.mocked(api).mockResolvedValueOnce({ confirmationToken: 'test-confirmation' }).mockResolvedValueOnce(result)
      await operation.write(writePath, 'POST', body, `${domain}.publish`, `${domain}.publish`)
      expect(vi.mocked(api).mock.calls[1]![1]!.headers).toMatchObject({ 'X-Action-Confirmation': 'test-confirmation' })
      expect(success).toHaveBeenCalledExactlyOnceWith(result)
      expect(operation.password.value).toBe('')
    })

    it('does not clear the stored identity on a rejected password confirmation before sending', async () => {
      const { operation } = await setup(domain)
      operation.password.value = 'wrong'
      vi.mocked(api).mockRejectedValueOnce(new ApiError('password rejected', 403, 'FORBIDDEN'))
      await operation.write(`${path}/${id}/${domain === 'coupon' ? 'publish' : 'availability'}`, 'POST', { expectedRevision: 1 }, `${domain}.publish`, `${domain}.publish`)
      expect(api).toHaveBeenCalledTimes(1)
      expect(operation.pending.value).not.toBeNull()
      expect(operation.error.value).toBe('password rejected')
      expect(operation.password.value).toBe('')
    })

    it('does not let an old recovery response replace state for a new actor', async () => {
      const { operation, account, success } = await setup(domain)
      vi.mocked(api).mockRejectedValueOnce(new Error('offline'))
      await operation.write(path, 'POST', validBody)
      let finish!: (value: unknown) => void
      vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      const recovering = operation.recover()
      await operation.recover()
      expect(api).toHaveBeenCalledTimes(2)
      account.value = { ...account.value, accountId: 'another' }
      finish({ status: 'COMPLETED', result: validResult })
      await recovering
      expect(operation.pending.value).toBeNull()
      expect(operation.notice.value).toBe('')
      expect(success).not.toHaveBeenCalled()
    })

    it('ignores a successful write arriving after unmount and retains its recoverable identity', async () => {
      const { operation, success } = await setup(domain)
      let finish!: (value: unknown) => void
      vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      const writing = operation.write(path, 'POST', validBody)
      const key = operation.pending.value!.key
      wrappers.pop()!.unmount()
      finish(validResult)
      await writing
      expect(success).not.toHaveBeenCalled()
      expect(JSON.parse(sessionStorage.getItem(`mall:${domain}:actor:${id}`)!).key).toBe(key)
    })

    it('prevents closing the browser with dirty state and removes its listener on unmount', async () => {
      const { dirty } = await setup(domain)
      dirty.value = true
      const before = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(before)
      expect(before.defaultPrevented).toBe(true)
      wrappers.pop()!.unmount()
      const after = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(after)
      expect(after.defaultPrevented).toBe(false)
    })
  })
}
