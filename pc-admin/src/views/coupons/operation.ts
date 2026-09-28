import type { Account } from '../../api'
import { usePersistentOperation } from '../../shared/persistent-operation'
import { pendingCoupon } from './form.mjs'
import { recoveryDecision, resultMatches, definitiveRejection } from './recovery.mjs'

export function useCouponOperation(account: () => Account, target: () => string,
  onSuccess: (result: unknown) => Promise<void> | void, dirty: () => boolean) {
  return usePersistentOperation({ account, target, onSuccess, dirty,
    readPermission: 'coupon.read', writePermission: 'coupon.manage', storage: pendingCoupon,
    recoveryPath: key => `/coupon-operations/${key}`, recoveryDecision, resultMatches, definitiveRejection })
}
