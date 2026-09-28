import type { Account } from '../../api'
import { usePersistentOperation } from '../../shared/persistent-operation'
import { pendingExchange, recoveryDecision, resultMatches, definitiveRejection } from './form.mjs'

export function useExchangeOperation(account: () => Account, target: () => string,
  onSuccess: (result: unknown) => Promise<void> | void, dirty: () => boolean) {
  return usePersistentOperation({ account, target, onSuccess, dirty,
    readPermission: 'exchange.read', writePermission: 'exchange.manage', storage: pendingExchange,
    recoveryPath: key => `/exchange-operations/${key}`, recoveryDecision, resultMatches, definitiveRejection })
}
