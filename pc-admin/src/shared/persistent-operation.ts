import { onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { api, ApiError, type Account, type Confirmation } from '../api'

export interface PendingOperation {
  key: string
  path: string
  method: 'POST' | 'PUT'
  body: Record<string, unknown>
  action: string
}

interface OperationOptions {
  account: () => Account
  target: () => string
  onSuccess: (result: unknown) => Promise<void> | void
  dirty: () => boolean
  readPermission: string
  writePermission: string
  storage: (storage: Storage, actor: string, target: string, value?: PendingOperation | null) => PendingOperation | null
  recoveryPath: (key: string) => string
  recoveryDecision: (result: unknown, pending: PendingOperation) => 'COMPLETED' | 'RETRY' | 'UNKNOWN'
  resultMatches: (pending: PendingOperation, result: unknown) => boolean
  definitiveRejection: (status: number) => boolean
}

/** Shared transport/lifecycle only. Domain validation and authorization policy are injected. */
export function usePersistentOperation(options: OperationOptions) {
  const pending = ref<PendingOperation | null>(null)
  const busy = ref(false), error = ref(''), notice = ref(''), password = ref('')
  const storageError = ref(''), verified = ref(false)
  let epoch = 0

  function stored(value?: PendingOperation | null) {
    return options.storage(sessionStorage, options.account().accountId, options.target(), value)
  }
  function restore() {
    password.value = ''; pending.value = null; verified.value = false; storageError.value = ''
    try { pending.value = stored() }
    catch { storageError.value = '浏览器存储不可用，无法保留操作身份，请恢复存储后操作。' }
  }
  function clear() {
    stored(null)
    pending.value = null
    verified.value = false
    storageError.value = ''
  }
  async function recover() {
    if (!pending.value || busy.value) return
    const seq = epoch
    busy.value = true; error.value = ''; verified.value = false
    try {
      const result = await api<{ status: 'COMPLETED' | 'NOT_FOUND'; result?: unknown }>(options.recoveryPath(pending.value.key))
      if (seq !== epoch) return
      const decision = options.recoveryDecision(result, pending.value!)
      if (decision === 'COMPLETED') {
        clear(); notice.value = '已读取上次操作的最终结果。'; busy.value = false
        await options.onSuccess(result.result)
      } else if (decision === 'RETRY') {
        stored(pending.value!)
        storageError.value = ''
        verified.value = true; notice.value = '当前没有已完成记录，可使用原请求重试。'
      } else { error.value = '服务端返回结果无法确认，原请求保持锁定，请重新读取结果。' }
    } catch (reason) {
      if (seq === epoch) error.value = reason instanceof Error ? reason.message : '无法读取操作结果，请重试。'
    } finally { if (seq === epoch) busy.value = false }
  }

  function writeFailure(reason: unknown, attempted: boolean) {
    if (reason instanceof ApiError && options.definitiveRejection(reason.status)) {
      if (attempted) {
        try { clear() }
        catch {
          verified.value = false
          storageError.value = '浏览器存储无法清除原请求，请恢复存储后重新读取操作结果。'
        }
      }
      error.value = reason.message + (reason.status === 409 ? ' 请刷新最新修订后核对。' : '')
    } else if (!pending.value && !attempted) {
      storageError.value = '浏览器存储不可用，操作尚未发送，请恢复存储后重试。'
    } else {
      verified.value = false
      error.value = `${reason instanceof Error ? reason.message : '操作结果未知。'} 原请求已保留，先读取操作结果再重试。`
    }
  }

  async function write(path: string, method: 'POST' | 'PUT', body: Record<string, unknown>, action = '', permission = options.writePermission) {
    const account = options.account()
    if (busy.value || storageError.value || !account.permissionCodes.includes(options.readPermission)
      || !account.permissionCodes.includes(permission) || pending.value && !verified.value || action && !password.value) return
    const seq = epoch, actor = account.accountId
    busy.value = true; error.value = ''
    let attempted = false
    try {
      if (!pending.value) {
        // Freeze the JSON sent over the wire, not the caller's mutable form object.
        const operation = { key: crypto.randomUUID(), path, method, body: JSON.parse(JSON.stringify(body)), action }
        stored(operation)
        pending.value = operation
      }
      const operation = pending.value!
      let token = ''
      if (operation.action) {
        const confirmation = await api<Confirmation>('/auth/confirm', { method: 'POST', body: JSON.stringify({
          action: operation.action, password: password.value, objectId: options.target().split(':')[0], revision: operation.body.expectedRevision,
        }) })
        token = confirmation.confirmationToken
      }
      if (seq !== epoch || actor !== options.account().accountId) return
      attempted = true
      const result = await api<unknown>(operation.path, { method: operation.method, body: JSON.stringify(operation.body),
        headers: { 'Idempotency-Key': operation.key, ...(token ? { 'X-Action-Confirmation': token } : {}) } })
      if (seq !== epoch) return
      if (!options.resultMatches(operation, result)) throw new Error('服务端返回结果无法确认。')
      clear(); notice.value = '操作已完成。'; busy.value = false
      await options.onSuccess(result)
    } catch (reason) {
      if (seq === epoch) writeFailure(reason, attempted)
    } finally { if (seq === epoch) { password.value = ''; busy.value = false } }
  }

  function beforeUnload(event: BeforeUnloadEvent) {
    if (options.dirty() || busy.value || pending.value) { event.preventDefault(); event.returnValue = '' }
  }
  const mayLeave = () => !busy.value && (!(options.dirty() || pending.value)
    || window.confirm(pending.value ? '操作结果待确认，原请求已保留。确定离开？' : '有未保存修改，确定离开？'))
  onMounted(() => { restore(); window.addEventListener('beforeunload', beforeUnload) })
  onUnmounted(() => { epoch++; password.value = ''; window.removeEventListener('beforeunload', beforeUnload) })
  watch(() => JSON.stringify([options.account().accountId, options.account().permissionCodes, options.target()]), () => {
    epoch++; busy.value = false; error.value = ''; notice.value = ''; restore()
  }, { flush: 'sync' })
  onBeforeRouteLeave(mayLeave)
  onBeforeRouteUpdate(mayLeave)
  return { pending, busy, error, notice, password, storageError, verified, recover, write }
}
