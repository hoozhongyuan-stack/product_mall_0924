import { yuanToFen } from '../../../shared/money.mjs'
export const statuses = { REVIEW:'待平台审单', QUOTE:'待确认报价', PAYMENT:'待付款', PROOF:'待核款', PAID:'待发货', SHIPPED:'待收货', EXCEPTION:'收货异常', RECEIVED:'已收货', CANCEL_REVIEW:'取消／退款处理中', CANCELLED:'已取消' }
export const actionLabels = { review:'审单并确认运费', reject:'拒绝供货', 'confirm-payment':'确认实际到账', 'reject-proof':'退回付款凭证', ship:'填写物流并发货', 'resolve-exception':'补齐凭证并重新发货', 'close-cancel':'确认退款并关闭', 'verify-units':'复核冻结单位' }
export const recordLabels = {submit:'提交进货申请',review:'平台审单',reject:'拒绝供货','confirm-quote':'确认报价','payment-proof':'提交付款凭证','confirm-payment':'确认到账','reject-proof':'退回付款凭证',ship:'平台发货',exception:'反馈收货异常','resolve-exception':'处理收货异常',receive:'验收并入库',cancel:'申请取消','close-cancel':'完成退款并关闭','verify-units':'复核销售单位'}
export function availableActions(order) {
 const normal = ({REVIEW:['review','reject'],QUOTE:['reject'],PROOF:['confirm-payment','reject-proof'],PAID:['ship'],EXCEPTION:['resolve-exception'],CANCEL_REVIEW:['close-cancel']}[order.status] || [])
 const mayVerify = ['REVIEW','QUOTE','PAYMENT','PROOF','PAID','SHIPPED','EXCEPTION'].includes(order.status)
 return order.unitReviewRequired && mayVerify ? ['verify-units', ...normal] : normal
}
export function actionPermission(action) { return ['confirm-payment','close-cancel'].includes(action) ? 'stores.procurement.finance' : 'stores.procurement.manage' }
export function actionBody(action, form, revision) {
 const body = { expectedRevision:revision }
 if (!Number.isSafeInteger(revision) || revision < 0) return {error:'订单修订无效，请重新读取。'}
 if (action === 'review') {
  const freightFen = yuanToFen(form.freight)
  if (freightFen === null || freightFen < 0 || freightFen > 9900000000) return {error:'运费须为非负金额，最多两位小数且不超过 9900 万元。'}
  return {body:{...body,freightFen}}
 }
 if (action === 'ship') {
  if (!form.carrier?.trim() || !form.tracking?.trim() || form.carrier.length > 80 || form.tracking.length > 120) return {error:'请填写物流公司和单号，分别不超过 80 字和 120 字。'}
  return {body:{...body,carrier:form.carrier.trim(),tracking:form.tracking.trim()}}
 }
 if (['reject','reject-proof'].includes(action)) {
  if (!form.reason?.trim() || form.reason.length > 300) return {error:'请填写原因，不超过 300 字。'}
  return {body:{...body,reason:form.reason.trim()}}
 }
 const reference = action === 'confirm-payment' ? form.paymentReference : form.reference
 if (!reference?.trim() || reference.length > 120 || (form.note || '').length > 300) return {error:'请填写到账／处理凭证编号，不超过 120 字；备注最多 300 字。'}
 if (['close-cancel','resolve-exception','verify-units'].includes(action) && !form.note?.trim()) return {error:'请填写处理备注，说明复核、补齐或退款情况。'}
 if (action === 'close-cancel' && !form.refundConfirmed) return {error:'请确认已完成线下退款。'}
 if (!['confirm-payment','close-cancel','resolve-exception','verify-units'].includes(action)) return {error:'操作无效。'}
 return {body:{...body,[action === 'confirm-payment' ? 'paymentReference' : 'reference']:reference.trim(),note:(form.note || '').trim(),...(action === 'close-cancel' ? {refundConfirmed:true} : {})}}
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function pendingProcurement(storage,actor,target,value=undefined,now=Date.now()) {
 const key = `mall:procurement:${actor}:${target}`
 if (value === null) { storage.removeItem(key); return null }
 if (value !== undefined) { storage.setItem(key,JSON.stringify({operation:value,expiresAt:now+3600000})); return value }
 const raw = storage.getItem(key)
 if (!raw) return null
 try {
  const {operation:p,expiresAt} = JSON.parse(raw)
  if (expiresAt > now && uuid.test(p.key) && p.method === 'POST' && p.body?.requestKey === p.key && Number.isSafeInteger(p.body.expectedRevision)
    && p.path === `/stores/procurement/orders/${target}/${p.path.split('/').at(-1)}` && Object.keys(actionLabels).includes(p.path.split('/').at(-1)) && !('password' in p.body)) return p
 } catch { /* Invalid private snapshots are discarded without exposing their contents. */ }
 storage.removeItem(key); return null
}
export function clearProcurementSnapshots(storage) {
 try {
  for (let i = storage.length - 1; i >= 0; i--) { const key = storage.key(i); if (key?.startsWith('mall:procurement:')) storage.removeItem(key) }
  return true
 } catch { return false }
}
export function resultMatches(p,result) {
 return result?.id === p.path.split('/')[4] && Number.isSafeInteger(result.revision) && result.revision > p.body.expectedRevision && Object.hasOwn(statuses,result.status)
}
export function recoveryDecision(result,p) { return result?.status === 'COMPLETED' && resultMatches(p,result.result) ? 'COMPLETED' : result?.status === 'NOT_FOUND' ? 'RETRY' : 'UNKNOWN' }
export function proofUrl(proof) {
 return proof && uuid.test(proof.assetId) && proof.previewUrl === `/api/v1/admin/stores/procurement/proofs/${proof.assetId}/file` ? proof.previewUrl : ''
}
