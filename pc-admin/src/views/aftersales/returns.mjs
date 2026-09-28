const count = value => Number.isSafeInteger(value) && value >= 0
export function acceptanceError(form, quantity, allowZero = false) {
  if (!['RECEIVED','WAIVED_RETURN'].includes(form.mode)) return '请选择验收方式。'
  if (!count(form.receivedQuantity) || form.receivedQuantity > quantity) return '收到数量超出申请范围。'
  if (!count(form.salableQuantity) || form.salableQuantity > form.receivedQuantity) return '可售数量不得超过收到数量。'
  if (!count(form.refundQuantity) || form.refundQuantity > quantity || (form.mode === 'RECEIVED' && form.refundQuantity > form.receivedQuantity)) return '退款数量不得超过本次收到或申请数量。'
  if (form.mode === 'WAIVED_RETURN' && (form.receivedQuantity || form.salableQuantity)) return '免寄回时收到和可售数量必须为零。'
  if (typeof form.reason !== 'string' || form.reason.trim().length < 5 || form.reason.trim().length > 500) return '请填写 5 至 500 字处理依据，向顾客说明。'
  if (form.amount && (!/^\d{1,10}(\.\d{1,2})?$/.test(form.amount) || (Number(form.amount) < 0 || (Number(form.amount) === 0 && !allowZero)))) return '退款金额须符合本笔现金范围，最多两位小数；留空由服务端计算。'
  return ''
}
export function acceptanceBody(form, revision) {
  const [whole, decimal = ''] = (form.amount || '0').split('.')
  return { expectedRevision: revision, mode: form.mode, receivedQuantity:form.receivedQuantity, salableQuantity:form.salableQuantity,
    refundQuantity:form.refundQuantity, refundAmountFen:form.amount ? Number(whole)*100+Number(decimal.padEnd(2,'0')) : null, reason:form.reason.trim() }
}
export function wechatRefundLabel(status) {
  return ({ PREPARED:'尚未发起', UNKNOWN:'结果未知，请先查单', PROCESSING:'退款处理中，请查单', FAILED:'退款失败，核查后沿原退款单处理', SUCCEEDED:'退款已成功' })[status] || '尚无退款记录'
}
export function pendingAcceptance(storage, accountId, caseId, value = undefined) {
 const key = `mall:return-acceptance:${accountId}:${caseId}`
 if(value === null) { storage.removeItem(key); return null }
 if(value !== undefined) { storage.setItem(key,JSON.stringify(value)); return value }
 const raw=storage.getItem(key)
 if(!raw)return null
 try { const parsed=JSON.parse(raw);return parsed && typeof parsed.key==='string' && parsed.body && typeof parsed.body==='object'?parsed:null }
 catch { storage.removeItem(key);return null }
}
export function wechatFailureLabel(code) {
 return ({ WECHAT_REFUND_EVENT_CONFLICT:'退款通知身份冲突，自动操作已停止，需人工核查资金凭证和渠道流水。',
  WECHAT_REFUND_CLOSED:'微信退款已关闭，不能自动重新发起；请核查渠道记录。',
  WECHAT_REFUND_NOT_CONFIGURED:'微信退款配置尚未完成。', WECHAT_NOT_CONFIGURED:'微信商户配置尚未完成。' })[code] || ''
}
